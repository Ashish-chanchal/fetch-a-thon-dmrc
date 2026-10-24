# Multi-Agent Metro Ticket Booking System (`uAgents` + `ONDC:TRV11` + Live Delhi OTD API)

Production-ready Multi-Agent Metro Ticket Booking System built on the **Fetch.ai `uAgents` Framework** (`uagents>=0.25.5`), **ONDC Mobility (`ONDC:TRV11` v2.1.0)** signed payloads, the **Live Delhi Government Open Transit Database (OTD) Real-Time API** (`VehiclePositions.pb`), and **Fetch.ai Dorado Testnet (`atestfet`) $\rightarrow$ DMRC RSF 2.0 (`INR`)** settlement.

> **Zero Static Files**: This repository uses **zero local static GTFS files**. All transit telemetry (`~5,300+` live vehicles and `~1,307` active routes) is fetched in real time from `https://otd.delhi.gov.in/api/realtime/VehiclePositions.pb` using an API key from `OTD_DELHI_API_KEY`, complemented by the live Delhi Metro station graph API (`https://dmrc-rest-api.vercel.app/api/v1/dmrc`).

---

## 1. Multi-Agent Architecture

The system coordinates three autonomous agents running concurrently in a unified [Fetch.ai Bureau](file:///Users/ashishchanchal/mts/metro_booking_agents.py):

| Agent | Dev Agent Address | Dorado Wallet Address | Protocols | REST Endpoints / Interfaces |
| :--- | :--- | :--- | :--- | :--- |
| **`metro_bap_coordinator`** ([agents/coordinator_agent.py](file:///Users/ashishchanchal/mts/agents/coordinator_agent.py)) | `agent1qvka2ph5m5xljedx3pzdajz8ulveu40lrrhnwhmvgn0xy3r42vte7w8ly54` | `fetch1s8end3ghttvcjcmwtlgwpspadjf90hlqcgmsty` | `ONDC_TRV11_LiveBAPProtocol`<br>`AgentChatProtocol` (ASI:One)<br>`AgentPaymentProtocol` (`role="buyer"`) | `POST /metro/chat`<br>`POST /metro/book`<br>`POST /metro/ticket/verify`<br>Live ASI:One Chat (`dmrcbook`) |
| **`metro_bpp_catalog_agent`** ([agents/catalog_agent.py](file:///Users/ashishchanchal/mts/agents/catalog_agent.py)) | `agent1qdym6kv3062msawu6gm7ys7gapw5lzf49myhx0jxaj6g8yp7tzt7jhu4ns0` | `fetch1nw4z9alwy3snwtjmzvs9ph5s9pd9xrdgg5l5qe` | `ONDC_TRV11_LiveCatalogProtocol` | `GET /metro/stations`<br>`POST /beckn/on_search`<br>`POST /beckn/on_select` |
| **`metro_bpp_ticketing_agent`** ([agents/ticketing_agent.py](file:///Users/ashishchanchal/mts/agents/ticketing_agent.py)) | `agent1qd78ral8gqhf3nv5s2a6fw9jswg6y9uwpz5vsuxuyn7h2eq32hpaccvt3cp` | `fetch1u56cps6c0za0h8sfutpmezd8kqew9me5mh8cdx` | `ONDC_TRV11_LiveTicketingProtocol`<br>`AgentPaymentProtocol` (`role="seller"`) | `POST /beckn/on_init`<br>`POST /beckn/on_confirm` |

---

## 2. Dual-Rail Settlement Architecture (FET $\rightarrow$ INR via DMRC RSF 2.0)

When a passenger books a ticket via **ASI:One Web Chat**, **Terminal CLI**, or `POST /metro/book`, settlement occurs across **two coupled rails**:

1. **Rail 1 — On-Chain `FET` Settlement on Fetch.ai Dorado Testnet (`AgentPaymentProtocol`)**:
   - `metro_bpp_ticketing_agent` prepares the order and sends a `RequestPayment` message (`0.01 FET` per ₹1 INR fare) to `metro_bap_coordinator`.
   - `metro_bap_coordinator` executes an on-chain `atestfet` transfer (`ctx.ledger.send_tokens`) on the **Fetch.ai Dorado Testnet** and replies with `CommitPayment(transaction_id=tx_hash)`.
   - `metro_bpp_ticketing_agent` queries the live Dorado blockchain via `ctx.ledger.query_tx(tx_hash).is_successful()` and confirms payment receipt.

2. **Rail 2 — BAP Nodal Bank Account $\rightarrow$ DMRC BPP Bank Account (`INR` via ONDC RSF 2.0)**:
   - `metro_bpp_ticketing_agent` reads DMRC's `SETTLEMENT_TERMS` (`Delhi Metro Rail Corporation Ltd`, VPA `dmrc.ondc@sbi`, SBI A/C `10891234567`, IFSC `SBIN0000691`).
   - Immediately following on-chain blockchain verification, `ONDCTRV11Client.execute_dmrc_inr_settlement` remits the exact INR fare from the **BAP Nodal Account** (`YES BANK` / `bap.metro-agent@yesbank`) to **DMRC's BPP Account** (`dmrc.ondc@sbi`) via `INR_PAYOUT_WEBHOOK_URL` (or simulated RSF 2.0 clearing) and records the **12-digit NPCI UPI UTR**.
   - `metro_bpp_ticketing_agent` dispatches a signed `ONDC:TRV11` `POST /confirm` with `status="PAID"`, `collected_by="BAP"`, DMRC bank parameters, and `SETTLEMENT_REFERENCE=<12-digit UTR>`.
   - A cryptographic, Ed25519-signed QR code (`DMRC-QR://v1?...`) is generated and delivered with the official DMRC settlement receipt.

---

## 2b. Rider-Paid FET Flow and Agentverse Mailbox Deployment

**Who pays whom.** The coordinator is the *seller* in the Agent Payment Protocol and the rider is the *buyer*:
1. After the quote, the coordinator sends the rider a `RequestPayment` (`payment_method="fet_direct"`, recipient = coordinator wallet, `fet_network` metadata).
2. The rider approves in ASI:One and pays from their own wallet; the UI replies with `CommitPayment` (tx hash + `buyer_fet_wallet`).
3. `common/fet_payment.py` reads the tx from the Fetch.ai REST API and requires a successful transfer with the matching sender, recipient and at least the fare. A tx hash can be redeemed once.
4. Only then does the ticketing agent issue the ticket. REST callers (`/metro/book`, CLI) have no wallet, so the coordinator wallet pays on their behalf in dev tooling.

**Mailbox deployment (no ngrok).** Set `ENABLE_MAILBOX=true`, run `python metro_booking_agents.py`, open the Inspector link in the logs, click **Connect -> Mailbox**, then restart. Use private random seeds in `.env`; the repo defaults are public. `scripts/register_agentverse.py` is only needed for the ngrok endpoint mode.

---

## 3. Multi-Environment Switching (`dev` | `test` | `prod`)

You can switch the network topology, payment rails, and gate rules across **Development**, **Staging/Test**, and **Production** using any of these methods:

1. **In `.env`**: Set `APP_ENV="dev"`, `APP_ENV="test"`, or `APP_ENV="prod"`.
2. **Via shell environment variable**: `export APP_ENV=test` (or `APP_ENV=prod python ...`).
3. **Via CLI flag**: `python chat_cli.py --env test` (or `python test_full_lifecycle.py --env dev`).

### Environment Matrix & Real-World Behaviors

| Feature / Rail | `dev` (Local Development) | `test` (Staging / QA) | `prod` (Production Real-World) |
| :--- | :--- | :--- | :--- |
| **Config Overrides File** | [`.env.dev`](file:///Users/ashishchanchal/mts/.env.dev) | [`.env.test`](file:///Users/ashishchanchal/mts/.env.test) | [`.env.prod`](file:///Users/ashishchanchal/mts/.env.prod) |
| **ONDC Gateway URL** | `http://localhost:3000` (auto-sandbox) | `https://staging.gateway.ondc.org` | `https://prod.gateway.ondc.org` |
| **BPP Registry ID** | `bpp.sandbox.ondc.org` | `dmrc-bpp.staging.ondc.org` | `dmrc-bpp.ondc.org` |
| **Local Beckn Sandbox** | ✅ Auto-boot on port 3001/3002 | ⚠️ Disabled (Staging ONDC network) | ⛔ Strictly Forbidden (Production Gate) |
| **Fetch.ai Network** | `testnet` (Dorado) | `testnet` (Dorado) | `mainnet` (Fetch-hub-4) |
| **Blockchain Token** | `atestfet` (Free testnet faucet) | `atestfet` | `afet` (Real mainnet FET tokens) |
| **DMRC Bank Settlement** | Simulated RSF 2.0 + Deterministic NPCI UTR | Staging Webhook / Simulated UTR | Real Bank/Payout API (RazorpayX / Cashfree / Nodal API) |
| **Ticket Storage** | `tickets/dev/` | `tickets/test/` | `tickets/prod/` |
| **Production Validation Gate** | Permissive (fast local iteration) | Staging verification | Strict (validates HSM keys, non-empty webhook, mainnet balances) |

---

## 4. Installation & Environment Setup

### Method A: Fast Setup with `uv` (Recommended)
```bash
# 1. Create a virtual environment with Python 3.12
uv venv .venv --python 3.12

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Install dependencies
uv pip install -r requirements.txt
```

### Method B: Standard Python Setup (`python3`)
```bash
# 1. Create virtual environment
python3 -m venv .venv

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. Complete Testing Guide (4 Ways to Test)

```
                                  TESTING MODES
  ┌───────────────────────┬──────────────────────┬──────────────────────┬──────────────────────┐
  │  1. Live ASI:One Chat │ 2. Terminal Chat CLI │ 3. 7-Stage Auto Test │  4. REST API / cURL  │
  │   (asi1.ai UI Cards)  │ (ASCII QR + Gates)   │ (Single-Command E2E) │ (Programmatic Hooks) │
  └───────────────────────┴──────────────────────┴──────────────────────┴──────────────────────┘
```

---

### Option 1: Live ASI:One Web Chat (`https://asi1.ai/chat`)

Experience the conversational multi-agent workflow on Fetch.ai's official [ASI:One Chat platform](https://asi1.ai/chat), featuring dynamic route cards, real-time fare estimates, 1-click booking, high-resolution QR rendering, and turnstile gate triggers.

#### Step 1: Start your public ngrok tunnel
The Fetch.ai Almanac and ASI:One forward user chat messages to your coordinator agent via HTTP webhook. Open a dedicated terminal:
```bash
ngrok http 8000
# Or if you have a reserved ngrok domain:
ngrok http 8000 --url https://<your-subdomain>.ngrok-free.dev
```
* Note your public forwarding URL (e.g., `https://bullheadedly-noncrustaceous-abdul.ngrok-free.dev`).

> [!IMPORTANT]
> ### 📍 Where to Update Your Ngrok URL
> Whenever your ngrok URL changes (or on initial setup), update **`AGENT_ENDPOINT`** in your [`.env`](file:///Users/ashishchanchal/mts/.env) file:
> ```properties
> # In .env (Line 69)
> AGENT_ENDPOINT="https://bullheadedly-noncrustaceous-abdul.ngrok-free.dev"
> ```
> * **Automatic `/submit` Append**: You can paste the base URL (with or without trailing `/submit`). [metro_booking_agents.py](file:///Users/ashishchanchal/mts/metro_booking_agents.py) automatically appends `/submit` and publishes `https://<your-url>/submit` to the Almanac contracts.
> * **How it flows through the code**:
>   1. [`.env`](file:///Users/ashishchanchal/mts/.env) $\rightarrow$ read by [common/config.py](file:///Users/ashishchanchal/mts/common/config.py) as `cfg.AGENT_PUBLIC_ENDPOINT`.
>   2. [metro_booking_agents.py](file:///Users/ashishchanchal/mts/metro_booking_agents.py) injects it as the Bureau public endpoint: `bureau_endpoint = [f"{public_url}/submit"]`.
>   3. [scripts/register_agentverse.py](file:///Users/ashishchanchal/mts/scripts/register_agentverse.py) uses it to sync your registered chat agent profile with Agentverse.
> * **Restart requirement**: After updating [`.env`](file:///Users/ashishchanchal/mts/.env), restart `metro_booking_agents.py` so the agents announce the new endpoint to the Almanac.

#### Step 2: Launch the Multi-Agent Bureau
In a second terminal, launch the bureau:
```bash
source .venv/bin/activate
python metro_booking_agents.py
```
You should see:
```text
INFO: [metro_bap_coordinator]: Starting agent with address: agent1qvka2ph5m5xljedx3pzdajz8ulveu40lrrhnwhmvgn0xy3r42vte7w8ly54
INFO: [bureau]: Starting server on http://0.0.0.0:8000
INFO: [bureau]: Batch registration on Almanac API successful
INFO: [metro_bap_coordinator]: Manifest published successfully: AgentChatProtocol
INFO: [metro_bap_coordinator]: Agent registration status updated to active
```

> **Syncing with Agentverse**: If you started a brand-new ngrok session with a new URL and want to immediately update your registered agent record on the Agentverse directory, run:
> ```bash
> python scripts/register_agentverse.py
> ```

#### Step 3: Open ASI:One and Chat with the Agent
1. Navigate to **[https://asi1.ai/chat](https://asi1.ai/chat)**.
2. Search for or tag the Coordinator Agent:
   ```text
   @agent1qvka2ph5m5xljedx3pzdajz8ulveu40lrrhnwhmvgn0xy3r42vte7w8ly54
   ```
   *(Or search by agent name: `dmrcbook`)*

#### Step 4: Step-by-Step Test Scenarios to Try in Chat

| Scenario | What to Send in Chat | Expected Agent Behavior & UI Elements |
| :--- | :--- | :--- |
| **A. Welcome & Discovery** | `"hey"` or `"hello"` | Personalized greeting + **Delhi Metro Quick Book** carousel with popular routes (e.g. Kashmere Gate $\rightarrow$ Rajiv Chowk, Hauz Khas $\rightarrow$ Botanical Garden). |
| **B. Real-Time Fare & Route Query** | `"Check fare from Millennium City Centre Gurugram to Kashmere Gate"` | Calls live DMRC API + OTD API; renders **Tailored Journey Card** showing:<br>• Origin & Destination stations<br>• Station count (e.g., 27 stations)<br>• Line & journey time (~58 mins)<br>• Exact fare in INR (₹60) and FET (0.60 FET)<br>• Interactive **`[⚡ Book Ticket]`** CTA button. |
| **C. Contextual Inquiries (No Carousel Spam)** | `"How many interchanges are there?"` or `"Which lines does it cross?"` | Natural conversational answer explaining the direct transit path without losing context, while keeping the specific booking card pinned (does **not** revert to generic carousels). |
| **D. One-Click / Affirmative Booking** | Click **`[⚡ Book Ticket]`** or type `"yes please book it"` | Automatically triggers:<br>1. ONDC `/init`<br>2. On-chain Dorado Testnet `0.60 FET` transfer<br>3. DMRC RSF 2.0 INR remittance with 12-digit NPCI UTR<br>4. ONDC `/confirm` ticket emission. |
| **E. In-Chat QR Code & Gate Actions** | *(Automatic after booking)* | Renders a scannable **High-Resolution QR Code** directly in the chat feed (via CDN, bypassing ngrok interstitial warnings) along with 3 interactive action buttons:<br>• **`[🚪 Simulate Gate Entry Scan]`** — Tests origin turnstile gate entry.<br>• **`[🔍 Verify On-Chain Ticket]`** — Inspects Ed25519 signature + Dorado testnet TX.<br>• **`[📥 Download Ticket (PNG)]`** — Direct download link for the ticket image. |

---

### Option 2: Interactive Terminal Chat CLI (`python chat_cli.py`)

For fast local testing without a browser, run the conversational CLI with live terminal ASCII QR rendering and turnstile commands:

```bash
python chat_cli.py
```

* **Conversational Booking**: Type plain English requests:
  ```text
  💬 You > Book 2 tickets from Rajiv Chowk to Hauz Khas
  ```
* **Instant Terminal QR Art**: Displays the full Ed25519-signed QR code directly in your terminal, and writes a clean PNG to `tickets/<ORDER_ID>.png`.
* **Built-in DMRC Turnstile Gate Scanner**:
  * `/verify latest` — Validates cryptographic Ed25519 signature, Fetch.ai Dorado testnet blockchain TX status, and AFC lifecycle state.
  * `/entry latest` — Scans ticket at origin entry turnstile (`ISSUED` $\rightarrow$ `IN_TRANSIT` with green gate flap).
  * `/entry latest` (again) — **Anti-Passback Security Check**: verifies gate rejects duplicate entry while already inside station.
  * `/exit latest` — Scans ticket at destination exit turnstile (`IN_TRANSIT` $\rightarrow$ `COMPLETED`).
  * `/tamper latest` — Forges the ticket payload to verify turnstile anti-tamper alarms.
  * `/status` — Displays active wallet balances and station count.
  * `/help` — Lists all available commands.

---

### Option 3: Single-Command Automated Lifecycle Test (`python test_full_lifecycle.py`)

Runs a fully autonomous end-to-end test validating all 7 real-world lifecycle stages in under 15 seconds:

```bash
python test_full_lifecycle.py
```

#### What It Tests Automatically:
1. **Stage 1: Natural-Language Booking**: Sends conversational prompt to `asi1-mini`, queries live Delhi OTD telemetry, computes fare, and executes Dorado blockchain + DMRC RSF 2.0 dual settlement.
2. **Stage 2: QR Code Generation**: Validates Ed25519 signing and confirms PNG image generation.
3. **Stage 3: `/verify` (INSPECT)**: Performs non-destructive 3-layer validation (crypto signature + Dorado testnet TX hash + AFC status = `ISSUED`).
4. **Stage 4: `/entry` (ENTRY_SCAN)**: Scans ticket at origin station turnstile $\rightarrow$ transitions state from `ISSUED` to `IN_TRANSIT` (`afc_gate_flap="OPEN_GREEN_LIGHT"`).
5. **Stage 5: Anti-Passback Test**: Attempts to scan the same QR code at the entry gate again $\rightarrow$ blocked (`afc_gate_flap="LOCKED_RED_LIGHT"`, `ANTI_PASSBACK_BLOCKED_RED_LIGHT`).
6. **Stage 6: `/exit` (EXIT_SCAN)**: Scans QR code at destination station turnstile $\rightarrow$ transitions state from `IN_TRANSIT` to `COMPLETED`.
7. **Stage 7: Replay & Tamper Attacks**:
   - **Replay**: Attempts to use expired/completed ticket at exit gate $\rightarrow$ blocked (`ALREADY_EXITED_BLOCKED_RED_LIGHT`).
   - **Tamper**: Modifies ticket payload (`pax=3` $\rightarrow$ `pax=9`) $\rightarrow$ signature validation fails (`ALARM_TAMPERED_QR_RED_LIGHT`).

To run across different environment profiles:
```bash
python test_full_lifecycle.py --env dev
python test_full_lifecycle.py --env test
```

---

### Option 4: Direct REST API & cURL Testing

Launch the background bureau:
```bash
python metro_booking_agents.py
```

Then test any endpoint directly from another terminal:

#### 1. Query Live DMRC Stations & Delhi OTD Vehicle Positions
```bash
curl -s http://localhost:8000/metro/stations | python3 -m json.tool | head -n 35
```
*Returns all 254 active metro stations, live GPS coordinates, interchange indicators, and real-time transit telemetry count.*

#### 2. Conversational Booking & Intent AI (`POST /metro/chat`)
```bash
curl -s -X POST http://localhost:8000/metro/chat \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Check fare from Kashmere Gate to Rajiv Chowk",
    "city_code": "std:011"
  }' | python3 -m json.tool
```

#### 3. Direct Programmatic Booking (`POST /metro/book`)
```bash
curl -s -X POST http://localhost:8000/metro/book \
  -H "Content-Type: application/json" \
  -d '{
    "origin_code": "KASHMERE_GATE",
    "destination_code": "RAJIV_CHOWK",
    "passengers": 2,
    "city_code": "std:011",
    "rider_name": "Test Passenger",
    "rider_phone": "+919876543210"
  }' | python3 -m json.tool
```
*Returns confirmed order ID, Ed25519 QR token, Dorado Testnet payment TX hash, and 12-digit NPCI UPI UTR.*

#### 4. DMRC AFC Gate Turnstile Simulator (`POST /metro/ticket/verify`)
```bash
# A. Non-destructive inspection of latest ticket:
curl -s -X POST http://localhost:8000/metro/ticket/verify \
  -H "Content-Type: application/json" \
  -d '{"qr_or_order_id": "latest", "action": "INSPECT"}' | python3 -m json.tool

# B. Scan at Entry Gate (ISSUED -> IN_TRANSIT):
curl -s -X POST http://localhost:8000/metro/ticket/verify \
  -H "Content-Type: application/json" \
  -d '{"qr_or_order_id": "latest", "action": "ENTRY_SCAN", "station_code": "KASHMERE_GATE"}' | python3 -m json.tool

# C. Scan at Exit Gate (IN_TRANSIT -> COMPLETED):
curl -s -X POST http://localhost:8000/metro/ticket/verify \
  -H "Content-Type: application/json" \
  -d '{"qr_or_order_id": "latest", "action": "EXIT_SCAN", "station_code": "RAJIV_CHOWK"}' | python3 -m json.tool
```

---

## 6. Going to Production (Keys & Portal Registration)

To book genuine tickets that open physical Delhi Metro turnstiles, follow these steps:

### 1. Generate Cryptographic Keys & Seeds
Run the built-in production key generator:
```bash
python scripts/generate_production_keys.py
```
This generates:
* **ONDC Ed25519 Signing Keypair** (Private key for [`.env.prod`](file:///Users/ashishchanchal/mts/.env.prod), Public key for ONDC Registry).
* **ONDC X25519 Encryption Keypair** (For Beckn payload decryption).
* **High-entropy Fetch.ai Mainnet Agent Seeds**.

### 2. Verify Production Readiness
Audit your [`.env.prod`](file:///Users/ashishchanchal/mts/.env.prod) anytime to verify which production rails are configured:
```bash
python scripts/verify_prod_readiness.py
```

### 3. Production Portal Registration Checklist
1. **ONDC Production Registry**: Register at [portal.ondc.org](https://portal.ondc.org) to obtain your `ONDC_BAP_ID` and upload your Ed25519 Public Key.
2. **Fiat Bank Payout Webhook**: Set up an account on [RazorpayX](https://razorpay.com/x/) or [Cashfree Payouts](https://www.cashfree.com/payouts/) and paste the webhook URL & API key into `INR_PAYOUT_WEBHOOK_URL` & `INR_PAYOUT_API_KEY`.
3. **Fetch.ai Mainnet Wallet**: Fund your coordinator wallet with real FET on the Fetch-hub-4 mainnet.
4. **Delhi OTD API**: Register on [otd.delhi.gov.in](https://otd.delhi.gov.in/) if you need to regenerate your API key.
5. **ASI:One AI Engine**: API key is managed via [api.asi1.ai](https://api.asi1.ai/).
