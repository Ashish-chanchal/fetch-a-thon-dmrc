"""Shared uAgents Model schemas for inter-agent communication and REST endpoints."""

from typing import Any, Dict, List, Optional
from uagents import Model


# ============================================================================
# REST Endpoint Models
# ============================================================================


class MetroBookRESTRequest(Model):
    """Inbound REST POST /metro/book payload for initiating a metro ticket booking."""

    origin_code: str
    destination_code: str
    passengers: int = 1
    city_code: str = "std:011"
    rider_name: Optional[str] = None
    rider_phone: Optional[str] = None
    rider_email: Optional[str] = None


class MetroBookRESTResponse(Model):
    """REST response returned by POST /metro/book."""

    status: str
    transaction_id: str
    origin: Dict[str, Any]
    destination: Dict[str, Any]
    passengers: int
    quote_inr: str
    quote_fet: str
    payment_tx_hash: Optional[str] = None
    dmrc_settlement_receipt: Dict[str, Any] = {}
    ticket_qr_token: Optional[str] = None
    bpp_id: str
    bpp_uri: str
    realtime_active_vehicles: int = 0
    realtime_vehicles_sample: List[Dict[str, Any]] = []
    message: str


class MetroChatRESTRequest(Model):
    """Inbound REST POST /metro/chat payload for natural-language conversation & booking via the ASI:One API (`asi1-mini`)."""

    prompt: str
    city_code: str = "std:011"
    history: List[Dict[str, str]] = []


class MetroChatRESTResponse(Model):
    """REST response returned by POST /metro/chat."""

    status: str
    transaction_id: str
    user_prompt: str
    asi1_llm_metadata: Dict[str, Any] = {}
    asi1_assistant_reply: str
    origin: Dict[str, Any] = {}
    destination: Dict[str, Any] = {}
    passengers: int = 0
    quote_inr: str = "0.00"
    quote_fet: str = "0.0000"
    payment_tx_hash: Optional[str] = None
    dmrc_settlement_receipt: Dict[str, Any] = {}
    ticket_qr_token: Optional[str] = None
    realtime_active_vehicles: int = 0
    realtime_vehicles_sample: List[Dict[str, Any]] = []


class MetroStationsRESTResponse(Model):
    """REST response returned by GET /metro/stations."""

    status: str
    total_stations: int
    total_routes: int
    realtime_active_vehicles: int
    realtime_vehicles_sample: List[Dict[str, Any]] = []
    stations: List[Dict[str, str]]
    routes: List[Dict[str, str]]


class BecknWebhookAckResponse(Model):
    """Standard ONDC Beckn webhook ACK response for /beckn/on_* callbacks."""

    message: Dict[str, Dict[str, str]]


class MetroTicketVerifyRESTRequest(Model):
    """Inbound REST POST /metro/ticket/verify payload for DMRC AFC turnstile scanning & cryptographic validation."""

    qr_or_order_id: str = "latest"
    action: str = "INSPECT"  # "INSPECT", "ENTRY_SCAN", "EXIT_SCAN", or "FORCE_EXPIRE"
    station_code: Optional[str] = None


class MetroTicketVerifyRESTResponse(Model):
    """REST response returned by POST /metro/ticket/verify."""

    valid_for_requested_action: bool
    requested_action: str
    afc_gate_flap: str
    afc_gate_message: str
    order_id: str
    afc_lifecycle_state: str
    ondc_fulfillment_state: str
    decoded_ticket: Dict[str, Any]
    timestamps: Dict[str, Any]
    verification_layers: Dict[str, Any]


# ============================================================================
# Inter-Agent Protocol Models: BAP Coordinator <-> BPP Catalog Agent
# ============================================================================


class CatalogSearchRequest(Model):
    """Sent by metro_bap_coordinator to metro_bpp_catalog_agent to resolve stations and run ONDC /search & /select."""

    transaction_id: str
    origin_query: str
    destination_query: str
    passengers: int = 1
    city_code: str = "std:011"
    requester_sender: Optional[str] = None


class CatalogQuoteResponse(Model):
    """Returned by metro_bpp_catalog_agent to metro_bap_coordinator with resolved stations and ONDC /on_select quote."""

    transaction_id: str
    origin_stop_id: str
    origin_code: str
    origin_name: str
    origin_gps: str
    destination_stop_id: str
    destination_code: str
    destination_name: str
    destination_gps: str
    route_summary: str
    passengers: int
    unit_fare_inr: str
    total_fare_inr: str
    total_fare_fet: str
    item_id: str
    provider_id: str
    fulfillment_id: str
    bpp_id: str
    bpp_uri: str
    realtime_vehicle_count: int
    realtime_vehicles_sample: List[Dict[str, Any]] = []
    beckn_search_Signature: str
    beckn_select_status: str


# ============================================================================
# Inter-Agent Protocol Models: BAP Coordinator <-> BPP Ticketing Agent
# ============================================================================


class TicketingInitRequest(Model):
    """Sent by metro_bap_coordinator to metro_bpp_ticketing_agent to initialize the ONDC order (/init) and trigger payment."""

    transaction_id: str
    origin_code: str
    origin_name: str
    destination_code: str
    destination_name: str
    passengers: int
    item_id: str
    provider_id: str
    fulfillment_id: str
    total_fare_inr: str
    total_fare_fet: str
    bpp_id: str
    bpp_uri: str
    city_code: str = "std:011"
    rider_name: Optional[str] = None
    rider_phone: Optional[str] = None
    rider_email: Optional[str] = None
    payment_tx_hash: str = ""
    payer_wallet: str = ""


class TicketIssuedResponse(Model):
    """Sent by metro_bpp_ticketing_agent to metro_bap_coordinator after on-chain payment verification and ONDC /confirm."""

    transaction_id: str
    order_id: str
    origin_code: str
    origin_name: str
    destination_code: str
    destination_name: str
    passengers: int
    total_fare_inr: str
    total_fare_fet: str
    payment_tx_hash: str
    payment_verified_on_chain: bool
    dmrc_settlement_receipt: Dict[str, Any] = {}
    qr_token: str
    valid_until: str
    bpp_id: str
    status: str
