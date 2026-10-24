"""
metro_bpp_catalog_agent:
- Resolves station names/codes dynamically from official live DMRC REST API (`/stations/search`)
- Calculates official DMRC fares and journey routes dynamically (`/journeys/fare-route`)
- Queries live Delhi OTD GTFS-RT `VehiclePositions.pb` using the authorized API key
- Executes signed ONDC:TRV11 v2.1.0 `POST /search` and `POST /select` requests
- Exposes `POST /beckn/on_search`, `POST /beckn/on_select`, and `GET /metro/stations`
"""

import json
import logging
from typing import Any, Dict, List, Optional

from uagents import Agent, Context, Model, Protocol
from uagents.setup import fund_agent_if_low

from common.config import (
    CATALOG_AGENT_SEED,
    INR_TO_TESTNET_FET,
    ONDC_DEFAULT_BPP_ID,
    ONDC_DEFAULT_BPP_URI,
    ONDC_GATEWAY_URL,
    FETCHAI_NETWORK,
)
from common.dmrc_client import DMRCClient
from common.models import (
    BecknWebhookAckResponse,
    CatalogQuoteResponse,
    CatalogSearchRequest,
    MetroStationsRESTResponse,
)
from common.ondc_client import ONDCTRV11Client
from common.otd_client import OTDTransitClient

logger = logging.getLogger("catalog_agent")


class BecknCallbackPayload(Model):
    """Generic inbound ONDC Beckn webhook payload for /beckn/on_search and /beckn/on_select."""

    context: Dict[str, Any]
    message: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


catalog_agent = Agent(
    name="metro_bpp_catalog_agent",
    seed=CATALOG_AGENT_SEED,
    port=8001,
    endpoint=["http://localhost:8001/submit"],
    network=FETCHAI_NETWORK,
)

ONDC_TRV11_LiveCatalogProtocol = Protocol(
    name="ONDC_TRV11_LiveCatalogProtocol", version="2.1.0"
)

dmrc_client = DMRCClient()
otd_client = OTDTransitClient()
ondc_client = ONDCTRV11Client()

# Stores asynchronous /beckn/on_search and /beckn/on_select callback payloads keyed by transaction_id
ON_SEARCH_CALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}
ON_SELECT_CALLBACK_CACHE: Dict[str, Dict[str, Any]] = {}


@catalog_agent.on_event("startup")
async def on_catalog_startup(ctx: Context) -> None:
    try:
        stations = await dmrc_client.get_all_stations()
        ctx.logger.info(
            f"[metro_bpp_catalog_agent] Address: {catalog_agent.address} | "
            f"Wallet: {catalog_agent.wallet.address()} | "
            f"Loaded {len(stations)} live DMRC stations from DMRC REST API"
        )
    except Exception as exc:
        ctx.logger.warning(f"[DMRC API] Initial station load warning: {exc}")

    try:
        fund_agent_if_low(str(catalog_agent.wallet.address()))
    except Exception as exc:
        ctx.logger.warning(f"Wallet funding check skipped or deferred: {exc}")


@ONDC_TRV11_LiveCatalogProtocol.on_message(
    model=CatalogSearchRequest, replies={CatalogQuoteResponse}
)
async def handle_catalog_search(
    ctx: Context, sender: str, msg: CatalogSearchRequest
) -> None:
    """
    1. Fetches live Delhi OTD GTFS-Realtime VehiclePositions.pb feed.
    2. Resolves origin and destination station codes dynamically from official live DMRC API.
    3. Queries official DMRC Fare & Routing API for exact fare, duration, and line interchanges.
    4. Sends signed ONDC:TRV11 POST /search and POST /select requests to ONDC Gateway/BPP.
    5. Returns CatalogQuoteResponse to metro_bap_coordinator.
    """
    # 1. Fetch live OTD Delhi GTFS-Realtime VehiclePositions
    realtime_vehicles = []
    try:
        realtime_vehicles = await otd_client.fetch_realtime_vehicle_positions()
        ctx.logger.info(
            f"[OTD Live API] Retrieved {len(realtime_vehicles)} active transit vehicles across Delhi"
        )
    except Exception as exc:
        ctx.logger.warning(f"[OTD Live API] VehiclePositions fetch warning: {exc}")

    # 2. Resolve stations dynamically via live DMRC REST API
    dmrc_orig = await dmrc_client.resolve_station(msg.origin_query)
    dmrc_dest = await dmrc_client.resolve_station(msg.destination_query)

    orig_code = dmrc_orig["station_code"] if dmrc_orig else msg.origin_query.strip().upper().replace(" ", "_")
    orig_name = dmrc_orig["station_name"] if dmrc_orig else msg.origin_query.strip()
    dest_code = dmrc_dest["station_code"] if dmrc_dest else msg.destination_query.strip().upper().replace(" ", "_")
    dest_name = dmrc_dest["station_name"] if dmrc_dest else msg.destination_query.strip()

    # 3. Query official DMRC fare and route
    unit_fare = 30.0
    fare_route_data = None
    stations_count = None
    total_time = None
    interchange_info = []

    if dmrc_orig and dmrc_dest:
        try:
            fare_route_data = await dmrc_client.get_fare_and_route(orig_code, dest_code)
            if fare_route_data:
                unit_fare = float(fare_route_data.get("weekday_fare") or 30.0)
                stations_count = fare_route_data.get("stations")
                total_time = fare_route_data.get("total_time")
                orig_name = fare_route_data.get("from") or orig_name
                dest_name = fare_route_data.get("to") or dest_name
                legs = fare_route_data.get("route") or []
                for i in range(len(legs) - 1):
                    interchange_info.append(
                        f"{legs[i].get('end')} ({legs[i].get('line')} -> {legs[i+1].get('line')})"
                    )
                ctx.logger.info(
                    f"[DMRC API] Official fare for {orig_name} -> {dest_name}: INR {unit_fare} "
                    f"({stations_count} stations, time={total_time})"
                )
        except Exception as exc:
            ctx.logger.warning(f"[DMRC API] Fare-route fetch failed: {exc}")

    total_inr = round(unit_fare * max(msg.passengers, 1), 2)
    total_fet = round(total_inr * INR_TO_TESTNET_FET, 4)

    # 4. Build & dispatch signed ONDC:TRV11 POST /search
    search_payload = ondc_client.build_search_payload(
        origin_code=orig_code,
        destination_code=dest_code,
        transaction_id=msg.transaction_id,
        city_code=msg.city_code,
    )
    search_resp: Dict[str, Any] = {}
    search_auth_header = ""
    try:
        search_resp, search_auth_header = await ondc_client.post_beckn_action(
            ONDC_GATEWAY_URL, "search", search_payload
        )
        ctx.logger.info(
            f"[ONDC /search] Dispatched to {ONDC_GATEWAY_URL}/search | Resp: {json.dumps(search_resp)}"
        )
    except Exception as exc:
        raw_search = json.dumps(search_payload, separators=(",", ":"))
        search_auth_header = ondc_client.build_authorization_header(raw_search)
        ctx.logger.warning(f"[ONDC /search] Gateway HTTP warning ({ONDC_GATEWAY_URL}/search): {exc}")

    # 5. Build & dispatch signed ONDC:TRV11 POST /select
    item_id = f"METRO_{orig_code}_{dest_code}"
    provider_id = "DMRC_PROVIDER_01"
    fulfillment_id = "FULFILLMENT_METRO_1"

    select_payload = ondc_client.build_select_payload(
        transaction_id=msg.transaction_id,
        provider_id=provider_id,
        item_id=item_id,
        fulfillment_id=fulfillment_id,
        passengers=msg.passengers,
        bpp_id=ONDC_DEFAULT_BPP_ID,
        bpp_uri=ONDC_DEFAULT_BPP_URI,
        city_code=msg.city_code,
    )
    select_resp: Dict[str, Any] = {}
    select_status = "ACK"
    try:
        select_resp, _ = await ondc_client.post_beckn_action(
            ONDC_DEFAULT_BPP_URI, "select", select_payload
        )
        select_status = (
            ((select_resp.get("message") or {}).get("ack") or {}).get("status")
            or "ACK"
        )
        ctx.logger.info(
            f"[ONDC /select] Dispatched to {ONDC_DEFAULT_BPP_URI}/select | Status: {select_status}"
        )
    except Exception as exc:
        select_status = f"FALLBACK_QUOTE ({type(exc).__name__})"
        ctx.logger.warning(
            f"[ONDC /select] BPP HTTP warning ({ONDC_DEFAULT_BPP_URI}/select): {exc}"
        )

    # Build rich route summary
    summary_parts = [f"{orig_name} ({orig_code}) -> {dest_name} ({dest_code})"]
    if stations_count:
        summary_parts.append(f"{stations_count} stations")
    if total_time:
        summary_parts.append(f"~{total_time}")
    if interchange_info:
        summary_parts.append(f"Interchange: {', '.join(interchange_info)}")
    route_summary = " | ".join(summary_parts)

    await ctx.send(
        sender,
        CatalogQuoteResponse(
            transaction_id=msg.transaction_id,
            origin_stop_id=orig_code,
            origin_code=orig_code,
            origin_name=orig_name,
            origin_gps="",
            destination_stop_id=dest_code,
            destination_code=dest_code,
            destination_name=dest_name,
            destination_gps="",
            route_summary=route_summary,
            passengers=msg.passengers,
            unit_fare_inr=f"{unit_fare:.2f}",
            total_fare_inr=f"{total_inr:.2f}",
            total_fare_fet=f"{total_fet:.4f}",
            item_id=item_id,
            provider_id=provider_id,
            fulfillment_id=fulfillment_id,
            bpp_id=ONDC_DEFAULT_BPP_ID,
            bpp_uri=ONDC_DEFAULT_BPP_URI,
            realtime_vehicle_count=len(realtime_vehicles),
            realtime_vehicles_sample=realtime_vehicles[:5],
            beckn_search_Signature=search_auth_header[:96] + "..." if len(search_auth_header) > 96 else search_auth_header,
            beckn_select_status=select_status,
        ),
    )


@catalog_agent.on_rest_post(
    "/beckn/on_search", BecknCallbackPayload, BecknWebhookAckResponse
)
async def handle_beckn_on_search(
    ctx: Context, req: BecknCallbackPayload
) -> BecknWebhookAckResponse:
    """Asynchronous ONDC Beckn webhook for /beckn/on_search."""
    tx_id = str((req.context or {}).get("transaction_id", ""))
    if tx_id:
        ON_SEARCH_CALLBACK_CACHE[tx_id] = req.model_dump()
    ctx.logger.info(f"[Webhook /beckn/on_search] Received callback for transaction_id={tx_id}")
    return BecknWebhookAckResponse(message={"ack": {"status": "ACK"}})


@catalog_agent.on_rest_post(
    "/beckn/on_select", BecknCallbackPayload, BecknWebhookAckResponse
)
async def handle_beckn_on_select(
    ctx: Context, req: BecknCallbackPayload
) -> BecknWebhookAckResponse:
    """Asynchronous ONDC Beckn webhook for /beckn/on_select."""
    tx_id = str((req.context or {}).get("transaction_id", ""))
    if tx_id:
        ON_SELECT_CALLBACK_CACHE[tx_id] = req.model_dump()
    ctx.logger.info(f"[Webhook /beckn/on_select] Received callback for transaction_id={tx_id}")
    return BecknWebhookAckResponse(message={"ack": {"status": "ACK"}})


@catalog_agent.on_rest_get("/metro/stations", MetroStationsRESTResponse)
async def handle_get_metro_stations(ctx: Context) -> MetroStationsRESTResponse:
    """Returns live DMRC stations and live OTD active vehicle telemetry directly from live APIs."""
    vehicles = []
    try:
        vehicles = await otd_client.fetch_realtime_vehicle_positions()
    except Exception as exc:
        ctx.logger.warning(f"[GET /metro/stations] Live OTD vehicle count warning: {exc}")

    dmrc_stations = []
    try:
        raw_stations = await dmrc_client.get_all_stations()
        for s in raw_stations:
            dmrc_stations.append({
                "code": s.get("station_code", ""),
                "name": s.get("station_name", ""),
                "lines": ", ".join([l.get("line_name", "") for l in s.get("metro_lines", [])]),
            })
    except Exception as exc:
        ctx.logger.warning(f"[GET /metro/stations] DMRC stations warning: {exc}")

    routes_list = otd_client.extract_live_routes_from_vehicles(vehicles)
    return MetroStationsRESTResponse(
        status="OK",
        total_stations=len(dmrc_stations),
        total_routes=len(routes_list),
        realtime_active_vehicles=len(vehicles),
        realtime_vehicles_sample=vehicles[:10],
        stations=dmrc_stations[:50],
        routes=routes_list[:50],
    )


catalog_agent.include(ONDC_TRV11_LiveCatalogProtocol, publish_manifest=True)


if __name__ == "__main__":
    catalog_agent.run()
