"""
Interactive Terminal Chat CLI (`chat_cli.py`) for the Multi-Agent Metro Ticket Booking System.

Capabilities:
- Connects to a running 3-agent Bureau on `http://localhost:8000` if already running,
  OR automatically boots the 3-agent Bureau (`metro_bap_coordinator`, `metro_bpp_catalog_agent`,
  `metro_bpp_ticketing_agent`) + local ONDC:TRV11 Beckn Sandbox in the background.
- Renders a real scannable QR code directly inside the terminal after every ticket booking,
  and saves a high-resolution PNG QR image in `tickets/<ORDER_ID>.png`.
- Includes an interactive DMRC Automatic Fare Collection (AFC) Turnstile Scanner & Cryptographic Validator:
  * `/verify [order_id_or_qr]` : 3-layer inspection (Ed25519 signature + Live Dorado Testnet blockchain TX + AFC state)
  * `/entry [order_id_or_qr]`  : Scan QR at Origin Station Entry Gate -> Marks Journey Started (`ISSUED -> IN_TRANSIT`)
  * `/exit [order_id_or_qr]`   : Scan QR at Destination Station Exit Gate -> Marks Journey Ended & Ticket Consumed/Expired (`IN_TRANSIT -> COMPLETED`)
  * `/expire [order_id_or_qr]` : Force-expire a ticket (`EXPIRED`)
  * `/tamper [order_id_or_qr]` : Simulate a forged/modified QR scan to watch DMRC's Ed25519 signature check catch the tampering
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

# Parse --env / -e CLI flag before loading config module
for i, arg in enumerate(sys.argv):
    if arg in ("--env", "-e") and i + 1 < len(sys.argv):
        os.environ["APP_ENV"] = sys.argv[i + 1].lower().strip()
        break

import httpx

import common.config as cfg
from common.ticket_verifier import render_qr_in_terminal, verify_and_scan_dmrc_ticket

# ANSI terminal styling
BOLD = "\033[1m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
DIM = "\033[2m"
RESET = "\033[0m"


async def is_server_running(base_url: str = "http://localhost:8000") -> bool:
    """Checks if the 3-agent Bureau REST server is already listening on port 8000."""
    try:
        async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
            resp = await client.get(f"{base_url}/metro/stations")
            return resp.status_code == 200
    except Exception:
        return False


async def start_embedded_bureau() -> asyncio.Task:
    """Starts the 3-agent Bureau in-process with quiet logging so the interactive terminal stays clean."""
    logging.getLogger().setLevel(logging.ERROR)
    for logger_name in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "httpx",
        "metro_bap_coordinator",
        "metro_bpp_catalog_agent",
        "metro_bpp_ticketing_agent",
        "bureau",
        "dispenser",
    ):
        logging.getLogger(logger_name).setLevel(logging.ERROR)

    from agents.catalog_agent import catalog_agent
    from agents.coordinator_agent import coordinator
    from agents.ticketing_agent import ticketing_agent
    from metro_booking_agents import create_metro_bureau

    running_loop = asyncio.get_running_loop()
    for ag in (coordinator, catalog_agent, ticketing_agent):
        ag._loop = running_loop
        ag._logger.setLevel(logging.ERROR)

    bureau = create_metro_bureau(port=8000, loop=running_loop)
    bureau._logger.setLevel(logging.ERROR)
    task = asyncio.create_task(bureau.run_async())

    for _ in range(30):
        if await is_server_running():
            break
        await asyncio.sleep(0.5)
    return task


def print_banner(
    live_vehicles: Optional[int] = None, active_routes: Optional[int] = None
) -> None:
    env_name = cfg.APP_ENV.upper()
    if cfg.APP_ENV == "prod":
        env_badge = f"{RED}{BOLD}[ENVIRONMENT: PRODUCTION 🚀 (Live ONDC Network & Real Payouts)]{RESET}"
    elif cfg.APP_ENV == "test":
        env_badge = f"{YELLOW}{BOLD}[ENVIRONMENT: STAGING / TEST 🧪 (Pre-Prod ONDC Gateway)]{RESET}"
    else:
        env_badge = f"{CYAN}{BOLD}[ENVIRONMENT: DEVELOPMENT 🛠️ (Local Sandbox & Simulated RSF 2.0)]{RESET}"

    print(
        f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════════════════╗{RESET}"
    )
    print(
        f"{BOLD}{CYAN}║   🚇  ASI:One × ONDC:TRV11 × Fetch.ai uAgents Metro Booking & AFC Terminal   ║{RESET}"
    )
    print(
        f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════════════════════╝{RESET}"
    )
    print(f"  {env_badge}")
    print(
        f"{DIM}• Active Gateway & BPP     : {cfg.ONDC_GATEWAY_URL} | BPP: {cfg.ONDC_DEFAULT_BPP_ID}{RESET}"
    )
    print(
        f"{DIM}• Blockchain Ledger        : Fetch.ai {cfg.FETCHAI_NETWORK.capitalize()} (`{cfg.FETCHAI_DENOM}`) | Payout: {cfg.INR_PAYOUT_PROVIDER}{RESET}"
    )
    if live_vehicles is not None:
        print(
            f"{DIM}• Live Delhi OTD Telemetry : {GREEN}{live_vehicles:,} active vehicles{RESET}{DIM} across {active_routes:,} routes{RESET}"
        )
    print(
        f"{DIM}• DMRC & ONDC Transit APIs : {GREEN}Live Official DMRC REST API{RESET}{DIM} + ONDC:TRV11 Protocol (100% Dynamic){RESET}"
    )
    print(
        f"{DIM}• Ticket Storage Directory : {cfg.TICKETS_DIR}{RESET}"
    )
    print(
        f"\n{YELLOW}Booking & DMRC AFC Turnstile Gate Commands:{RESET}\n"
        f"  1. {BOLD}Book by chatting{RESET}  : {GREEN}\"Book 2 metro tickets from Kashmere Gate to Rajiv Chowk\"{RESET}\n"
        f"  2. {BOLD}/verify [id|qr]{RESET}   : Validate ticket authenticity (Ed25519 sig + blockchain TX + AFC state)\n"
        f"  3. {BOLD}/entry  [id|qr]{RESET}   : Scan QR at Origin Entry Gate -> Mark {GREEN}Journey Started (IN_TRANSIT){RESET}\n"
        f"  4. {BOLD}/exit   [id|qr]{RESET}   : Scan QR at Destination Exit Gate -> Mark {CYAN}Journey Ended & Expired (COMPLETED){RESET}\n"
        f"  5. {BOLD}/expire [id|qr]{RESET}   : Force-expire ticket ({RED}EXPIRED{RESET})\n"
        f"  6. {BOLD}/tamper [id|qr]{RESET}   : Test scanning a forged/modified QR (shows Ed25519 cryptographic rejection)\n"
        f"  7. {BOLD}/stations{RESET}         : Fetch live Delhi OTD vehicle count | {BOLD}/json{RESET} : Toggle raw JSON | {BOLD}exit{RESET} : Quit\n"
    )


def print_afc_scan_report(res: Dict[str, Any]) -> None:
    ok = res.get("valid_for_requested_action", False)
    flap = res.get("afc_gate_flap", "")
    msg = res.get("afc_gate_message", "")
    state = res.get("afc_lifecycle_state", "")
    ondc_state = res.get("ondc_fulfillment_state", "")
    decoded = res.get("decoded_ticket") or {}
    layers = res.get("verification_layers") or {}
    sig_layer = layers.get("1_ed25519_cryptographic_signature") or {}
    chain_layer = layers.get("2_fetchai_dorado_blockchain_proof") or {}
    afc_layer = layers.get("3_dmrc_afc_gate_and_rsf_settlement") or {}
    ts = res.get("timestamps") or {}

    color = GREEN if ok else RED
    icon = "🟢 [GATE OPEN]" if ok else "🔴 [GATE BLOCKED]"

    print(
        f"\n{BOLD}{color}╔══ DMRC AFC TURNSTILE SCANNER & CRYPTOGRAPHIC VALIDATOR ══════════════════════╗{RESET}"
    )
    print(f"  {BOLD}Gate Decision{RESET}       : {color}{BOLD}{icon} {flap}{RESET}")
    print(f"  {BOLD}Gate Message{RESET}        : {msg}")
    print(
        f"  {BOLD}Order ID / Route{RESET}    : {res.get('order_id')} | {decoded.get('origin_code')} → {decoded.get('destination_code')} ({decoded.get('passengers')} pax, ₹{decoded.get('fare_inr')})"
    )
    print(
        f"  {BOLD}Ticket Lifecycle{RESET}    : {BOLD}{YELLOW}{state}{RESET} (ONDC Fulfillment: {ondc_state})"
    )
    print(
        f"\n  {BOLD}{CYAN}[Layer 1] Ed25519 Cryptographic Signature Check (Offline AFC Turnstile){RESET}"
    )
    print(
        f"    • Status          : {GREEN + 'PASSED (Authentic DMRC BPP Signature)' + RESET if sig_layer.get('passed') else RED + 'FAILED — ' + str(sig_layer.get('error')) + RESET}"
    )
    print(f"    • Public Key      : {sig_layer.get('public_key_b64')}")
    print(
        f"\n  {BOLD}{CYAN}[Layer 2] Fetch.ai Dorado Testnet Blockchain Proof (Live Ledger Query){RESET}"
    )
    print(
        f"    • Status          : {GREEN + 'VERIFIED ON-CHAIN (code=0)' + RESET if chain_layer.get('passed') else RED + 'FAILED' + RESET}"
    )
    print(
        f"    • Block / TX Hash : Block #{chain_layer.get('block_height')} | TX={decoded.get('dorado_tx_hash')}"
    )
    print(f"    • Explorer URL    : {chain_layer.get('dorado_explorer_url')}")
    print(
        f"\n  {BOLD}{CYAN}[Layer 3] DMRC AFC Gate State Machine & ONDC RSF 2.0 Settlement{RESET}"
    )
    print(
        f"    • NPCI UPI UTR    : {decoded.get('dmrc_settlement_utr')} (Registry Match: {afc_layer.get('utr_verified_in_registry')})"
    )
    print(
        f"    • Timestamps      : Issued={ts.get('issued_at')} | Entry={ts.get('entry_scanned_at') or 'Not Scanned'} | Exit={ts.get('exit_scanned_at') or 'Not Scanned'}"
    )
    audit_log = afc_layer.get("scan_audit_log") or []
    if audit_log:
        print(f"    • Audit Trail     :")
        for ev in audit_log:
            print(
                f"        - [{ev.get('timestamp')}] {BOLD}{ev.get('event')}{RESET} -> state={ev.get('state')} ({ev.get('detail')})"
            )
    print(
        f"{BOLD}{color}╚══════════════════════════════════════════════════════════════════════════════╝{RESET}\n"
    )


async def interactive_chat_loop() -> None:
    bureau_task: Optional[asyncio.Task] = None
    already_running = await is_server_running()

    if not already_running:
        print(
            f"{YELLOW}⏳ Starting 3-agent uAgents Bureau (BAP Coordinator + BPP Catalog + BPP Ticketing) & Dorado Testnet wallets...{RESET}"
        )
        bureau_task = await start_embedded_bureau()
    else:
        print(
            f"{GREEN}✓ Connected to active 3-agent Bureau on http://localhost:8000{RESET}"
        )

    live_vehicles = None
    active_routes = None
    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
        try:
            st_resp = await client.get("http://localhost:8000/metro/stations")
            if st_resp.status_code == 200:
                st_data = st_resp.json()
                live_vehicles = st_data.get("realtime_active_vehicles")
                active_routes = st_data.get("total_routes")
        except Exception:
            pass

        print_banner(live_vehicles=live_vehicles, active_routes=active_routes)

        show_raw_json = False
        chat_history = []
        loop = asyncio.get_running_loop()

        while True:
            try:
                user_input = await loop.run_in_executor(
                    None, lambda: input(f"{BOLD}{CYAN}You > {RESET}").strip()
                )
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}Exiting chat...{RESET}")
                break

            if not user_input:
                continue

            cmd_parts = user_input.split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else "latest"

            if cmd in {"exit", "quit", "/exit", "/quit", "q"}:
                print(f"{DIM}Goodbye! Safe travels on Delhi Metro.{RESET}")
                break

            if cmd == "/json":
                show_raw_json = not show_raw_json
                state_str = "ENABLED" if show_raw_json else "DISABLED"
                print(f"{YELLOW}Full JSON receipt output is now {state_str}.{RESET}\n")
                continue

            if cmd == "/stations":
                print(f"{DIM}Fetching live Delhi OTD vehicle positions...{RESET}")
                st_resp = await client.get("http://localhost:8000/metro/stations")
                st_data = st_resp.json()
                print(
                    f"\n{GREEN}Live Delhi OTD Telemetry:{RESET}\n"
                    f"  • Active Vehicles right now : {BOLD}{st_data.get('realtime_active_vehicles')}{RESET}\n"
                    f"  • Active Transit Routes     : {BOLD}{st_data.get('total_routes')}{RESET}\n"
                )
                continue

            # ── DMRC AFC Gate Scanning & Validation Commands ──
            if cmd in {"/verify", "/entry", "/exit", "/expire", "/tamper"}:
                if cmd == "/tamper":
                    from common.ticket_verifier import (
                        _resolve_qr_input,
                        load_ticket_registry,
                    )

                    reg = load_ticket_registry()
                    orig_qr = _resolve_qr_input(arg, reg)
                    tampered_qr = (
                        orig_qr.replace("pax=1", "pax=9")
                        .replace("pax=2", "pax=9")
                        .replace("pax=3", "pax=9")
                    )
                    if tampered_qr == orig_qr:
                        tampered_qr = orig_qr.replace("inr=", "inr=1")
                    print(
                        f"{YELLOW}⚠️ Simulating tampered QR scan (modified passenger count inside QR without DMRC's Ed25519 private key)...{RESET}"
                    )
                    res = await verify_and_scan_dmrc_ticket(
                        tampered_qr, action="ENTRY_SCAN"
                    )
                    print_afc_scan_report(res)
                    continue

                action_map = {
                    "/verify": "INSPECT",
                    "/entry": "ENTRY_SCAN",
                    "/exit": "EXIT_SCAN",
                    "/expire": "FORCE_EXPIRE",
                }
                res = await verify_and_scan_dmrc_ticket(
                    arg, action=action_map[cmd]
                )
                print_afc_scan_report(res)
                continue

            print(f"{DIM}🤖 ASI:One (`asi1-mini`) thinking...{RESET}")

            try:
                resp = await client.post(
                    "http://localhost:8000/metro/chat",
                    json={
                        "prompt": user_input,
                        "city_code": "std:011",
                        "history": chat_history,
                    },
                )
                data = resp.json()
            except Exception as exc:
                print(
                    f"{RED}Error communicating with coordinator agent: {exc}{RESET}\n"
                )
                continue

            status = data.get("status", "UNKNOWN")
            parsed = (data.get("asi1_llm_metadata") or {}).get("parsed_intent") or {}
            reply = data.get("asi1_assistant_reply") or ""
            receipt = data.get("dmrc_settlement_receipt") or {}
            qr_token = data.get("ticket_qr_token") or ""

            # Append turn to conversation memory
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": reply})

            # If it's a conversational turn (e.g. "hey", "I want to book a ticket", "from Hauz Khas"), just print the AI reply!
            if status == "CHAT" or not qr_token:
                print(f"\n{BOLD}{GREEN}ASI:One Metro Agent >{RESET} {reply}\n")
                if show_raw_json:
                    print(f"{DIM}{json.dumps(data, indent=2)}{RESET}\n")
                continue

            # Once a real booking was triggered and confirmed, reset pending booking history for next trip and print QR + Proof
            chat_history.clear()

            print(f"\n{BOLD}{GREEN}ASI:One Metro Agent ({status}) >{RESET}")
            print(reply)

            print(
                f"\n{BOLD}{CYAN}─── DMRC Scannable AFC Gate QR Ticket (Ed25519 Cryptographically Signed) ───{RESET}"
            )
            print(render_qr_in_terminal(qr_token))
            png_path = receipt.get("qr_png_path")
            if png_path:
                print(f"  {DIM}Saved high-res PNG QR to: {png_path}{RESET}")

            print(
                f"{BOLD}{MAGENTA}─── Verification & Settlement Proof ───────────────────────────────────────{RESET}"
            )
            print(
                f"  • {BOLD}Parsed by asi1-mini{RESET} : {parsed.get('origin_code')} → {parsed.get('destination_code')} ({parsed.get('passengers')} pax)"
            )
            print(
                f"  • {BOLD}ONDC Transaction ID{RESET} : {data.get('transaction_id')}"
            )
            print(
                f"  • {BOLD}Dorado Testnet TX{RESET}   : {data.get('payment_tx_hash')} ({data.get('quote_fet')} FET)"
            )
            print(
                f"  • {BOLD}DMRC UPI UTR (INR){RESET}  : {receipt.get('settlement_reference_utr')} (₹{data.get('quote_inr')} → {receipt.get('destination_dmrc_bpp_account', {}).get('upi_address')})"
            )
            print(f"  • {BOLD}Signed QR Payload{RESET}   : {CYAN}{qr_token}{RESET}")
            print(
                f"  • {BOLD}Next Step in Chat{RESET}   : Type {GREEN}/verify{RESET}, {GREEN}/entry{RESET} (start journey), {GREEN}/exit{RESET} (end journey & expire), or {YELLOW}/tamper{RESET}"
            )
            print(
                f"{BOLD}{MAGENTA}───────────────────────────────────────────────────────────────────────────{RESET}\n"
            )

            if show_raw_json:
                print(f"{DIM}{json.dumps(data, indent=2)}{RESET}\n")

    if bureau_task is not None:
        bureau_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(interactive_chat_loop())
    except KeyboardInterrupt:
        sys.exit(0)
