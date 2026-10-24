"""
metro_bpp_ticketing_agent:
- Executes signed ONDC:TRV11 v2.1.0 `POST /init`
- Receives orders whose FET payment was already collected and verified by the coordinator
- Verifies on-chain transaction hash (`ctx.ledger.query_tx(payment_tx_hash).is_successful()`)
- Dispatches signed ONDC:TRV11 v2.1.0 `POST /confirm` with `payments[0].params.transaction_id` & `status="PAID"`
- Exposes `POST /beckn/on_init` and `POST /beckn/on_confirm`
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import uuid

from uagents import Agent, Context, Model, Protocol
from uagents.setup import fund_agent_if_low
from common.config import (
    FETCHAI_NETWORK,
    ONDC_DEFAULT_BPP_ID,
    ONDC_DEFAULT_BPP_URI,
    TICKETING_AGENT_SEED,
)
from common.models import (
    BecknWebhookAckResponse,
    TicketIssuedResponse,
    TicketingInitRequest,
)
from common.ondc_client import ONDCTRV11Client


class BecknTicketingCallbackPayload(Model):
    """Inbound ONDC Beckn webhook payload for /beckn/on_init and /beckn/on_confirm."""

    context: Dict[str, Any]
    message: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


ticketing_agent = Agent(
    name="metro_bpp_ticketing_agent",
    seed=TICKETING_AGENT_SEED,
    port=8002,
    endpoint=["http://localhost:8002/submit"],
    network=FETCHAI_NETWORK,
)

ONDC_TRV11_LiveTicketingProtocol = Protocol(
    name="ONDC_TRV11_LiveTicketingProtocol", version="2.1.0"
)

ondc_client = ONDCTRV11Client()

# Tracks pending orders awaiting on-chain payment settlement keyed by buyer sender and transaction_id
PENDING_ORDERS_BY_TX: Dict[str, Dict[str, Any]] = {}
LATEST_TX_BY_BUYER: Dict[str, str] = {}
ON_INIT_CALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}
ON_CONFIRM_CALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}


@ticketing_agent.on_event("startup")
async def on_ticketing_startup(ctx: Context) -> None:
    ctx.logger.info(
        f"[metro_bpp_ticketing_agent] Address: {ticketing_agent.address} | "
        f"Wallet: {ticketing_agent.wallet.address()}"
    )
    try:
        fund_agent_if_low(str(ticketing_agent.wallet.address()))
    except Exception as exc:
        ctx.logger.warning(f"Wallet funding check skipped or deferred: {exc}")


@ONDC_TRV11_LiveTicketingProtocol.on_message(
    model=TicketingInitRequest, replies={TicketIssuedResponse}
)
async def handle_ticketing_init(
    ctx: Context, sender: str, msg: TicketingInitRequest
) -> None:
    """
    1. Sends signed ONDC:TRV11 POST /init to BPP URI.
    2. Verifies the coordinator-confirmed FET payment on chain and issues the ticket.
    """
    bpp_uri = msg.bpp_uri or ONDC_DEFAULT_BPP_URI
    bpp_id = msg.bpp_id or ONDC_DEFAULT_BPP_ID

    init_payload = ondc_client.build_init_payload(
        transaction_id=msg.transaction_id,
        provider_id=msg.provider_id,
        item_id=msg.item_id,
        fulfillment_id=msg.fulfillment_id,
        passengers=msg.passengers,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        city_code=msg.city_code,
    )
    try:
        init_resp, _ = await ondc_client.post_beckn_action(bpp_uri, "init", init_payload)
        ctx.logger.info(
            f"[ONDC /init] Dispatched to {bpp_uri}/init for tx={msg.transaction_id} | Resp={init_resp}"
        )
    except Exception as exc:
        ctx.logger.warning(f"[ONDC /init] BPP HTTP warning ({bpp_uri}/init): {exc}")

    PENDING_ORDERS_BY_TX[msg.transaction_id] = {
        "buyer_sender": sender,
        "request": msg.model_dump(),
    }
    LATEST_TX_BY_BUYER[sender] = msg.transaction_id

    # Payment is collected and verified by the coordinator (the seller-facing agent) before this request is sent.
    await _issue_ticket(ctx, sender, msg.payment_tx_hash, msg.transaction_id)


async def _issue_ticket(
    ctx: Context, sender: str, payment_tx_hash: str, reference_tx_id: str
) -> None:
    """Confirms the ONDC order for a paid transaction and returns the signed QR ticket to the coordinator."""
    order_entry = PENDING_ORDERS_BY_TX.get(reference_tx_id, {})
    req_data: Dict[str, Any] = order_entry.get("request", {})
    ondc_transaction_id = req_data.get("transaction_id") or reference_tx_id or str(uuid.uuid4())

    verified_on_chain = False
    try:
        tx_status = ctx.ledger.query_tx(payment_tx_hash)
        verified_on_chain = bool(tx_status.is_successful())
        ctx.logger.info(
            f"[Dorado Ledger] Verified tx_hash={payment_tx_hash} | is_successful={verified_on_chain}"
        )
    except Exception as exc:
        ctx.logger.warning(
            f"[Dorado Ledger] Ledger query warning for tx_hash={payment_tx_hash}: {exc}"
        )

    if not verified_on_chain:
        await ctx.send(
            sender,
            TicketIssuedResponse(
                transaction_id=ondc_transaction_id,
                order_id="",
                origin_code=req_data.get("origin_code", ""),
                origin_name=req_data.get("origin_name", ""),
                destination_code=req_data.get("destination_code", ""),
                destination_name=req_data.get("destination_name", ""),
                passengers=int(req_data.get("passengers", 1)),
                total_fare_inr=str(req_data.get("total_fare_inr", "0")),
                total_fare_fet=str(req_data.get("total_fare_fet", "0")),
                payment_tx_hash=payment_tx_hash,
                payment_verified_on_chain=False,
                qr_token="",
                valid_until="",
                bpp_id=req_data.get("bpp_id", ""),
                status="PAYMENT_FAILED",
            ),
        )
        return

    # 2. Extract DMRC's BPP bank / UPI settlement details from /on_init and execute BAP Nodal -> DMRC INR settlement
    bpp_uri = req_data.get("bpp_uri") or ONDC_DEFAULT_BPP_URI
    bpp_id = req_data.get("bpp_id") or ONDC_DEFAULT_BPP_ID
    origin_code = req_data.get("origin_code", "ORIGIN")
    origin_name = req_data.get("origin_name", origin_code)
    destination_code = req_data.get("destination_code", "DEST")
    destination_name = req_data.get("destination_name", destination_code)
    passengers = int(req_data.get("passengers", 1))
    total_fare_inr = str(req_data.get("total_fare_inr", "30.00"))
    total_fare_fet = str(req_data.get("total_fare_fet", "0.3000"))

    on_init_cb = ON_INIT_CALLBACK_CACHE.get(ondc_transaction_id)
    dmrc_settlement_details = ondc_client.extract_dmrc_settlement_details(on_init_cb)
    dmrc_settlement_receipt = await ondc_client.execute_dmrc_inr_settlement(
        transaction_id=ondc_transaction_id,
        amount_inr=total_fare_inr,
        fet_tx_hash=payment_tx_hash,
        dmrc_settlement=dmrc_settlement_details,
    )
    settlement_utr = str(dmrc_settlement_receipt.get("settlement_reference_utr", ""))
    ctx.logger.info(
        f"[DMRC RSF 2.0 Settlement] Settled INR {total_fare_inr} from BAP Nodal "
        f"({dmrc_settlement_details['payer_bap_upi_vpa']}) -> DMRC BPP "
        f"({dmrc_settlement_details['beneficiary_name']} / {dmrc_settlement_details['upi_address']}) "
        f"| UTR={settlement_utr}"
    )

    # 3. Dispatch ONDC:TRV11 POST /confirm with PAID status, on-chain tx_hash, DMRC bank/VPA, and UTR
    confirm_payload = ondc_client.build_confirm_payload(
        transaction_id=ondc_transaction_id,
        provider_id=req_data.get("provider_id", "DMRC_PROVIDER_01"),
        item_id=req_data.get("item_id", f"METRO_{origin_code}_{destination_code}"),
        fulfillment_id=req_data.get("fulfillment_id", "FULFILLMENT_METRO_1"),
        passengers=passengers,
        amount_inr=total_fare_inr,
        payment_tx_hash=payment_tx_hash,
        settlement_reference_utr=settlement_utr,
        dmrc_settlement=dmrc_settlement_details,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        city_code=req_data.get("city_code", "std:011"),
        rider_name=req_data.get("rider_name"),
        rider_phone=req_data.get("rider_phone"),
        rider_email=req_data.get("rider_email"),
    )

    confirm_resp: Dict[str, Any] = {}
    try:
        confirm_resp, _ = await ondc_client.post_beckn_action(
            bpp_uri, "confirm", confirm_payload
        )
        ctx.logger.info(
            f"[ONDC /confirm] Dispatched to {bpp_uri}/confirm | Resp={confirm_resp}"
        )
    except Exception as exc:
        ctx.logger.warning(f"[ONDC /confirm] BPP HTTP warning ({bpp_uri}/confirm): {exc}")

    # Extract QR token from /on_confirm callback cache if available, else issue signed DMRC/ONDC QR token
    cb_confirm = ON_CONFIRM_CALLBACK_CACHE.get(ondc_transaction_id) or confirm_resp
    order_obj = (cb_confirm.get("message") or {}).get("order") or {}
    order_id = order_obj.get("id") or f"ONDC-TRV11-{ondc_transaction_id[:8].upper()}"

    fulfillments = order_obj.get("fulfillments") or []
    qr_token = ""
    if fulfillments:
        stops = fulfillments[0].get("stops") or []
        for s in stops:
            auth = s.get("authorization") or {}
            if auth.get("token"):
                qr_token = str(auth["token"])
                break
    valid_until = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    from common.ticket_verifier import create_signed_dmrc_qr_token

    signed_qr_token, qr_png_path = create_signed_dmrc_qr_token(
        order_id=order_id,
        transaction_id=ondc_transaction_id,
        origin_code=origin_code,
        destination_code=destination_code,
        passengers=passengers,
        fare_inr=total_fare_inr,
        fare_fet=total_fare_fet,
        settlement_utr=settlement_utr,
        payment_tx_hash=payment_tx_hash,
        valid_until=valid_until,
        dmrc_settlement_receipt=dmrc_settlement_receipt,
        origin_name=req_data.get("origin_name"),
        destination_name=req_data.get("destination_name"),
    )
    if not qr_token:
        qr_token = signed_qr_token
    dmrc_settlement_receipt["qr_png_path"] = qr_png_path

    await ctx.send(
        sender,
        TicketIssuedResponse(
            transaction_id=ondc_transaction_id,
            order_id=order_id,
            origin_code=origin_code,
            origin_name=origin_name,
            destination_code=destination_code,
            destination_name=destination_name,
            passengers=passengers,
            total_fare_inr=total_fare_inr,
            total_fare_fet=total_fare_fet,
            payment_tx_hash=payment_tx_hash,
            payment_verified_on_chain=verified_on_chain,
            dmrc_settlement_receipt=dmrc_settlement_receipt,
            qr_token=qr_token,
            valid_until=valid_until,
            bpp_id=bpp_id,
            status="CONFIRMED",
        ),
    )


@ticketing_agent.on_rest_post(
    "/beckn/on_init", BecknTicketingCallbackPayload, BecknWebhookAckResponse
)
async def handle_beckn_on_init(
    ctx: Context, req: BecknTicketingCallbackPayload
) -> BecknWebhookAckResponse:
    """Asynchronous ONDC Beckn webhook for /beckn/on_init."""
    tx_id = str((req.context or {}).get("transaction_id", ""))
    if tx_id:
        ON_INIT_CALLBACK_CACHE[tx_id] = req.model_dump()
    ctx.logger.info(f"[Webhook /beckn/on_init] Received callback for transaction_id={tx_id}")
    return BecknWebhookAckResponse(message={"ack": {"status": "ACK"}})


@ticketing_agent.on_rest_post(
    "/beckn/on_confirm", BecknTicketingCallbackPayload, BecknWebhookAckResponse
)
async def handle_beckn_on_confirm(
    ctx: Context, req: BecknTicketingCallbackPayload
) -> BecknWebhookAckResponse:
    """Asynchronous ONDC Beckn webhook for /beckn/on_confirm."""
    tx_id = str((req.context or {}).get("transaction_id", ""))
    if tx_id:
        ON_CONFIRM_CALLBACK_CACHE[tx_id] = req.model_dump()
    ctx.logger.info(f"[Webhook /beckn/on_confirm] Received callback for transaction_id={tx_id}")
    return BecknWebhookAckResponse(message={"ack": {"status": "ACK"}})


ticketing_agent.include(ONDC_TRV11_LiveTicketingProtocol, publish_manifest=True)


if __name__ == "__main__":
    ticketing_agent.run()
