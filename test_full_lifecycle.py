"""
Complete Live End-to-End Walkthrough Test (`test_full_lifecycle.py`):
Tests all 7 stages of a real Delhi Metro ONDC:TRV11 + ASI:One + Dorado Testnet ticket lifecycle:
  Stage 1: Natural-language booking via Live ASI:One API (`asi1-mini`) + Live Delhi OTD API (`VehiclePositions.pb`) + Dorado Testnet (`atestfet`) + DMRC RSF 2.0 UPI UTR.
  Stage 2: Render the scannable Ed25519-signed QR code in the terminal & verify PNG file creation.
  Stage 3: `/verify` (INSPECT) — 3-layer validation (Ed25519 signature + Live Dorado Testnet blockchain query + AFC status = ISSUED).
  Stage 4: `/entry` (ENTRY_SCAN) — Scan QR at Origin Station Entry Gate -> Journey Started (`ISSUED -> IN_TRANSIT`).
  Stage 5: Anti-Passback Test — Try scanning the same QR at the Entry Gate a second time -> Blocked (`ANTI_PASSBACK_BLOCKED_RED_LIGHT`).
  Stage 6: `/exit` (EXIT_SCAN) — Scan QR at Destination Station Exit Gate -> Journey Ended & Ticket Consumed/Expired (`IN_TRANSIT -> COMPLETED`).
  Stage 7: Replay & Tamper Tests — Try reusing the completed ticket (`ALREADY_EXITED_BLOCKED_RED_LIGHT`) and try modifying the QR payload (`ALARM_TAMPERED_QR_RED_LIGHT`).
"""

import asyncio
import os
import sys

# Parse --env / -e CLI flag before loading config module
for i, arg in enumerate(sys.argv):
    if arg in ("--env", "-e") and i + 1 < len(sys.argv):
        os.environ["APP_ENV"] = sys.argv[i + 1].lower().strip()
        break

import httpx

import common.config as cfg
from chat_cli import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    MAGENTA,
    RESET,
    YELLOW,
    print_afc_scan_report,
    start_embedded_bureau,
)
from common.ticket_verifier import render_qr_in_terminal, verify_and_scan_dmrc_ticket


async def run_full_demo() -> None:
    print(
        f"\n{BOLD}{CYAN}🚀 Starting 3-Agent uAgents Bureau [{cfg.APP_ENV.upper()}] + Fetch.ai {cfg.FETCHAI_NETWORK.capitalize()} Wallets...{RESET}"
    )
    bureau_task = await start_embedded_bureau()

    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
        # ── STAGE 1: Book Ticket via Natural Language (ASI:One API + Live OTD + Dorado Testnet) ──
        prompt = "Book 3 metro tickets from Hauz Khas to Rajiv Chowk"
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 1: Natural-Language Booking via ASI:One (`asi1-mini`) ━━━{RESET}")
        print(f"{BOLD}User Prompt >{RESET} {GREEN}\"{prompt}\"{RESET}")

        chat_resp = await client.post(
            "http://localhost:8000/metro/chat",
            json={"prompt": prompt, "city_code": "std:011"},
        )
        data = chat_resp.json()
        qr_token = data.get("ticket_qr_token") or ""
        receipt = data.get("dmrc_settlement_receipt") or {}

        print(f"\n{BOLD}{GREEN}ASI:One Metro Agent ({data.get('status')}) >{RESET}")
        print(data.get("asi1_assistant_reply"))

        # ── STAGE 2: Render Scannable QR Code in Terminal ──
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 2: Scannable DMRC QR Ticket in Terminal (Ed25519 Signed) ━━━{RESET}")
        print(render_qr_in_terminal(qr_token))
        print(f"{DIM}• PNG QR File   : {receipt.get('qr_png_path')}{RESET}")
        print(f"{DIM}• Signed Token  : {qr_token}{RESET}")

        # ── STAGE 3: Inspect & Validate Newly Issued Ticket (POST /metro/ticket/verify | action=INSPECT) ──
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 3: Inspect Newly Issued Ticket (`action=INSPECT`) ━━━{RESET}")
        v_resp = await client.post(
            "http://localhost:8000/metro/ticket/verify",
            json={"qr_or_order_id": "latest", "action": "INSPECT"},
        )
        print_afc_scan_report(v_resp.json())

        # ── STAGE 4: Scan at Origin Station Entry Gate -> Mark Journey Started (ISSUED -> IN_TRANSIT) ──
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 4: Scan QR at Hauz Khas Entry Gate (`action=ENTRY_SCAN` -> Journey Started) ━━━{RESET}")
        entry_resp = await client.post(
            "http://localhost:8000/metro/ticket/verify",
            json={"qr_or_order_id": "latest", "action": "ENTRY_SCAN", "station_code": "HAUZ_KHAS"},
        )
        print_afc_scan_report(entry_resp.json())

        # ── STAGE 5: Anti-Passback Check (Try Scanning at Entry Gate Again While Already Inside) ──
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 5: Anti-Passback Security Check (Second `ENTRY_SCAN` While IN_TRANSIT) ━━━{RESET}")
        passback_resp = await client.post(
            "http://localhost:8000/metro/ticket/verify",
            json={"qr_or_order_id": "latest", "action": "ENTRY_SCAN", "station_code": "HAUZ_KHAS"},
        )
        print_afc_scan_report(passback_resp.json())

        # ── STAGE 6: Scan at Destination Station Exit Gate -> Mark Journey Ended & Ticket Expired (IN_TRANSIT -> COMPLETED) ──
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 6: Scan QR at Rajiv Chowk Exit Gate (`action=EXIT_SCAN` -> Journey Ended & Ticket Expired) ━━━{RESET}")
        exit_resp = await client.post(
            "http://localhost:8000/metro/ticket/verify",
            json={"qr_or_order_id": "latest", "action": "EXIT_SCAN", "station_code": "RAJIV_CHOWK"},
        )
        print_afc_scan_report(exit_resp.json())

        # ── STAGE 7: Replay & Tamper Security Checks ──
        print(f"\n{BOLD}{YELLOW}━━━ STAGE 7A: Replay Check (Try Scanning Completed/Expired Ticket Again) ━━━{RESET}")
        replay_resp = await client.post(
            "http://localhost:8000/metro/ticket/verify",
            json={"qr_or_order_id": "latest", "action": "EXIT_SCAN", "station_code": "RAJIV_CHOWK"},
        )
        print_afc_scan_report(replay_resp.json())

        print(f"\n{BOLD}{YELLOW}━━━ STAGE 7B: Cryptographic Tamper Check (Change `pax=3` -> `pax=9` in QR String) ━━━{RESET}")
        tampered_qr = qr_token.replace("pax=3", "pax=9")
        tamper_resp = await client.post(
            "http://localhost:8000/metro/ticket/verify",
            json={"qr_or_order_id": tampered_qr, "action": "ENTRY_SCAN"},
        )
        print_afc_scan_report(tamper_resp.json())

    bureau_task.cancel()


if __name__ == "__main__":
    asyncio.run(run_full_demo())
