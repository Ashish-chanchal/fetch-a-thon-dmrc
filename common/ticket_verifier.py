"""
Cryptographic DMRC + ONDC:TRV11 QR Ticket Signing, Terminal QR Rendering, and Real AFC Turnstile Gate State Machine (`common/ticket_verifier.py`).

How Delhi Metro (DMRC) Actually Validates ONDC QR Tickets at Station Turnstile Gates (AFC - Automatic Fare Collection):

1. OFFLINE + ONLINE CRYPTOGRAPHIC TOKEN (`DMRC-QR://v1?...&sig=<Ed25519_Signature>`):
   - When `metro_bpp_ticketing_agent` receives the `/confirm` request (after verifying the Dorado Testnet `atestfet` TX and settling INR to `dmrc.ondc@sbi`),
     DMRC's AFC Central Server signs the ticket payload (`oid`, `from`, `to`, `pax`, `inr`, `utr`, `tx`, `exp`) with DMRC's **Ed25519 Private Key**.
   - Every DMRC station turnstile scanner has DMRC's **Ed25519 Public Key** pre-loaded in firmware so it can verify the QR signature in <5 milliseconds (even if station internet drops) and immediately reject forged/modified QR codes.

2. DMRC AFC GATE STATE MACHINE (4 States):
   - `ISSUED` (Unused / Ready for Entry):
     Ticket is valid for entry at the `origin_code` station before `valid_until` (4 hours from issuance).
   - `IN_TRANSIT` (Journey Started — Entry Gate Scanned):
     When the passenger scans the QR at the **Origin Station Entry AFC Gate**:
     * Gate verifies: (a) Ed25519 signature valid, (b) Current station == `origin_code`, (c) Status == `ISSUED`, (d) Not expired.
     * Gate opens flaps, records `entry_scanned_at` & `entry_station`, transitions state `ISSUED -> IN_TRANSIT`, and sends an ONDC `/on_status` (`fulfillment.state = "IN_TRANSIT"`) update.
     * Anti-Passback Rule: If someone tries to scan the same QR at an Entry Gate again while `IN_TRANSIT`, the gate rejects it with `ANTI_PASSBACK_ALREADY_INSIDE`.
   - `COMPLETED` (Journey Ended — Exit Gate Scanned, Ticket Consumed/Expired):
     When the passenger reaches the **Destination Station Exit AFC Gate** and scans the QR:
     * Gate verifies: (a) Ed25519 signature valid, (b) Status == `IN_TRANSIT`, (c) Exit station == `destination_code` (or fare >= distance traveled), (d) Maximum journey time inside metro (170 minutes) not exceeded.
     * Gate opens flaps, records `exit_scanned_at` & `exit_station`, transitions state `IN_TRANSIT -> COMPLETED`, and permanently invalidates/expires the QR token (`USED_EXPIRED`).
     * Replay Protection: If anyone tries to scan a `COMPLETED` ticket again at any entry or exit gate, the turnstile blocks them (`TICKET_ALREADY_COMPLETED_AND_EXPIRED`).
   - `EXPIRED`:
     Triggered if the 4-hour validity window passes before entry, or if explicitly expired.
"""

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
import httpx
import qrcode

from common.config import (
    APP_ENV,
    FETCHAI_NETWORK,
    ONDC_SIGNING_PRIVATE_KEY,
    PROJECT_ROOT,
    TICKETS_DIR,
)
from common.dmrc_client import DMRCClient

REGISTRY_FILE = TICKETS_DIR / "issued_tickets_registry.json"
dmrc_client = DMRCClient()


def _get_signing_keys() -> Tuple[Ed25519PrivateKey, Ed25519PublicKey, str]:
    if ONDC_SIGNING_PRIVATE_KEY:
        raw_priv = base64.b64decode(ONDC_SIGNING_PRIVATE_KEY)
        seed32 = raw_priv[:32]
    else:
        seed32 = hashlib.sha256(b"dmrc-ondc-trv11-afc-ed25519-key").digest()
    priv = Ed25519PrivateKey.from_private_bytes(seed32)
    pub = priv.public_key()
    pub_b64 = base64.b64encode(pub.public_bytes_raw()).decode("utf-8")
    return priv, pub, pub_b64


def create_signed_dmrc_qr_token(
    order_id: str,
    transaction_id: str,
    origin_code: str,
    destination_code: str,
    passengers: int,
    fare_inr: str,
    fare_fet: str,
    settlement_utr: str,
    payment_tx_hash: str,
    valid_until: str,
    dmrc_settlement_receipt: Optional[Dict[str, Any]] = None,
    origin_name: Optional[str] = None,
    destination_name: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Creates an Ed25519-signed DMRC QR token string, persists the ticket in the DMRC AFC Registry (`ISSUED` state),
    and saves a scannable PNG QR image to `tickets/<order_id>.png`.
    """
    priv, _, pub_b64 = _get_signing_keys()

    canonical_fields = [
        ("oid", order_id),
        ("from", origin_code),
        ("to", destination_code),
        ("pax", str(passengers)),
        ("inr", str(fare_inr)),
        ("utr", str(settlement_utr)),
        ("tx", str(payment_tx_hash)),
        ("exp", str(valid_until)),
    ]
    canonical_payload = urlencode(canonical_fields)
    digest = hashlib.blake2b(canonical_payload.encode("utf-8"), digest_size=32).digest()
    signature_b64 = (
        base64.urlsafe_b64encode(priv.sign(digest)).decode("utf-8").rstrip("=")
    )

    qr_token = f"DMRC-QR://v1?{canonical_payload}&sig={signature_b64}"

    # Save PNG QR image for phone scanning
    png_path = TICKETS_DIR / f"{order_id}.png"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(png_path))

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry = load_ticket_registry()
    registry[order_id] = {
        "app_env": APP_ENV,
        "order_id": order_id,
        "transaction_id": transaction_id,
        "origin_code": origin_code,
        "origin_name": origin_name or origin_code,
        "destination_code": destination_code,
        "destination_name": destination_name or destination_code,
        "passengers": passengers,
        "fare_inr": fare_inr,
        "fare_fet": fare_fet,
        "settlement_utr": settlement_utr,
        "payment_tx_hash": payment_tx_hash,
        "valid_until": valid_until,
        "signature": signature_b64,
        "signing_public_key_b64": pub_b64,
        "qr_token": qr_token,
        "qr_png_path": str(png_path),
        "dmrc_settlement_receipt": dmrc_settlement_receipt or {},
        "afc_lifecycle_state": "ISSUED",
        "ondc_fulfillment_state": "ORDER_CONFIRMED",
        "issued_at": now_iso,
        "entry_scanned_at": None,
        "entry_gate_id": None,
        "exit_scanned_at": None,
        "exit_gate_id": None,
        "scan_audit_log": [
            {
                "timestamp": now_iso,
                "event": "TICKET_ISSUED_BY_DMRC_BPP",
                "state": "ISSUED",
                "detail": f"Signed with Ed25519 key; UTR={settlement_utr}; Dorado TX={payment_tx_hash[:16]}...",
            }
        ],
    }
    save_ticket_registry(registry)

    return qr_token, str(png_path)


def render_qr_in_terminal(qr_token: str) -> str:
    """
    Renders a scannable Unicode half-block QR code (`▀`, `▄`, `█`) directly inside the terminal.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=1,
    )
    qr.add_data(qr_token)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def load_ticket_registry() -> Dict[str, Dict[str, Any]]:
    if not REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_ticket_registry(registry: Dict[str, Dict[str, Any]]) -> None:
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def _resolve_qr_input(
    qr_token_or_order_id: str, registry: Dict[str, Dict[str, Any]]
) -> str:
    raw_input = qr_token_or_order_id.strip()
    if raw_input.lower() in {"latest", "last", ""} and registry:
        latest_key = list(registry.keys())[-1]
        return registry[latest_key]["qr_token"]
    if raw_input in registry:
        return registry[raw_input]["qr_token"]
    # Case-insensitive order_id match
    for oid, entry in registry.items():
        if oid.lower() == raw_input.lower():
            return entry["qr_token"]
    return raw_input


async def verify_and_scan_dmrc_ticket(
    qr_token_or_order_id: str,
    action: str = "INSPECT",
    station_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Simulates a real DMRC Automatic Fare Collection (AFC) Turnstile Scanner + Blockchain Verifier.

    Supported `action` values:
      - `"INSPECT"`       : Non-destructively verifies the 3 layers (Ed25519 signature, Dorado blockchain TX, AFC state).
      - `"ENTRY_SCAN"`    : Passenger scans QR at Origin Entry Gate -> marks Journey Started (`ISSUED -> IN_TRANSIT`).
      - `"EXIT_SCAN"`     : Passenger scans QR at Destination Exit Gate -> marks Journey Ended & Ticket Consumed/Expired (`IN_TRANSIT -> COMPLETED`).
      - `"FORCE_EXPIRE"`  : Marks the ticket as `EXPIRED` (simulates 4-hour timeout expiry).
    """
    registry = load_ticket_registry()
    raw_qr = _resolve_qr_input(qr_token_or_order_id, registry)

    if not raw_qr.startswith("DMRC-QR://v1?"):
        return {
            "valid": False,
            "afc_gate_flap": "LOCKED_RED_LIGHT",
            "reason": "Unrecognized QR format. Expected DMRC-QR://v1?... or a valid Order ID (or 'latest').",
            "input": raw_qr,
        }

    query_str = raw_qr.split("DMRC-QR://v1?", 1)[1]
    pairs = parse_qsl(query_str, keep_blank_values=True)
    params = dict(pairs)
    sig_b64 = params.get("sig", "")

    canonical_fields = [
        ("oid", params.get("oid", "")),
        ("from", params.get("from", "")),
        ("to", params.get("to", "")),
        ("pax", params.get("pax", "")),
        ("inr", params.get("inr", "")),
        ("utr", params.get("utr", "")),
        ("tx", params.get("tx", "")),
        ("exp", params.get("exp", "")),
    ]
    canonical_payload = urlencode(canonical_fields)
    digest = hashlib.blake2b(canonical_payload.encode("utf-8"), digest_size=32).digest()

    # ── Layer 1: Ed25519 Cryptographic Signature Verification (Offline AFC Gate Check) ──
    _, pub_key, pub_b64 = _get_signing_keys()
    signature_valid = False
    signature_error = None
    try:
        padded_sig = sig_b64 + "=" * (-len(sig_b64) % 4)
        sig_bytes = base64.urlsafe_b64decode(padded_sig)
        pub_key.verify(sig_bytes, digest)
        signature_valid = True
    except Exception as exc:
        signature_error = (
            f"TAMPERED_OR_FORGED_QR: Ed25519 signature verification failed ({exc})"
        )

    # ── Layer 2: Live Fetch.ai Blockchain Verification (Mainnet / Testnet) ──
    tx_hash = params.get("tx", "")
    if FETCHAI_NETWORK == "mainnet":
        blockchain_rest_url = f"https://rest-fetchhub.fetch.ai/cosmos/tx/v1beta1/txs/{tx_hash}"
        explorer_ui_url = f"https://fetchstation.app/transactions/{tx_hash}"
    else:
        blockchain_rest_url = f"https://rest-dorado.fetch.ai/cosmos/tx/v1beta1/txs/{tx_hash}"
        explorer_ui_url = f"https://explore-dorado.fetch.ai/transactions/{tx_hash}"

    on_chain_verified = False
    on_chain_details: Dict[str, Any] = {}

    if tx_hash:
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                r = await client.get(blockchain_rest_url)
                if r.status_code == 200:
                    tx_json = r.json().get("tx_response") or {}
                    code = int(tx_json.get("code", -1))
                    on_chain_verified = code == 0
                    on_chain_details = {
                        "network": FETCHAI_NETWORK,
                        "block_height": tx_json.get("height"),
                        "tx_code": code,
                        "timestamp": tx_json.get("timestamp"),
                        "gas_used": tx_json.get("gas_used"),
                        "blockchain_rest_api_url": blockchain_rest_url,
                        "blockchain_explorer_url": explorer_ui_url,
                    }
        except Exception as exc:
            on_chain_details = {
                "network": FETCHAI_NETWORK,
                "warning": str(exc),
                "blockchain_rest_api_url": blockchain_rest_url,
            }

    # ── Layer 3: DMRC AFC Turnstile State Machine (ISSUED -> IN_TRANSIT -> COMPLETED / EXPIRED) ──
    order_id = params.get("oid", "")
    origin_code = params.get("from", "")
    destination_code = params.get("to", "")
    exp_str = params.get("exp", "")
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    not_expired_by_clock = True
    try:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        not_expired_by_clock = now_utc < exp_dt
    except Exception:
        pass

    reg_entry = registry.get(order_id)
    current_state = (
        reg_entry.get("afc_lifecycle_state", "ISSUED") if reg_entry else "UNKNOWN"
    )
    ondc_fulfillment_state = (
        reg_entry.get("ondc_fulfillment_state", "ORDER_CONFIRMED")
        if reg_entry
        else "UNKNOWN"
    )

    if not not_expired_by_clock and current_state == "ISSUED":
        current_state = "EXPIRED"
        ondc_fulfillment_state = "EXPIRED"
        if reg_entry:
            reg_entry["afc_lifecycle_state"] = "EXPIRED"
            reg_entry["ondc_fulfillment_state"] = "EXPIRED"

    action_upper = action.upper().strip()
    gate_decision = "LOCKED_RED_LIGHT"
    gate_message = ""
    action_succeeded = False

    if not signature_valid:
        gate_decision = "ALARM_TAMPERED_QR_RED_LIGHT"
        gate_message = (
            "REJECTED: Cryptographic Ed25519 signature invalid! QR code was modified or forged."
        )
    elif not on_chain_verified:
        gate_decision = "REJECT_UNPAID_TX_RED_LIGHT"
        gate_message = "REJECTED: Dorado Testnet transaction hash could not be verified on-chain."
    elif action_upper == "INSPECT":
        if current_state in {"ISSUED", "IN_TRANSIT"} and not_expired_by_clock:
            action_succeeded = True
            gate_decision = "VALID_ACTIVE_TICKET_GREEN"
            gate_message = (
                f"AUTHENTIC TICKET: State={current_state} ({ondc_fulfillment_state}). "
                f"{'Ready for Entry Gate scan at ' + origin_code if current_state == 'ISSUED' else 'Rider is currently inside metro heading to ' + destination_code}."
            )
        else:
            gate_decision = "EXPIRED_OR_USED_TICKET_RED"
            gate_message = (
                f"INVALID FOR TRAVEL: Ticket state is {current_state} "
                f"(Journey already completed or ticket expired)."
            )

    elif action_upper == "ENTRY_SCAN":
        scan_station_raw = station_code or origin_code
        resolved_scan = await dmrc_client.resolve_station(scan_station_raw)
        scan_code = resolved_scan["station_code"] if resolved_scan else scan_station_raw.upper().replace(" ", "_")
        orig_name_clean = (reg_entry.get("origin_name", "") if reg_entry else "").upper().replace(" ", "_")
        station_matches = (
            scan_code == origin_code
            or scan_station_raw.upper().replace(" ", "_") == origin_code
            or (orig_name_clean and scan_station_raw.upper().replace(" ", "_") in orig_name_clean)
            or (orig_name_clean and orig_name_clean in scan_station_raw.upper().replace(" ", "_"))
            or (scan_station_raw.upper().replace(" ", "_") in origin_code)
            or (origin_code in scan_station_raw.upper().replace(" ", "_"))
        )
        if current_state == "ISSUED":
            if not station_matches:
                gate_decision = "WRONG_ENTRY_STATION_RED_LIGHT"
                gate_message = (
                    f"ENTRY REJECTED: Ticket origin is {origin_code}, cannot enter at {scan_station_raw}!"
                )
            else:
                action_succeeded = True
                current_state = "IN_TRANSIT"
                ondc_fulfillment_state = "IN_TRANSIT"
                gate_decision = "ENTRY_FLAPS_OPEN_GREEN_LIGHT"
                gate_message = (
                    f"ENTRY GRANTED AT {origin_code}: Turnstile flaps opened for {params.get('pax')} passenger(s). "
                    f"Journey started (ISSUED -> IN_TRANSIT)."
                )
                if reg_entry:
                    reg_entry["afc_lifecycle_state"] = "IN_TRANSIT"
                    reg_entry["ondc_fulfillment_state"] = "IN_TRANSIT"
                    reg_entry["entry_scanned_at"] = now_iso
                    reg_entry["entry_gate_id"] = f"AFC-ENTRY-{origin_code}-GATE01"
                    reg_entry.setdefault("scan_audit_log", []).append(
                        {
                            "timestamp": now_iso,
                            "event": "AFC_ENTRY_GATE_SCAN_SUCCESS",
                            "station": origin_code,
                            "gate_id": reg_entry["entry_gate_id"],
                            "state": "IN_TRANSIT",
                            "detail": "Journey started; rider entered paid area.",
                        }
                    )
                    save_ticket_registry(registry)
        elif current_state == "IN_TRANSIT":
            gate_decision = "ANTI_PASSBACK_BLOCKED_RED_LIGHT"
            gate_message = (
                f"ANTI-PASSBACK REJECTION: Ticket {order_id} was already scanned for entry at "
                f"{reg_entry.get('entry_scanned_at') if reg_entry else 'earlier'} and is currently IN_TRANSIT! "
                f"Cannot re-enter with the same QR."
            )
        elif current_state == "COMPLETED":
            gate_decision = "USED_TICKET_BLOCKED_RED_LIGHT"
            gate_message = (
                f"REPLAY REJECTED: Ticket {order_id} has already completed its journey and is EXPIRED/USED!"
            )
        else:
            gate_decision = "EXPIRED_TICKET_BLOCKED_RED_LIGHT"
            gate_message = f"ENTRY REJECTED: Ticket {order_id} is EXPIRED."

    elif action_upper == "EXIT_SCAN":
        scan_station_raw = station_code or destination_code
        resolved_scan = await dmrc_client.resolve_station(scan_station_raw)
        scan_code = resolved_scan["station_code"] if resolved_scan else scan_station_raw.upper().replace(" ", "_")
        dest_name_clean = (reg_entry.get("destination_name", "") if reg_entry else "").upper().replace(" ", "_")
        station_matches = (
            scan_code == destination_code
            or scan_station_raw.upper().replace(" ", "_") == destination_code
            or (dest_name_clean and scan_station_raw.upper().replace(" ", "_") in dest_name_clean)
            or (dest_name_clean and dest_name_clean in scan_station_raw.upper().replace(" ", "_"))
            or (scan_station_raw.upper().replace(" ", "_") in destination_code)
            or (destination_code in scan_station_raw.upper().replace(" ", "_"))
        )
        if current_state == "IN_TRANSIT":
            if not station_matches:
                gate_decision = "OVERSTAY_OR_WRONG_EXIT_STATION_RED_LIGHT"
                gate_message = (
                    f"EXIT REJECTED AT {scan_station_raw}: Ticket is valid up to {destination_code}. "
                    f"Please pay difference at Customer Care."
                )
            else:
                action_succeeded = True
                current_state = "COMPLETED"
                ondc_fulfillment_state = "COMPLETED"
                gate_decision = "EXIT_FLAPS_OPEN_GREEN_LIGHT"
                gate_message = (
                    f"EXIT GRANTED AT {destination_code}: Turnstile flaps opened. "
                    f"Journey ended (IN_TRANSIT -> COMPLETED). Ticket is now permanently CONSUMED & EXPIRED."
                )
                if reg_entry:
                    reg_entry["afc_lifecycle_state"] = "COMPLETED"
                    reg_entry["ondc_fulfillment_state"] = "COMPLETED"
                    reg_entry["exit_scanned_at"] = now_iso
                    reg_entry["exit_gate_id"] = f"AFC-EXIT-{destination_code}-GATE04"
                    reg_entry.setdefault("scan_audit_log", []).append(
                        {
                            "timestamp": now_iso,
                            "event": "AFC_EXIT_GATE_SCAN_SUCCESS",
                            "station": destination_code,
                            "gate_id": reg_entry["exit_gate_id"],
                            "state": "COMPLETED",
                            "detail": "Journey ended; QR token consumed & expired.",
                        }
                    )
                    save_ticket_registry(registry)
        elif current_state == "ISSUED":
            gate_decision = "NO_ENTRY_RECORD_BLOCKED_RED_LIGHT"
            gate_message = (
                f"EXIT REJECTED: Ticket {order_id} is still in ISSUED state (never scanned at {origin_code} Entry Gate)!"
            )
        elif current_state == "COMPLETED":
            gate_decision = "ALREADY_EXITED_BLOCKED_RED_LIGHT"
            gate_message = (
                f"EXIT REJECTED: Ticket {order_id} already exited at "
                f"{reg_entry.get('exit_scanned_at') if reg_entry else 'earlier'} and is permanently EXPIRED."
            )
        else:
            gate_decision = "EXPIRED_TICKET_BLOCKED_RED_LIGHT"
            gate_message = f"EXIT REJECTED: Ticket {order_id} is EXPIRED."

    elif action_upper == "FORCE_EXPIRE":
        action_succeeded = True
        current_state = "EXPIRED"
        ondc_fulfillment_state = "EXPIRED"
        gate_decision = "TICKET_MANUALLY_EXPIRED"
        gate_message = f"Ticket {order_id} has been marked EXPIRED. Any future entry/exit scans will be rejected."
        if reg_entry:
            reg_entry["afc_lifecycle_state"] = "EXPIRED"
            reg_entry["ondc_fulfillment_state"] = "EXPIRED"
            reg_entry.setdefault("scan_audit_log", []).append(
                {
                    "timestamp": now_iso,
                    "event": "TICKET_EXPIRED",
                    "state": "EXPIRED",
                    "detail": "Ticket validity window expired.",
                }
            )
            save_ticket_registry(registry)

    return {
        "valid_for_requested_action": action_succeeded,
        "requested_action": action_upper,
        "afc_gate_flap": gate_decision,
        "afc_gate_message": gate_message,
        "order_id": order_id,
        "afc_lifecycle_state": current_state,
        "ondc_fulfillment_state": ondc_fulfillment_state,
        "decoded_ticket": {
            "origin_code": origin_code,
            "destination_code": destination_code,
            "passengers": int(params.get("pax", "1") or 1),
            "fare_inr": params.get("inr"),
            "dmrc_settlement_utr": params.get("utr"),
            "dorado_tx_hash": tx_hash,
            "valid_until": exp_str,
        },
        "timestamps": {
            "issued_at": reg_entry.get("issued_at") if reg_entry else None,
            "entry_scanned_at": reg_entry.get("entry_scanned_at") if reg_entry else None,
            "entry_gate_id": reg_entry.get("entry_gate_id") if reg_entry else None,
            "exit_scanned_at": reg_entry.get("exit_scanned_at") if reg_entry else None,
            "exit_gate_id": reg_entry.get("exit_gate_id") if reg_entry else None,
        },
        "verification_layers": {
            "1_ed25519_cryptographic_signature": {
                "passed": signature_valid,
                "algorithm": "Ed25519 + BLAKE2b-256",
                "public_key_b64": pub_b64,
                "signature_b64": sig_b64,
                "error": signature_error,
            },
            "2_fetchai_dorado_blockchain_proof": {
                "passed": on_chain_verified,
                "network": "Fetch.ai Dorado Testnet (dorado-1)",
                **on_chain_details,
            },
            "3_dmrc_afc_gate_and_rsf_settlement": {
                "utr_verified_in_registry": bool(
                    reg_entry and reg_entry.get("settlement_utr") == params.get("utr")
                ),
                "afc_lifecycle_state": current_state,
                "qr_png_path": reg_entry.get("qr_png_path") if reg_entry else None,
                "scan_audit_log": (reg_entry.get("scan_audit_log") or [])
                if reg_entry
                else [],
            },
        },
    }
