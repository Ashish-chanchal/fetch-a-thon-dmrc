"""
metro_bap_coordinator:
- User-facing Buyer App (BAP) orchestrator and the seller in the Agent Payment Protocol
- Includes `ONDC_TRV11_LiveBAPProtocol`, `Protocol(spec=chat_protocol_spec)`, and `Protocol(spec=payment_protocol_spec, role="seller")`
- Requests FET from the rider (fet_direct, Dorado testnet), verifies the transfer on chain, then issues the ticket
- Renders journey, payment and ticket custom cards in ASI:One Chat
- Exposes `POST /metro/book` REST endpoint for web/mobile apps and natural-language booking over ASI:One Chat
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Dict, Optional, Tuple
import uuid

from uagents import Agent, Context, Protocol
from uagents.setup import fund_agent_if_low
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    MetadataContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.chat.cards import extract_card_response
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents import chat_cards
from agents.catalog_agent import catalog_agent
from agents.ticketing_agent import ticketing_agent
from common.asi1_client import ASIOneClient, execute_ondc_fare_query
from common.config import (
    AGENT_HANDLE,
    AGENT_NAME,
    AGENT_PUBLIC_ENDPOINT,
    COORDINATOR_SEED,
    ENABLE_MAILBOX,
    FETCHAI_DENOM,
    FETCHAI_NETWORK,
    INR_TO_TESTNET_FET,
    ONDC_CITY_CODE,
    PROJECT_ROOT,
)
from common.models import (
    CatalogQuoteResponse,
    CatalogSearchRequest,
    MetroBookRESTRequest,
    MetroBookRESTResponse,
    MetroChatRESTRequest,
    MetroChatRESTResponse,
    MetroTicketVerifyRESTRequest,
    MetroTicketVerifyRESTResponse,
    TicketIssuedResponse,
    TicketingInitRequest,
)
from common.fet_payment import explorer_tx_url, fet_to_base_units, find_incoming_payment, verify_fet_payment
from common.ticket_verifier import verify_and_scan_dmrc_ticket

coordinator_endpoint = (
    [f"{AGENT_PUBLIC_ENDPOINT.rstrip('/')}/submit"]
    if AGENT_PUBLIC_ENDPOINT
    else ["http://localhost:8000/submit"]
)

if ENABLE_MAILBOX:
    # Mailbox agent: connects out to Agentverse, so no public endpoint or tunnel is needed.
    # The display name, description, handle and README are published to the agent's Agentverse page.
    coordinator = Agent(
        name=AGENT_NAME,
        handle=AGENT_HANDLE,
        seed=COORDINATOR_SEED,
        port=8000,
        network=FETCHAI_NETWORK,
        mailbox=True,
        publish_agent_details=True,
        description="Plan Delhi Metro journeys, pay in FET and get a signed QR ticket in chat.",
        readme_path=str(PROJECT_ROOT / "agent_readme.md"),
    )
else:
    coordinator = Agent(
        name=AGENT_NAME,
        handle=AGENT_HANDLE,
        seed=COORDINATOR_SEED,
        port=8000,
        endpoint=coordinator_endpoint,
        network=FETCHAI_NETWORK,
    )


ONDC_TRV11_LiveBAPProtocol = Protocol(
    name="ONDC_TRV11_LiveBAPProtocol", version="2.1.0"
)
chat_protocol = Protocol(spec=chat_protocol_spec)
# The coordinator is the SELLER: riders pay it, and it pays nobody.
seller_payment_protocol = Protocol(spec=payment_protocol_spec, role="seller")

asi1_client = ASIOneClient()

# Active booking sessions indexed by transaction_id
BOOKING_SESSIONS: Dict[str, Dict[str, Any]] = {}
BOOKING_EVENTS: Dict[str, asyncio.Event] = {}

# Multi-turn conversational memory indexed by chat sender
CHAT_CONVERSATION_HISTORY: Dict[str, list[Dict[str, str]]] = {}
# Most recent quoted journey per sender
LAST_QUOTED_ROUTES: Dict[str, Dict[str, Any]] = {}
# Rider -> booking awaiting payment, and payment hashes already redeemed
PENDING_PAYMENTS: Dict[str, str] = {}
# Verified payment tx hash -> booking id (idempotency: one payment, one ticket)
PROCESSED_PAYMENTS: Dict[str, str] = {}
# Ticket order id -> chat sender who owns it
TICKET_OWNERS: Dict[str, str] = {}


def _parse_natural_language_booking(text: str) -> Tuple[str, str, int]:
    """
    Parses natural-language ASI:One chat prompts such as:
    'Book 2 metro tickets from Kashmere Gate to Rajiv Chowk'
    """
    passengers = 1
    pax_match = re.search(r"\b(\d+)\s*(?:tickets?|passengers?|pax)\b", text, re.IGNORECASE)
    if pax_match:
        passengers = max(1, int(pax_match.group(1)))

    route_match = re.search(
        r"from\s+([A-Za-z0-9_\-\s]+?)\s+to\s+([A-Za-z0-9_\-\s]+?)(?:\s+for\s+\d+|\s*$|[.,?!])",
        text,
        re.IGNORECASE,
    )
    if route_match:
        origin = route_match.group(1).strip()
        destination = route_match.group(2).strip()
        return origin, destination, passengers

    return "KASHMERE_GATE", "RAJIV_CHOWK", passengers


@coordinator.on_event("startup")
async def on_coordinator_startup(ctx: Context) -> None:
    ctx.logger.info(
        f"[metro_bap_coordinator] Address: {coordinator.address} | "
        f"Wallet: {coordinator.wallet.address()}"
    )
    try:
        fund_agent_if_low(str(coordinator.wallet.address()))
    except Exception as exc:
        ctx.logger.warning(f"Wallet funding check skipped or deferred: {exc}")


# ============================================================================
# Rider payments (Agent Payment Protocol, coordinator = seller) and booking pipeline
# ============================================================================

PAYMENT_DEADLINE_SECONDS = 300


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _fet_amount(value: Any) -> str:
    """'0.6400' -> '0.64' (what the rider sees and approves)."""
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _network_label() -> str:
    return "Fetch.ai mainnet" if FETCHAI_NETWORK == "mainnet" else "Fetch.ai testnet"


def _pay_card(quote: Dict[str, Any]) -> MetadataContent:
    return chat_cards.payment_card(quote, _network_label(), str(coordinator.wallet.address()), _fet_amount(quote.get("total_fare_fet")))


def _with_passengers(quote: Dict[str, Any], pax: int) -> Dict[str, Any]:
    """Re-prices a quote for a different passenger count without another API call."""
    q = dict(quote)
    pax = max(1, min(int(pax or 1), 6))
    unit = float(q.get("official_dmrc_fare_inr") or float(q.get("total_fare_inr", 0)) / max(int(q.get("passengers", 1) or 1), 1))
    total_inr = round(unit * pax, 2)
    q.update(
        passengers=pax,
        official_dmrc_fare_inr=f"{unit:.2f}",
        total_fare_inr=f"{total_inr:.2f}",
        total_fare_fet=f"{round(total_inr * INR_TO_TESTNET_FET, 4):.4f}",
    )
    return q


def _remember(sender: str, role: str, content: str) -> None:
    hist = CHAT_CONVERSATION_HISTORY.setdefault(sender, [])
    hist.append({"role": role, "content": content})
    if len(hist) > 10:
        CHAT_CONVERSATION_HISTORY[sender] = hist[-10:]


async def _reply(ctx: Context, to: str, text: str, card: Optional[MetadataContent] = None) -> None:
    content: list = [TextContent(type="text", text=text)]
    if card is not None:
        content.append(card)
    await ctx.send(to, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid.uuid4(), content=content))


async def _quote(origin: str, destination: str, passengers: int) -> Dict[str, Any]:
    return await execute_ondc_fare_query(origin, destination, max(1, int(passengers or 1)))


def _same_route(a: Dict[str, Any], origin: str, destination: str) -> bool:
    def norm(x: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(x or "").lower())
    return norm(a.get("origin_station")) == norm(origin) and norm(a.get("destination_station")) == norm(destination)


def _pending_booking(sender: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    tx_id = PENDING_PAYMENTS.get(sender)
    session = BOOKING_SESSIONS.get(tx_id or "")
    if not session or not session.get("awaiting_payment"):
        return None
    if _now() - session.get("requested_at", 0) > PAYMENT_DEADLINE_SECONDS:
        session["awaiting_payment"] = False
        PENDING_PAYMENTS.pop(sender, None)
        return None
    return tx_id, session


async def _send_payment_request(ctx: Context, sender: str, session: Dict[str, Any]) -> None:
    """Same shape as Fetch.ai's FET payment template, sent from the rider's chat turn."""
    quote = session["quote_preview"]
    amount = _fet_amount(quote.get("total_fare_fet"))
    route = f"{quote.get('origin_station')} → {quote.get('destination_station')}"
    pax = int(quote.get("passengers", 1) or 1)
    session["payment_reference"] = str(ctx.session)
    session["requested_at"] = _now()
    req = RequestPayment(
        accepted_funds=[Funds(currency="FET", amount=amount, payment_method="fet_direct")],
        recipient=str(coordinator.wallet.address()),
        deadline_seconds=PAYMENT_DEADLINE_SECONDS,
        reference=str(ctx.session),
        description=f"Delhi Metro ticket: {route} ({pax} passenger{'s' if pax > 1 else ''})",
        metadata={
            "agent": coordinator.name,
            "service": "delhi_metro_ticket",
            "content": f"Please approve the {amount} FET payment to book {route}. Your QR ticket is issued right after the transfer is verified on chain.",
            "fet_network": "mainnet" if FETCHAI_NETWORK == "mainnet" else "stable-testnet",
            "mainnet": "true" if FETCHAI_NETWORK == "mainnet" else "false",
            "provider_agent_wallet": str(coordinator.wallet.address()),
        },
    )
    ctx.logger.info(f"[outbound] RequestPayment -> {sender} amount={amount} FET ref={req.reference} booking={session.get('tx_id')}")
    await ctx.send(sender, req)


async def _request_rider_payment(ctx: Context, sender: str, quote: Dict[str, Any]) -> None:
    """Shows the confirm-and-pay card and sends one payment request per booking (re-sent, never duplicated)."""
    pending = _pending_booking(sender)
    if pending:
        tx_id, session = pending
        prev = session["quote_preview"]
        if _same_route(prev, quote.get("origin_station"), quote.get("destination_station")) and int(prev.get("passengers", 1)) == int(quote.get("passengers", 1)):
            await _reply(ctx, sender, "Your payment request for this journey is still open. Approve it to get your ticket.", _pay_card(prev))
            await _send_payment_request(ctx, sender, session)
            return
        session["awaiting_payment"] = False  # replaced by the new journey

    tx_id = str(uuid.uuid4())
    session = {
        "tx_id": tx_id,
        "chat_sender": sender,
        "quote_preview": quote,
        "awaiting_payment": True,
        "created_at": _now(),
        "city_code": ONDC_CITY_CODE,
        "user_prompt": f"Book {quote.get('origin_station')} to {quote.get('destination_station')}",
    }
    BOOKING_SESSIONS[tx_id] = session
    PENDING_PAYMENTS[sender] = tx_id
    LAST_QUOTED_ROUTES[sender] = quote

    amount = _fet_amount(quote.get("total_fare_fet"))
    text = (
        f"{chat_cards.journey_summary_text(quote)}\n\n"
        f"To book, pay **{amount} FET** ({_network_label()}): tap Approve on the payment request, "
        f"or send it to `{coordinator.wallet.address()}` from any Fetch wallet and tap **I've paid**."
    )
    _remember(sender, "assistant", f"Asked the rider to pay {amount} FET for {quote.get('origin_station')} to {quote.get('destination_station')}.")
    await _reply(ctx, sender, text, _pay_card(quote))
    await _send_payment_request(ctx, sender, session)


async def _settle_rider_payment(
    ctx: Context, sender: str, tx_hash: str, payer: str, via_protocol: bool
) -> None:
    """Verifies the rider's FET transfer on chain, completes the payment and starts the booking pipeline."""
    tx_hash = tx_hash.strip().upper()
    if tx_hash in PROCESSED_PAYMENTS:
        if via_protocol:
            await ctx.send(sender, CompletePayment(transaction_id=tx_hash))
        return

    pending = _pending_booking(sender)
    if not pending:
        if via_protocol:
            await ctx.send(sender, CancelPayment(transaction_id=tx_hash, reason="No open booking for this payment (it may have expired)"))
        await _reply(ctx, sender, "I don't have an open booking waiting for payment. Ask me for a route and I'll quote it again.")
        return
    tx_id, session = pending
    quote = session["quote_preview"]

    ok, reason = await verify_fet_payment(
        tx_hash=tx_hash,
        payer=payer,
        recipient=str(coordinator.wallet.address()),
        expected_amount_fet=str(quote.get("total_fare_fet", "0")),
    )
    ctx.logger.info(f"[Payment] tx={tx_hash} payer={payer or 'unspecified'} booking={tx_id} verified={ok} ({reason})")
    if not ok:
        if via_protocol:
            await ctx.send(sender, CancelPayment(transaction_id=tx_hash, reason=f"Payment verification failed: {reason}"))
        await _reply(
            ctx, sender,
            f"I couldn't verify that payment on chain ({reason}). No ticket was issued.",
            chat_cards.status_card(
                "Payment not verified",
                [("Reason", reason), ("Expected", f"{_fet_amount(quote.get('total_fare_fet'))} FET")],
                ("Not charged for a ticket", "warning"),
                [("Try again", {"action": "retry_payment"}, True), ("Cancel booking", {"action": "cancel_booking"}, False)],
            ),
        )
        return

    PROCESSED_PAYMENTS[tx_hash] = tx_id
    session.update(awaiting_payment=False, paid_tx=tx_hash, payer=payer)
    PENDING_PAYMENTS.pop(sender, None)
    if via_protocol:
        await ctx.send(sender, CompletePayment(transaction_id=tx_hash))
    await _reply(ctx, sender, f"Payment confirmed on chain ([view transaction]({explorer_tx_url(tx_hash)})). Issuing your ticket now...")
    await ctx.send(
        catalog_agent.address,
        CatalogSearchRequest(
            transaction_id=tx_id,
            origin_query=str(quote.get("origin_station")),
            destination_query=str(quote.get("destination_station")),
            passengers=int(quote.get("passengers", 1) or 1),
            city_code=ONDC_CITY_CODE,
            requester_sender=sender,
        ),
    )


@seller_payment_protocol.on_message(model=CommitPayment)
async def handle_commit_payment(ctx: Context, sender: str, msg: CommitPayment) -> None:
    """The rider approved the payment in ASI:One and the transfer was broadcast."""
    method = str(getattr(msg.funds, "payment_method", "") or "")
    currency = str(getattr(msg.funds, "currency", "") or "")
    ctx.logger.info(f"[inbound] CommitPayment from {sender} tx={msg.transaction_id} method={method} currency={currency}")
    if method != "fet_direct" or currency != "FET":
        await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason=f"Unsupported payment method: {method or currency}"))
        return
    meta = msg.metadata or {}
    payer = str(meta.get("buyer_fet_wallet") or meta.get("buyer_fet_address") or "")
    await _settle_rider_payment(ctx, sender, str(msg.transaction_id), payer, via_protocol=True)


@seller_payment_protocol.on_message(model=RejectPayment)
async def handle_reject_payment(ctx: Context, sender: str, msg: RejectPayment) -> None:
    ctx.logger.info(f"[inbound] RejectPayment from {sender}: {msg.reason}")
    pending = _pending_booking(sender)
    if pending:
        pending[1]["awaiting_payment"] = False
        PENDING_PAYMENTS.pop(sender, None)
    await _reply(ctx, sender, "Payment declined, so no ticket was booked and nothing was charged. Tap **Check fare** on any route to start again.", chat_cards.welcome_carousel())


async def _forward_to_ticketing(
    ctx: Context, msg: CatalogQuoteResponse, session: Dict[str, Any], tx_hash: str, payer: str
) -> None:
    """Sends the paid order to the ticketing agent."""
    await ctx.send(
        ticketing_agent.address,
        TicketingInitRequest(
            transaction_id=msg.transaction_id,
            origin_code=msg.origin_code,
            origin_name=msg.origin_name,
            destination_code=msg.destination_code,
            destination_name=msg.destination_name,
            passengers=msg.passengers,
            item_id=msg.item_id,
            provider_id=msg.provider_id,
            fulfillment_id=msg.fulfillment_id,
            total_fare_inr=msg.total_fare_inr,
            total_fare_fet=msg.total_fare_fet,
            bpp_id=msg.bpp_id,
            bpp_uri=msg.bpp_uri,
            city_code=session.get("city_code", ONDC_CITY_CODE),
            rider_name=session.get("rider_name"),
            rider_phone=session.get("rider_phone"),
            rider_email=session.get("rider_email"),
            payment_tx_hash=tx_hash,
            payer_wallet=payer,
        ),
    )


@ONDC_TRV11_LiveBAPProtocol.on_message(model=CatalogQuoteResponse)
async def handle_catalog_quote(ctx: Context, sender: str, msg: CatalogQuoteResponse) -> None:
    """
    Quote from `metro_bpp_catalog_agent`.
    - Chat bookings arrive here already paid by the rider; forward them to ticketing.
    - REST / CLI callers (dev tooling with no rider wallet): the coordinator wallet pays.
    """
    session = BOOKING_SESSIONS.setdefault(msg.transaction_id, {})
    session["quote"] = msg.model_dump()
    ctx.logger.info(f"[BAP Coordinator] Quote for tx={msg.transaction_id}: {msg.route_summary} | INR {msg.total_fare_inr}")

    if session.get("paid_tx"):
        await _forward_to_ticketing(ctx, msg, session, session["paid_tx"], session.get("payer", ""))
        return
    if session.get("chat_sender"):
        ctx.logger.warning(f"[BAP Coordinator] Unpaid chat booking reached the catalog step, ignoring tx={msg.transaction_id}")
        return

    try:
        def _pay() -> str:
            tx = ctx.ledger.send_tokens(
                str(ticketing_agent.wallet.address()),
                fet_to_base_units(msg.total_fare_fet),
                FETCHAI_DENOM,
                coordinator.wallet,
            )
            tx.wait_to_complete()
            return str(tx.tx_hash)

        tx_hash = await asyncio.to_thread(_pay)
    except Exception as exc:
        ctx.logger.error(f"[BAP Coordinator] REST-path ledger transfer failed: {exc}")
        ev = BOOKING_EVENTS.get(msg.transaction_id)
        if ev:
            ev.set()
        return
    await _forward_to_ticketing(ctx, msg, session, tx_hash, str(coordinator.wallet.address()))


@ONDC_TRV11_LiveBAPProtocol.on_message(model=TicketIssuedResponse)
async def handle_ticket_issued(ctx: Context, sender: str, msg: TicketIssuedResponse) -> None:
    """Final ticket from `metro_bpp_ticketing_agent`: wake REST callers or deliver the ticket card in chat."""
    session = BOOKING_SESSIONS.setdefault(msg.transaction_id, {})
    session["ticket"] = msg.model_dump()
    ev = BOOKING_EVENTS.get(msg.transaction_id)
    if ev:
        ev.set()

    chat_sender = session.get("chat_sender")
    if msg.status != "CONFIRMED":
        ctx.logger.error(f"[BAP Coordinator] Ticketing refused tx={msg.transaction_id}: {msg.status}")
        if chat_sender:
            await _reply(ctx, chat_sender, "Your payment went through, but the ticket could not be issued. Show this message to the team and we'll sort it out.")
        return

    ctx.logger.info(f"[BAP Coordinator] Ticket issued order={msg.order_id} tx={msg.payment_tx_hash}")
    if not chat_sender:
        return

    TICKET_OWNERS[msg.order_id] = chat_sender
    qr_url = chat_cards.qr_image_url(msg.qr_token)
    explorer = explorer_tx_url(msg.payment_tx_hash)
    text = (
        f"🎫 **Your ticket is ready: {msg.origin_name} → {msg.destination_name}**\n\n"
        f"Order `{msg.order_id}` · {msg.passengers} passenger{'s' if msg.passengers > 1 else ''} · "
        f"{chat_cards.fet(msg.total_fare_fet)} ({chat_cards.inr(msg.total_fare_inr)})\n\n"
        f"![Ticket QR code]({qr_url})\n\n"
        f"Payment: [view on the block explorer]({explorer})"
    )
    await _reply(ctx, chat_sender, text, chat_cards.ticket_card(msg.model_dump(), qr_url, explorer))
    LAST_QUOTED_ROUTES.pop(chat_sender, None)
    _remember(chat_sender, "assistant", f"Ticket issued for {msg.origin_name} to {msg.destination_name}, order {msg.order_id}.")


# ============================================================================
# ASI:One Chat Protocol
# ============================================================================


def _selection_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Card clicks can arrive as a JSON string in TextContent (direct @mention)."""
    candidate = text.strip()
    if not candidate.startswith("{"):
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not m:
            return None
        candidate = m.group(0)
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("action") else None


async def _show_journey(ctx: Context, sender: str, quote: Dict[str, Any], lead: str = "") -> None:
    if not quote.get("resolved", True):
        await _reply(ctx, sender, "I couldn't match one of those stations. Which Delhi Metro stations are you travelling between?")
        return
    LAST_QUOTED_ROUTES[sender] = quote
    text = (lead.strip() + "\n\n" if lead.strip() else "") + chat_cards.journey_summary_text(quote)
    _remember(sender, "assistant", text)
    await _reply(ctx, sender, text, chat_cards.journey_card(quote))


async def _scan(ctx: Context, sender: str, order_id: str, action: str, station: Optional[str] = None) -> None:
    owner = TICKET_OWNERS.get(order_id)
    if owner and owner != sender:
        await _reply(ctx, sender, "That ticket belongs to someone else, so I can't scan it for you.")
        return
    res = await verify_and_scan_dmrc_ticket(qr_token_or_order_id=order_id, action=action, station_code=station)
    flap = str(res.get("afc_gate_flap", "LOCKED_RED_LIGHT"))
    state = str(res.get("afc_lifecycle_state", "UNKNOWN"))
    message = str(res.get("afc_gate_message", res.get("reason", "")))
    opened = "OPEN" in flap or (action == "INSPECT" and res.get("valid_for_requested_action"))
    layers = res.get("verification_layers", {}) or {}
    sig_ok = bool((layers.get("ed25519_cryptographic_signature") or {}).get("valid"))

    if action == "INSPECT":
        title, badge = "Ticket check", (("Valid", "success") if opened else ("Not valid", "warning"))
        rows = [("Signature", "Valid" if sig_ok else "Invalid"), ("Status", state), ("Order", order_id)]
        buttons = [("Simulate entry gate", {"action": "gate_scan", "order_id": order_id, "station": station or ""}, True)]
    elif action == "ENTRY_SCAN":
        title, badge = "Entry gate", (("Gate open", "success") if opened else ("Gate closed", "warning"))
        rows = [("Status", state), ("Gate says", message), ("Order", order_id)]
        buttons = [("Simulate exit gate", {"action": "exit_scan", "order_id": order_id}, True)] if opened else []
    else:
        title, badge = "Exit gate", (("Journey complete", "success") if opened else ("Gate closed", "warning"))
        rows = [("Status", state), ("Gate says", message), ("Order", order_id)]
        buttons = [("Plan another trip", {"action": "show_routes"}, True)]

    await _reply(ctx, sender, f"**{title}:** {badge[0]}. {message}".strip(), chat_cards.status_card(title, rows, badge, buttons))


async def _handle_card_action(ctx: Context, sender: str, sel: Dict[str, Any]) -> None:
    action = str(sel.get("action", ""))
    ctx.logger.info(f"[ASI:One card] sender={sender} action={action} data={sel}")

    if action in ("quote_route", "plan_journey"):
        quote = await _quote(str(sel.get("origin", "")), str(sel.get("destination", "")), int(sel.get("passengers", 1) or 1))
        await _show_journey(ctx, sender, quote)
    elif action == "book_route":
        origin, dest = str(sel.get("origin", "")), str(sel.get("destination", ""))
        pax = int(sel.get("passengers", 1) or 1)
        last = LAST_QUOTED_ROUTES.get(sender)
        quote = _with_passengers(last, pax) if last and _same_route(last, origin, dest) else await _quote(origin, dest, pax)
        if not quote.get("resolved", True):
            await _reply(ctx, sender, "I couldn't match one of those stations. Which Delhi Metro stations are you travelling between?")
            return
        _remember(sender, "user", f"Book {pax} ticket(s) from {origin} to {dest}")
        await _request_rider_payment(ctx, sender, quote)
    elif action == "choose_pax":
        origin, dest = str(sel.get("origin", "")), str(sel.get("destination", ""))
        last = LAST_QUOTED_ROUTES.get(sender)
        unit = (last or {}).get("official_dmrc_fare_inr") if last and _same_route(last, origin, dest) else None
        await _reply(ctx, sender, "How many passengers?", chat_cards.passengers_card(origin, dest, unit))
    elif action == "retry_payment":
        pending = _pending_booking(sender)
        if pending:
            await _reply(ctx, sender, "Here's the payment request again.", _pay_card(pending[1]["quote_preview"]))
            await _send_payment_request(ctx, sender, pending[1])
        else:
            await _reply(ctx, sender, "That booking expired. Pick a route and I'll quote it again.", chat_cards.welcome_carousel())
    elif action == "check_payment":
        await _check_payment(ctx, sender)
    elif action == "cancel_booking":
        pending = _pending_booking(sender)
        if pending:
            pending[1]["awaiting_payment"] = False
            PENDING_PAYMENTS.pop(sender, None)
        await _reply(ctx, sender, "Booking cancelled. Nothing was charged.", chat_cards.welcome_carousel())
    elif action == "verify_ticket":
        await _scan(ctx, sender, str(sel.get("order_id", "")), "INSPECT", str(sel.get("station") or "") or None)
    elif action == "gate_scan":
        await _scan(ctx, sender, str(sel.get("order_id", "")), "ENTRY_SCAN", str(sel.get("station") or "") or None)
    elif action == "exit_scan":
        await _scan(ctx, sender, str(sel.get("order_id", "")), "EXIT_SCAN")
    else:  # show_routes and anything unknown
        await _reply(ctx, sender, "Where would you like to go? Pick a popular route or just type your journey, like *Hauz Khas to Rajiv Chowk*.", chat_cards.welcome_carousel())


async def _check_payment(ctx: Context, sender: str) -> None:
    """Looks on chain for the rider's transfer, so a wallet payment works without pasting a tx hash."""
    pending = _pending_booking(sender)
    if not pending:
        await _reply(ctx, sender, "There's no booking waiting for payment. Pick a route and I'll quote it.", chat_cards.welcome_carousel())
        return
    tx_id, session = pending
    quote = session["quote_preview"]
    amount = _fet_amount(quote.get("total_fare_fet"))
    found = await find_incoming_payment(
        recipient=str(coordinator.wallet.address()),
        expected_amount_fet=amount,
        since_ts=float(session.get("created_at", 0)),
        exclude=PROCESSED_PAYMENTS.keys(),
    )
    ctx.logger.info(f"[Payment] chain check for booking={tx_id}: {'found ' + found[0] if found else 'nothing yet'}")
    if found:
        await _settle_rider_payment(ctx, sender, found[0], found[1], via_protocol=False)
        return
    await _reply(
        ctx, sender,
        f"**No payment received yet**, so no ticket has been booked. I'm waiting for **{amount} FET** at "
        f"`{coordinator.wallet.address()}` ({_network_label()}). Tap **I've paid** again once the transfer goes through.",
        _pay_card(quote),
    )


@chat_protocol.on_message(ChatMessage)
async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))

    selection: Optional[Dict[str, Any]] = None
    user_text = ""
    for item in msg.content:
        if isinstance(item, MetadataContent):
            resp = extract_card_response(item)
            if resp and resp.selection:
                selection = dict(resp.selection)
        elif isinstance(item, TextContent):
            user_text += item.text + " "
        elif isinstance(item, StartSessionContent):
            ctx.logger.info(f"[ASI:One Chat] Session started with {sender}")
        elif isinstance(item, EndSessionContent):
            ctx.logger.info(f"[ASI:One Chat] Session ended with {sender}")
            return
    user_text = user_text.strip()
    # Direct @mentions deliver card clicks as JSON text
    selection = selection or _selection_from_text(user_text)

    try:
        if selection:
            await _handle_card_action(ctx, sender, selection)
            return
        if not user_text:
            return
        await _handle_text(ctx, sender, user_text)
    except Exception as exc:
        ctx.logger.exception(f"[ASI:One Chat] handler error: {exc}")
        await _reply(ctx, sender, "Something went wrong on my side. Please try that again.")


async def _handle_text(ctx: Context, sender: str, user_text: str) -> None:
    short = len(user_text.split()) <= 4
    pending = _pending_booking(sender)

    if pending:
        hash_match = re.search(r"\b(?:0x)?([0-9A-Fa-f]{64})\b", user_text)
        if hash_match:
            await _settle_rider_payment(ctx, sender, hash_match.group(1).upper(), "", via_protocol=False)
            return
        if short and re.search(r"\b(cancel|stop|no|nope|never ?mind)\b", user_text, re.IGNORECASE):
            await _handle_card_action(ctx, sender, {"action": "cancel_booking"})
            return
        if short and re.search(r"\b(yes|yep|yeah|sure|ok|okay|pay|paid|book|confirm|proceed|go ahead|done|approved?|sent)\b", user_text, re.IGNORECASE):
            # Never take "approved" at face value: check the chain, and say plainly if nothing arrived.
            await _check_payment(ctx, sender)
            return

    last_quote = LAST_QUOTED_ROUTES.get(sender)
    if short and last_quote and re.search(r"\b(yes|yep|yeah|sure|ok|okay|book( it)?|confirm|proceed|go ahead)\b", user_text, re.IGNORECASE):
        _remember(sender, "user", user_text)
        await _request_rider_payment(ctx, sender, last_quote)
        return

    _remember(sender, "user", user_text)
    try:
        turn = await asi1_client.analyze_turn(user_text, chat_history=CHAT_CONVERSATION_HISTORY.get(sender, [])[:-1])
    except Exception as exc:
        ctx.logger.warning(f"[ASI:One API] analyze_turn error: {exc}")
        turn = {"ready_to_book": False, "assistant_reply": "Tell me where you're starting and where you're going, and I'll find the route and fare."}

    if turn.get("ready_to_book"):
        quote = await _quote(str(turn.get("origin_name") or turn.get("origin_code")), str(turn.get("destination_name") or turn.get("destination_code")), int(turn.get("passengers") or 1))
        if not quote.get("resolved", True):
            await _reply(ctx, sender, "I couldn't match one of those stations. Which Delhi Metro stations are you travelling between?")
            return
        await _request_rider_payment(ctx, sender, quote)
        return

    reply = str(turn.get("assistant_reply") or "").strip()
    route_quote = turn.get("route_quote")
    if route_quote:
        await _show_journey(ctx, sender, route_quote, lead=reply)
        return

    first_time = len(CHAT_CONVERSATION_HISTORY.get(sender, [])) <= 1
    if first_time or re.search(r"^(hi|hello|hey|start|namaste)\b|\b(show|popular|other)\s+routes\b", user_text, re.IGNORECASE):
        text = reply if reply and not first_time else "👋 Namaste! I can plan Delhi Metro journeys and book tickets you pay for in FET. Pick a popular route or tell me where you're going."
        _remember(sender, "assistant", text)
        await _reply(ctx, sender, text, chat_cards.welcome_carousel())
        return

    reply = reply or "Tell me your start and destination stations and I'll find the route and fare."
    _remember(sender, "assistant", reply)
    await _reply(ctx, sender, reply)


@chat_protocol.on_message(ChatAcknowledgement)
async def handle_chat_ack(
    ctx: Context, sender: str, msg: ChatAcknowledgement
) -> None:
    ctx.logger.debug(
        f"[ASI:One Chat] Ack received from {sender} for msg_id={msg.acknowledged_msg_id}"
    )


# ============================================================================
# REST Endpoint: POST /metro/chat (Conversational AI & Booking via ASI:One API)
# ============================================================================


@coordinator.on_rest_post(
    "/metro/chat", MetroChatRESTRequest, MetroChatRESTResponse
)
async def handle_rest_metro_chat(
    ctx: Context, req: MetroChatRESTRequest
) -> MetroChatRESTResponse:
    """
    Conversational & Natural-Language Metro Booking endpoint powered by the live ASI:One API (`asi1-mini`):
    1. Calls `asi1_client.analyze_turn(req.prompt, req.history)`.
    2. If the user is greeting ('hey'), asking a question, or hasn't provided both origin and destination stations yet:
       returns `status="CHAT"` with `asi1-mini`'s conversational response asking for the missing details (NO premature booking!).
    3. Once `ready_to_book=True` (both origin & destination stations are known):
       executes the full Live OTD + ONDC:TRV11 + Dorado Testnet FET + DMRC RSF 2.0 pipeline and issues the signed QR ticket.
    """
    try:
        turn = await asi1_client.analyze_turn(req.prompt, chat_history=req.history)
    except Exception as exc:
        ctx.logger.error(f"[POST /metro/chat] Unhandled analyze_turn exception: {exc}")
        return MetroChatRESTResponse(
            status="CHAT",
            transaction_id="",
            user_prompt=req.prompt,
            asi1_llm_metadata={"error": str(exc)},
            asi1_assistant_reply="I am here to help you with Delhi Metro tickets, routes, and fares! Please share your starting station and destination station.",
        )

    llm_meta = turn.get("llm_metadata", {})

    if not turn.get("ready_to_book"):
        ctx.logger.info(
            f"[POST /metro/chat | Conversational Turn] '{req.prompt}' -> Asking user for trip details (id={llm_meta.get('id')})"
        )
        return MetroChatRESTResponse(
            status="CHAT",
            transaction_id="",
            user_prompt=req.prompt,
            asi1_llm_metadata=llm_meta,
            asi1_assistant_reply=str(turn.get("assistant_reply", "")),
        )

    origin_q = str(turn.get("origin_code"))
    dest_q = str(turn.get("destination_code"))
    passengers = int(turn.get("passengers") or 1)
    ctx.logger.info(
        f"[POST /metro/chat | ASI:One API] Ready to book: '{req.prompt}' -> "
        f"{origin_q} to {dest_q}, passengers={passengers} (id={llm_meta.get('id')})"
    )

    tx_id = str(uuid.uuid4())
    event = asyncio.Event()
    BOOKING_EVENTS[tx_id] = event
    BOOKING_SESSIONS[tx_id] = {
        "city_code": req.city_code,
        "user_prompt": req.prompt,
        "asi1_llm_metadata": llm_meta,
    }

    await ctx.send(
        catalog_agent.address,
        CatalogSearchRequest(
            transaction_id=tx_id,
            origin_query=origin_q,
            destination_query=dest_q,
            passengers=passengers,
            city_code=req.city_code,
        ),
    )

    try:
        await asyncio.wait_for(event.wait(), timeout=45.0)
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[POST /metro/chat] Timeout waiting for full pipeline tx={tx_id}")
    finally:
        BOOKING_EVENTS.pop(tx_id, None)

    session = BOOKING_SESSIONS.get(tx_id, {})
    quote = session.get("quote", {})
    ticket = session.get("ticket", {})

    try:
        assistant_reply = await asi1_client.format_ticket_summary(
            user_prompt=req.prompt,
            ticket=ticket,
            quote=quote,
        )
    except Exception as exc:
        ctx.logger.warning(f"[POST /metro/chat] ASI:One summary fallback: {exc}")
        assistant_reply = (
            f"Confirmed Order {ticket.get('order_id')}: {origin_q} -> {dest_q} "
            f"for {passengers} passenger(s). QR: {ticket.get('qr_token')}"
        )

    return MetroChatRESTResponse(
        status=ticket.get("status", "PENDING"),
        transaction_id=tx_id,
        user_prompt=req.prompt,
        asi1_llm_metadata=llm_meta,
        asi1_assistant_reply=assistant_reply,
        origin={
            "stop_id": quote.get("origin_stop_id", origin_q),
            "code": ticket.get("origin_code", origin_q),
            "name": ticket.get("origin_name", origin_q),
            "gps": quote.get("origin_gps", ""),
        },
        destination={
            "stop_id": quote.get("destination_stop_id", dest_q),
            "code": ticket.get("destination_code", dest_q),
            "name": ticket.get("destination_name", dest_q),
            "gps": quote.get("destination_gps", ""),
        },
        passengers=passengers,
        quote_inr=ticket.get("total_fare_inr", quote.get("total_fare_inr", "0.00")),
        quote_fet=ticket.get("total_fare_fet", quote.get("total_fare_fet", "0.0000")),
        payment_tx_hash=ticket.get("payment_tx_hash"),
        dmrc_settlement_receipt=ticket.get("dmrc_settlement_receipt", {}),
        ticket_qr_token=ticket.get("qr_token"),
        realtime_active_vehicles=int(quote.get("realtime_vehicle_count", 0)),
        realtime_vehicles_sample=quote.get("realtime_vehicles_sample", []),
    )


# ============================================================================
# REST Endpoint: POST /metro/book
# ============================================================================


@coordinator.on_rest_post(
    "/metro/book", MetroBookRESTRequest, MetroBookRESTResponse
)
async def handle_rest_metro_book(
    ctx: Context, req: MetroBookRESTRequest
) -> MetroBookRESTResponse:
    """
    Triggers an end-to-end multi-agent metro booking from web/mobile apps:
    1. Sends CatalogSearchRequest to metro_bpp_catalog_agent (OTD GTFS + Live VehiclePositions + ONDC /search & /select)
    2. Forwards quote to metro_bpp_ticketing_agent (ONDC /init + RequestPayment)
    3. Executes on-chain Dorado Testnet FET transfer (`ctx.ledger.send_tokens`)
    4. Verifies TX on-chain, settles INR from BAP Nodal Account to DMRC BPP (`SETTLEMENT_TERMS`), and dispatches ONDC /confirm
    """
    tx_id = str(uuid.uuid4())
    event = asyncio.Event()
    BOOKING_EVENTS[tx_id] = event
    BOOKING_SESSIONS[tx_id] = {
        "city_code": req.city_code,
        "rider_name": req.rider_name,
        "rider_phone": req.rider_phone,
        "rider_email": req.rider_email,
    }

    await ctx.send(
        catalog_agent.address,
        CatalogSearchRequest(
            transaction_id=tx_id,
            origin_query=req.origin_code,
            destination_query=req.destination_code,
            passengers=req.passengers,
            city_code=req.city_code,
        ),
    )

    try:
        await asyncio.wait_for(event.wait(), timeout=45.0)
    except asyncio.TimeoutError:
        ctx.logger.warning(f"[POST /metro/book] Timeout waiting for full pipeline tx={tx_id}")
    finally:
        BOOKING_EVENTS.pop(tx_id, None)

    session = BOOKING_SESSIONS.get(tx_id, {})
    quote = session.get("quote", {})
    ticket = session.get("ticket", {})

    if ticket:
        return MetroBookRESTResponse(
            status=ticket.get("status", "CONFIRMED"),
            transaction_id=tx_id,
            origin={
                "stop_id": quote.get("origin_stop_id", ""),
                "code": ticket.get("origin_code", req.origin_code),
                "name": ticket.get("origin_name", req.origin_code),
                "gps": quote.get("origin_gps", ""),
            },
            destination={
                "stop_id": quote.get("destination_stop_id", ""),
                "code": ticket.get("destination_code", req.destination_code),
                "name": ticket.get("destination_name", req.destination_code),
                "gps": quote.get("destination_gps", ""),
            },
            passengers=req.passengers,
            quote_inr=ticket.get("total_fare_inr", quote.get("total_fare_inr", "0.00")),
            quote_fet=ticket.get("total_fare_fet", quote.get("total_fare_fet", "0.0000")),
            payment_tx_hash=ticket.get("payment_tx_hash"),
            dmrc_settlement_receipt=ticket.get("dmrc_settlement_receipt", {}),
            ticket_qr_token=ticket.get("qr_token"),
            bpp_id=ticket.get("bpp_id", quote.get("bpp_id", "")),
            bpp_uri=quote.get("bpp_uri", ""),
            realtime_active_vehicles=int(quote.get("realtime_vehicle_count", 0)),
            realtime_vehicles_sample=quote.get("realtime_vehicles_sample", []),
            message=(
                f"Order {ticket.get('order_id')} confirmed via ONDC:TRV11 v2.1.0, "
                f"settled on Fetch.ai Dorado Testnet, and remitted to DMRC BPP via ONDC RSF 2.0."
            ),
        )

    return MetroBookRESTResponse(
        status="PENDING",
        transaction_id=tx_id,
        origin={
            "stop_id": quote.get("origin_stop_id", ""),
            "code": quote.get("origin_code", req.origin_code),
            "name": quote.get("origin_name", req.origin_code),
            "gps": quote.get("origin_gps", ""),
        },
        destination={
            "stop_id": quote.get("destination_stop_id", ""),
            "code": quote.get("destination_code", req.destination_code),
            "name": quote.get("destination_name", req.destination_code),
            "gps": quote.get("destination_gps", ""),
        },
        passengers=req.passengers,
        quote_inr=quote.get("total_fare_inr", "0.00"),
        quote_fet=quote.get("total_fare_fet", "0.0000"),
        payment_tx_hash=None,
        dmrc_settlement_receipt={},
        ticket_qr_token=None,
        bpp_id=quote.get("bpp_id", ""),
        bpp_uri=quote.get("bpp_uri", ""),
        realtime_active_vehicles=int(quote.get("realtime_vehicle_count", 0)),
        realtime_vehicles_sample=quote.get("realtime_vehicles_sample", []),
        message="Booking pipeline initiated; awaiting ledger settlement completion.",
    )


# ============================================================================
# REST Endpoint: POST /metro/ticket/verify (DMRC AFC Gate Scanner & Validator)
# ============================================================================


@coordinator.on_rest_post(
    "/metro/ticket/verify",
    MetroTicketVerifyRESTRequest,
    MetroTicketVerifyRESTResponse,
)
async def handle_rest_ticket_verify(
    ctx: Context, req: MetroTicketVerifyRESTRequest
) -> MetroTicketVerifyRESTResponse:
    """
    Validates a DMRC QR ticket across 3 layers (Ed25519 signature, Fetch.ai Dorado Testnet blockchain TX,
    and DMRC AFC Gate Registry) and optionally executes AFC turnstile state transitions:
      - action="INSPECT"      : Non-destructive validation of signature + blockchain + status
      - action="ENTRY_SCAN"   : Marks Journey Started (ISSUED -> IN_TRANSIT) at Origin Entry Gate
      - action="EXIT_SCAN"    : Marks Journey Ended & Ticket Consumed/Expired (IN_TRANSIT -> COMPLETED) at Destination Exit Gate
      - action="FORCE_EXPIRE" : Marks Ticket Expired (EXPIRED)
    """
    res = await verify_and_scan_dmrc_ticket(
        qr_token_or_order_id=req.qr_or_order_id,
        action=req.action,
        station_code=req.station_code,
    )
    ctx.logger.info(
        f"[DMRC AFC Gate | {req.action}] Order={res.get('order_id')} | "
        f"Flap={res.get('afc_gate_flap')} | State={res.get('afc_lifecycle_state')}"
    )
    return MetroTicketVerifyRESTResponse(
        valid_for_requested_action=bool(res.get("valid_for_requested_action", False)),
        requested_action=str(res.get("requested_action", req.action)),
        afc_gate_flap=str(res.get("afc_gate_flap", "LOCKED_RED_LIGHT")),
        afc_gate_message=str(res.get("afc_gate_message", res.get("reason", ""))),
        order_id=str(res.get("order_id", "")),
        afc_lifecycle_state=str(res.get("afc_lifecycle_state", "UNKNOWN")),
        ondc_fulfillment_state=str(res.get("ondc_fulfillment_state", "UNKNOWN")),
        decoded_ticket=res.get("decoded_ticket", {}),
        timestamps=res.get("timestamps", {}),
        verification_layers=res.get("verification_layers", {}),
    )


coordinator.include(ONDC_TRV11_LiveBAPProtocol, publish_manifest=True)
coordinator.include(chat_protocol, publish_manifest=True)
coordinator.include(seller_payment_protocol, publish_manifest=True)


if __name__ == "__main__":
    coordinator.run()
