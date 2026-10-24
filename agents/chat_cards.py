"""
ASI:One card builders for the coordinator's chat UI.

Every card is sent together with a plain-text summary, so clients that do not render
cards still show the full information.
"""

from typing import Any, Dict, List, Optional
import urllib.parse
import uuid

from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    BadgeNode,
    ButtonAction,
    ButtonNode,
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    CustomCardPayload,
    DividerNode,
    GroupNode,
    HeadingNode,
    ImageNode,
    ListItem,
    ListNode,
    SectionNode,
    TextNode,
    create_card_content,
)

POPULAR_ROUTES = [
    ("Kashmere Gate", "Rajiv Chowk", "Yellow Line", "Interchange hub"),
    ("Hauz Khas", "Botanical Garden", "Magenta Line", "Direct"),
    ("New Delhi", "IGI Airport (T1-T2-T3)", "Airport Express", "Airport link"),
    ("Noida Sector 18", "Rajiv Chowk", "Blue Line", "NCR"),
]


def _text(value: str, style: Optional[str] = None) -> TextNode:
    return TextNode(type="text", value=value, style=style)


def _badge(label: str, variant: Optional[str] = None) -> BadgeNode:
    return BadgeNode(type="badge", label=label, variant=variant)


def _button(label: str, selection: Dict[str, Any], primary: bool = False) -> ButtonNode:
    return ButtonNode(type="button", label=label, primary=primary, action=ButtonAction(selection=selection))


def _row(*children) -> GroupNode:
    return GroupNode(type="group", direction="row", gap=8, children=list(children))


def _field(label: str, value: str) -> GroupNode:
    return _row(_text(label, "muted"), _text(value, "emphasis"))


def _custom(root: SectionNode) -> MetadataContent:
    return create_card_content(CustomCardPayload(root=root), card_id=uuid.uuid4())


def inr(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"₹{value}"
    return f"₹{v:.0f}" if v == int(v) else f"₹{v:.2f}"


def fet(value: Any) -> str:
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".") + " FET"
    except (TypeError, ValueError):
        return f"{value} FET"


def _lines(quote: Dict[str, Any]) -> List[str]:
    lines = []
    for leg in quote.get("route_legs") or []:
        name = leg.split(":")[0].strip() if ":" in leg else leg
        if name and name not in lines:
            lines.append(name)
    return lines


def _travel_time(quote: Dict[str, Any]) -> Optional[str]:
    raw = str(quote.get("travel_time") or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            h, m = int(parts[0]), int(parts[1]) + (1 if int(parts[2]) >= 30 else 0)
            return f"{h} h {m} min" if h else f"{m} min"
    except ValueError:
        pass
    return raw


def journey_summary_text(quote: Dict[str, Any]) -> str:
    pax = int(quote.get("passengers", 1) or 1)
    parts = [
        f"**{quote.get('origin_station')} → {quote.get('destination_station')}**",
        f"Fare: {inr(quote.get('total_fare_inr'))} ({fet(quote.get('total_fare_fet'))}) for {pax} passenger{'s' if pax > 1 else ''}",
    ]
    lines = _lines(quote)
    if lines:
        parts.append("Lines: " + ", ".join(lines))
    if quote.get("stations_count"):
        parts.append(f"{quote['stations_count']} stations")
    t = _travel_time(quote)
    if t:
        parts.append(f"About {t}")
    return "\n".join(parts)


def journey_card(quote: Dict[str, Any]) -> MetadataContent:
    """Route, lines, interchanges and fare, with Book / passengers / other routes actions."""
    pax = int(quote.get("passengers", 1) or 1)
    origin = str(quote.get("origin_station"))
    dest = str(quote.get("destination_station"))
    interchanges = quote.get("interchanges") or []

    badges = [_badge(line, "info") for line in _lines(quote)[:3]]
    badges.append(_badge("Direct" if not interchanges else f"{len(interchanges)} interchange{'s' if len(interchanges) > 1 else ''}",
                         "success" if not interchanges else "warning"))
    t = _travel_time(quote)
    if t:
        badges.append(_badge(t))

    legs = quote.get("route_legs") or []
    leg_items = []
    for leg in legs:
        name, _, span = leg.partition(":")
        leg_items.append(ListItem(children=[_text(name.strip(), "emphasis"), _text(span.strip().replace("->", "→") or leg, "muted")]))

    children: list = [
        HeadingNode(type="heading", value=f"{origin} → {dest}", level=2),
        _row(*badges),
        DividerNode(type="divider"),
    ]
    if leg_items:
        children.append(ListNode(type="list", items=leg_items))
        children.append(DividerNode(type="divider"))
    stations = quote.get("stations_count")
    children += [
        _field("Fare", f"{inr(quote.get('total_fare_inr'))} · {fet(quote.get('total_fare_fet'))}"),
        _field("Passengers", str(pax)),
    ]
    if stations:
        children.append(_field("Stations", str(stations)))
    children += [
        _text("Weekday DMRC fare. Paid in FET on the Fetch.ai testnet; your ticket is issued after the payment is verified on chain.", "muted"),
        _row(
            _button(f"Book · {inr(quote.get('total_fare_inr'))}", {"action": "book_route", "origin": origin, "destination": dest, "passengers": pax}, primary=True),
            _button("Passengers", {"action": "choose_pax", "origin": origin, "destination": dest}),
            _button("Other routes", {"action": "show_routes"}),
        ),
    ]
    return _custom(SectionNode(type="section", children=children))


def passengers_card(origin: str, dest: str, unit_fare_inr: Any) -> MetadataContent:
    try:
        unit = float(unit_fare_inr)
    except (TypeError, ValueError):
        unit = 0.0
    buttons = [
        _button(f"{n} · {inr(unit * n)}" if unit else str(n), {"action": "book_route", "origin": origin, "destination": dest, "passengers": n}, primary=(n == 1))
        for n in (1, 2, 3, 4)
    ]
    return _custom(SectionNode(
        type="section",
        children=[
            HeadingNode(type="heading", value="How many passengers?", level=3),
            _text(f"{origin} → {dest}", "muted"),
            _row(*buttons),
        ],
    ))


def payment_card(quote: Dict[str, Any], network_label: str, pay_to: str, amount_fet: str) -> MetadataContent:
    """Shown while a booking waits for payment. Works with or without ASI:One's native Approve widget."""
    pax = int(quote.get("passengers", 1) or 1)
    return _custom(SectionNode(
        type="section",
        children=[
            HeadingNode(type="heading", value="Pay to get your ticket", level=2),
            _row(_badge("Awaiting payment", "warning"), _badge(network_label, "info")),
            _field("Journey", f"{quote.get('origin_station')} → {quote.get('destination_station')}"),
            _field("Passengers", str(pax)),
            _field("Amount", f"{amount_fet} FET ({inr(quote.get('total_fare_inr'))})"),
            DividerNode(type="divider"),
            _text("Option 1: tap Approve on the FET payment request from this agent.", "emphasis"),
            _text(f"Option 2: from any Fetch wallet on {network_label}, send exactly {amount_fet} FET to the address below, then tap I've paid.", "emphasis"),
            _text(pay_to, "muted"),
            _text("Nothing is booked until the transfer is confirmed on chain. Your QR ticket appears here right after.", "muted"),
            _row(
                _button("I've paid · check", {"action": "check_payment"}, primary=True),
                _button("Resend request", {"action": "retry_payment"}),
                _button("Cancel", {"action": "cancel_booking"}),
            ),
        ],
    ))


def ticket_card(ticket: Dict[str, Any], qr_image_url: str, explorer_url: str) -> MetadataContent:
    pax = int(ticket.get("passengers", 1) or 1)
    tx = str(ticket.get("payment_tx_hash", ""))
    return _custom(SectionNode(
        type="section",
        children=[
            _row(_badge("Confirmed", "success"), _badge(f"{pax} passenger{'s' if pax > 1 else ''}", "info")),
            HeadingNode(type="heading", value=f"{ticket.get('origin_name')} → {ticket.get('destination_name')}", level=2),
            ImageNode(type="image", src=qr_image_url, alt="Ticket QR code", aspect_ratio="1:1"),
            _field("Order", str(ticket.get("order_id"))),
            _field("Paid", f"{fet(ticket.get('total_fare_fet'))} ({inr(ticket.get('total_fare_inr'))})"),
            _field("Valid until", str(ticket.get("valid_until", "")).replace("T", " ").replace("Z", " UTC")),
            _field("Payment tx", f"{tx[:10]}…{tx[-6:]}" if len(tx) > 20 else tx),
            _text(f"Explorer: {explorer_url}", "muted"),
            DividerNode(type="divider"),
            _row(
                _button("Verify ticket", {"action": "verify_ticket", "order_id": str(ticket.get("order_id"))}, primary=True),
                _button("Simulate entry gate", {"action": "gate_scan", "order_id": str(ticket.get("order_id")), "station": str(ticket.get("origin_code", ""))}),
            ),
        ],
    ))


def qr_image_url(qr_token: str) -> str:
    return f"https://api.qrserver.com/v1/create-qr-code/?size=360x360&margin=12&data={urllib.parse.quote(qr_token)}"


def welcome_carousel() -> MetadataContent:
    items = [
        CarouselItem(
            id=f"route_{i}",
            title=f"{o} → {d}",
            subtitle=line,
            badges=[CarouselBadge(label=tag, variant="info")],
            secondary_text="Tap for the live fare",
            primary_cta=CtaAction(label="Check fare", selection={"action": "quote_route", "origin": o, "destination": d}, primary=True),
        )
        for i, (o, d, line, tag) in enumerate(POPULAR_ROUTES)
    ]
    return create_card_content(
        CarouselCardPayload(title="Popular Delhi Metro routes", subtitle="Or just tell me where you're going", items=items),
        card_id=uuid.uuid4(),
    )


def status_card(title: str, rows: List[tuple], badge: tuple, buttons: List[tuple]) -> MetadataContent:
    """Generic status card: rows of (label, value), one (label, variant) badge, buttons of (label, selection, primary)."""
    children: list = [_row(_badge(*badge)), HeadingNode(type="heading", value=title, level=3)]
    children += [_field(label, value) for label, value in rows]
    if buttons:
        children.append(_row(*[_button(lbl, sel, prim) for lbl, sel, prim in buttons]))
    return _custom(SectionNode(type="section", children=children))
