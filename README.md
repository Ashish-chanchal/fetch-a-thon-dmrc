# Delhi Metro Multi-Agent Ticket Booking System (`metrotickets`)

[![Deployment: Google Cloud Run](https://img.shields.io/badge/Deployment-Google%20Cloud%20Run-4285F4?logo=googlecloud&logoColor=white)](docs/DEPLOYMENT_GCP.md)
[![Container: Docker](https://img.shields.io/badge/Container-Docker%20Python%203.12-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![Framework: Fetch.ai uAgents](https://img.shields.io/badge/uAgents-%3E%3D0.25.5-002D62)](https://fetch.ai)
[![Protocol: ONDC TRV11](https://img.shields.io/badge/Protocol-ONDC%3ATRV11%20v2.1.0-008080)](docs/SYSTEM_ARCHITECTURE.md)
[![Settlement: Fetch.ai Dorado Testnet](https://img.shields.io/badge/Settlement-Dorado%20atestfet-FF6F00)](https://explore-dorado.fetch.ai)
[![Transit Data: Delhi OTD Live](https://img.shields.io/badge/Transit%20Telemetry-Delhi%20OTD%20GTFS--RT-2E7D32)](https://otd.delhi.gov.in)

Autonomous Multi-Agent Metro Ticket Booking System built on the **Fetch.ai `uAgents` Framework**, India's **ONDC Mobility Protocol (`ONDC:TRV11` v2.1.0)**, the **Delhi Government Open Transit Database (OTD) Real-Time API** (`VehiclePositions.pb`), and a **Dual-Rail Crypto-to-Fiat Settlement Engine** (Fetch.ai Dorado Testnet `atestfet` $\rightarrow$ DMRC RSF 2.0 `INR`).

The agent is deployed 24/7 on **Google Cloud Run** under the name **`metrotickets`** inside a Docker container, connected via Agentverse Mailbox mode.

> **Zero Static Files**: This system operates with zero static GTFS timetable files. All route telemetry and transit vehicle counts are streamed live from the Delhi Government OTD API (`VehiclePositions.pb`) and the live Delhi Metro station network API (`dmrc-rest-api.vercel.app`), covering all 254 active metro stations.

---

## Documentation Suite

| Document | Purpose |
| :--- | :--- |
| **[System Architecture](docs/SYSTEM_ARCHITECTURE.md)** | Multi-agent Bureau topology, Beckn protocol sequence, dual-rail settlement mechanics, and Ed25519 turnstile security. |
| **[Google Cloud & Docker Guide](docs/DEPLOYMENT_GCP.md)** | Step-by-step operations guide for containerizing and deploying `metrotickets` on Google Cloud Run with Agentverse Mailbox mode. |
| **[Production Roadmap & Readiness](docs/TAKE_TO_PRODUCTION.md)** | Commercial readiness guide covering ONDC network participant onboarding, RazorpayX fiat payouts, and mainnet migration. |
| **[Agent Profile & Prompts](agent_readme.md)** | User-facing catalog card published to ASI:One and the Fetch.ai Agentverse directory. |

---

## 1. 24/7 Google Cloud Run Deployment (`metrotickets`)

The system is deployed on Google Cloud Run as a containerized Python 3.12 service named **`metrotickets`**.

```
                                    CLOUD RUN DEPLOYMENT
  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │ Google Cloud Run Service: "metrotickets"                                              │
  │                                                                                        │
  │   Docker Container (Python 3.12-slim)                                                  │
  │   ├── Multi-Agent Bureau Process (Port 8080)                                           │
  │   │   ├── metro_bap_coordinator                                                        │
  │   │   ├── metro_bpp_catalog_agent                                                      │
  │   │   └── metro_bpp_ticketing_agent                                                    │
  │   └── Embedded ASGIServer: Serving HTTP Health & Inline QR Image Delivery              │
  └──────────────────────────────────────┬─────────────────────────────────────────────────┘
                                         │
                             Outbound TLS / WSS Connection
                                (ENABLE_MAILBOX=true)
                                         │
                                         ▼
  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │ Fetch.ai Agentverse Mailbox Relay Cluster                                              │
  │ No public ingress IP, tunnels, or port forwarding required.                            │
  └──────────────────────────────────────┬─────────────────────────────────────────────────┘
                                         │
                                         ▼
  ┌────────────────────────────────────────────────────────────────────────────────────────┐
  │ ASI:One Conversational Network  ──>  Rider Interface: @metrotickets                    │
  └────────────────────────────────────────────────────────────────────────────────────────┘
```

### Why Mailbox Mode on Cloud Run?
- **Zero Ingress Vulnerability**: The container initiates an outbound, authenticated WebSocket connection to Agentverse using `AGENTVERSE_KEY`. It does not require public inbound firewall rules or third-party tunneling services (such as ngrok).
- **Persistent Availability**: Configured with `--min-instances=1` and `--timeout=3600`, ensuring the agent never goes to sleep and responds instantly to ASI:One riders.
- **Root-Path Compatibility**: The internal ASGI server automatically maps root health probes (`/`) to `/submit`, satisfying Cloud Run startup and liveness probes.

For detailed deployment instructions, secret management, and IAM configuration, see [docs/DEPLOYMENT_GCP.md](docs/DEPLOYMENT_GCP.md).

---

## 2. Multi-Agent Architecture

Three autonomous agents run cooperatively inside a unified Fetch.ai Bureau process (`metro_booking_agents.py`):

| Agent Identifier | Beckn / ONDC Role | Protocols Implemented | Network Responsibilities |
| :--- | :--- | :--- | :--- |
| **`metro_bap_coordinator`** ([agents/coordinator_agent.py](agents/coordinator_agent.py)) | Buyer App (BAP) Proxy | `ONDC_TRV11_LiveBAPProtocol`<br>`AgentChatProtocol`<br>`AgentPaymentProtocol` (role="seller") | • Manages ASI:One conversational dialog and renders UI cards.<br>• Requests FET payment from rider wallet and verifies tx on Dorado.<br>• Hosts HTTP REST endpoints (`/metro/chat`, `/metro/book`, `/metro/stations`). |
| **`metro_bpp_catalog_agent`** ([agents/catalog_agent.py](agents/catalog_agent.py)) | Seller Platform (BPP) Catalog Provider | `ONDC_TRV11_LiveCatalogProtocol` | • Resolves stations and computes path hops across 254 stations.<br>• Streams real-time transit telemetry from Delhi OTD.<br>• Formats signed Beckn `on_search` and `on_select` catalog payloads. |
| **`metro_bpp_ticketing_agent`** ([agents/ticketing_agent.py](agents/ticketing_agent.py)) | Seller Platform (BPP) Ticketing Provider | `ONDC_TRV11_LiveTicketingProtocol`<br>`AgentPaymentProtocol` (role="buyer") | • Verifies token settlement receipts on-chain.<br>• Remits INR fare to DMRC via RSF 2.0 with NPCI UPI UTR.<br>• Emits Ed25519-signed QR tickets and enforces AFC gate transitions. |

---

## 3. Dual-Rail Settlement Architecture (FET $\rightarrow$ INR via DMRC RSF 2.0)

When a passenger books a ticket via **ASI:One Chat**, **Terminal CLI**, or `POST /metro/book`, settlement occurs across two coupled rails:

```
                          DUAL-RAIL SETTLEMENT WORKFLOW
  
  Rail 1: On-Chain FET Settlement (Fetch.ai Dorado Testnet)
  ┌────────────────┐         RequestPayment (0.01 FET / ₹1)         ┌─────────────────────┐
  │  Rider Wallet  │ <───────────────────────────────────────────── │ metro_bap_          │
  │   (ASI:One)    │ ───────────── CommitPayment (tx_hash) ───────> │ coordinator         │
  └────────────────┘                                                └──────────┬──────────┘
                                                                               │
                                                   Verifies tx on Dorado REST  │
                                                                               ▼
  Rail 2: Fiat Remittance (DMRC RSF 2.0 Clearing)                   ┌─────────────────────┐
  ┌────────────────┐          Exact Fare ₹INR Remittance            │ metro_bpp_          │
  │ DMRC Bank A/C  │ <───────────────────────────────────────────── │ ticketing_agent     │
  │ dmrc.ondc@sbi  │ ──────── 12-digit NPCI UPI Bank UTR ─────────> │                     │
  └────────────────┘                                                └──────────┬──────────┘
                                                                               │
                                                                               ▼
                                                            Ed25519-Signed QR Ticket Issued
```

1. **Rail 1 — On-Chain `FET` Settlement**:
   - The coordinator sends a `RequestPayment` message (`0.01 FET` per ₹1 INR fare) to the rider.
   - The rider approves in ASI:One or transfers `atestfet` on the Dorado testnet, replying with `CommitPayment(transaction_id=tx_hash)`.
   - The agent inspects the Dorado blockchain via REST to verify the transaction was successful, recipient matches the coordinator wallet, and the amount satisfies the quote.
   - An anti-replay registry prevents any transaction hash from being redeemed more than once.

2. **Rail 2 — BAP Nodal Escrow $\rightarrow$ DMRC Account (`INR` via ONDC RSF 2.0)**:
   - Immediately following blockchain confirmation, the ticketing agent executes INR remittance from the BAP Nodal Account (`YES BANK`) to DMRC's BPP Account (`State Bank of India`, VPA `dmrc.ondc@sbi`).
   - A deterministic 12-digit NPCI UPI UTR is recorded (e.g. `202610260421`).
   - A signed `ONDC:TRV11` `/confirm` message is dispatched with `status="PAID"` and `collected_by="BAP"`.
   - An Ed25519-signed QR ticket (`DMRC-QR://v1?...`) is delivered to the rider.

---

## 4. Multi-Environment Switching (`dev` | `test` | `prod`)

Switch environments using `.env`, shell variables, or CLI flags:
```bash
# Via environment variable
export APP_ENV=test
python metro_booking_agents.py

# Via CLI flag
python chat_cli.py --env test
python test_full_lifecycle.py --env dev
```

### Environment Matrix

| Feature / Rail | `dev` (Local Development) | `test` (Staging / QA) | `prod` (Production) |
| :--- | :--- | :--- | :--- |
| **Config Overrides** | `.env.dev` | `.env.test` | `.env.prod` |
| **ONDC Gateway** | `http://localhost:3000` (auto-sandbox) | `https://staging.gateway.ondc.org` | `https://prod.gateway.ondc.org` |
| **Local Beckn Sandbox** | Auto-starts on port 3001/3002 | Disabled | Strictly Forbidden |
| **Fetch.ai Network** | `testnet` (Dorado) | `testnet` (Dorado) | `mainnet` (Fetch-hub-4) |
| **Token Denomination** | `atestfet` | `atestfet` | `afet` (Real mainnet FET) |
| **DMRC Bank Settlement** | Simulated RSF 2.0 + Deterministic UTR | Staging Webhook / Simulated UTR | Real Bank API (RazorpayX Payouts) |
| **Ticket Storage** | `tickets/dev/` | `tickets/test/` | `tickets/prod/` |
| **Validation Gate** | Permissive | Staging verification | Strict (validates HSM keys & mainnet) |

---

## 5. Local Setup & Docker Execution

### Local Python Setup (Python 3.12)
```bash
# 1. Clone repository
git clone https://github.com/Ashish-chanchal/fetch-a-thon-dmrc.git
cd fetch-a-thon-dmrc

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Copy configuration
cp .env.example .env
```

### Local Docker Execution
Run the exact container deployed on Google Cloud Run locally:
```bash
# 1. Build Docker image
docker build -t metrotickets:local .

# 2. Run container on port 8080
docker run -p 8080:8080 --env-file .env metrotickets:local
```

---

## 6. Testing Guide (4 Ways to Test)

```
                                     TESTING MODES
   ┌───────────────────────┬──────────────────────┬──────────────────────┬──────────────────────┐
   │  1. Live ASI:One Chat │ 2. Terminal Chat CLI │ 3. 7-Stage Auto Test │  4. REST API / cURL  │
   │    (@metrotickets)    │ (ASCII QR + Gates)   │ (Single-Command E2E) │ (Programmatic Hooks) │
   └───────────────────────┴──────────────────────┴──────────────────────┴──────────────────────┘
```

---

### Option 1: Live ASI:One Chat (`https://asi1.ai/chat`)

Experience the live multi-agent workflow deployed on Google Cloud Run via Fetch.ai's official [ASI:One Chat platform](https://asi1.ai/chat):

1. Open **[https://asi1.ai/chat](https://asi1.ai/chat)**.
2. Search for the agent by handle or address:
   ```text
   @metrotickets
   ```
   *(Or query coordinator address: `agent1qvka2ph5m5xljedx3pzdajz8ulveu40lrrhnwhmvgn0xy3r42vte7w8ly54`)*
3. **Sample Dialogues to Try**:
   - **Discovery**: `"hello"` $\rightarrow$ Greets with Delhi Metro Quick Book carousel of popular routes.
   - **Route & Fare**: `"Fare from Rajiv Chowk to Hauz Khas"` $\rightarrow$ Computes live distance, interchanges, ₹INR fare, and FET equivalent.
   - **Instant Booking**: Click **`[⚡ Book Ticket]`** on the Journey Card $\rightarrow$ Prompts for FET payment confirmation.
   - **QR Issuance**: Approving payment instantly emits the signed QR ticket with simulated gate action buttons.

---

### Option 2: Interactive Terminal Chat CLI (`python chat_cli.py`)

For rapid testing in the terminal with live ASCII QR rendering and turnstile commands:

```bash
python chat_cli.py
```

* **Conversational Booking**:
  ```text
  You > Book 2 tickets from Kashmere Gate to Rajiv Chowk
  ```
* **Built-in Turnstile Gate Scanner**:
  * `/verify latest` — Non-destructive cryptographic inspection (Ed25519 signature + Dorado tx status).
  * `/entry latest` — Simulates origin station turnstile scan (`ISSUED` $\rightarrow$ `IN_TRANSIT` with green gate flap).
  * `/entry latest` (again) — **Anti-Passback Test**: verifies duplicate entry is rejected.
  * `/exit latest` — Simulates destination exit turnstile scan (`IN_TRANSIT` $\rightarrow$ `COMPLETED`).
  * `/tamper latest` — Forges the ticket payload to verify turnstile anti-tamper alarm.
  * `/status` — Displays wallet balances, active environment, and station count.

---

### Option 3: Single-Command Automated Lifecycle Test (`python test_full_lifecycle.py`)

Runs a fully autonomous end-to-end regression test validating all 7 lifecycle stages in under 15 seconds:

```bash
python test_full_lifecycle.py
```

#### Verification Stages Executed:
1. **Stage 1: Intent & Booking**: Natural language query dispatched to `asi1-mini`, queries live telemetry, and executes dual settlement.
2. **Stage 2: QR Generation**: Generates Ed25519 cryptographic token and PNG image.
3. **Stage 3: Inspection**: Validates signature and blockchain transaction status.
4. **Stage 4: Entry Scan**: Scans at origin turnstile (`ISSUED` $\rightarrow$ `IN_TRANSIT`).
5. **Stage 5: Anti-Passback**: Re-entry attempt blocked (`ANTI_PASSBACK_BLOCKED_RED_LIGHT`).
6. **Stage 6: Exit Scan**: Destination exit turnstile scan (`IN_TRANSIT` $\rightarrow$ `COMPLETED`).
7. **Stage 7: Tamper & Replay**: Modifies passenger count (`pax=9`) $\rightarrow$ signature alarm triggered (`ALARM_TAMPERED_QR_RED_LIGHT`).

---

### Option 4: Direct REST API & cURL Testing

When running locally or querying the Google Cloud Run service URL:

#### 1. Query Active Metro Stations & Telemetry
```bash
curl -s http://localhost:8080/metro/stations | python3 -m json.tool | head -n 30
```

#### 2. Conversational Intent Processing (`POST /metro/chat`)
```bash
curl -s -X POST http://localhost:8080/metro/chat \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Check fare from Hauz Khas to Rajiv Chowk",
    "city_code": "std:011"
  }' | python3 -m json.tool
```

#### 3. Programmatic Ticket Booking (`POST /metro/book`)
```bash
curl -s -X POST http://localhost:8080/metro/book \
  -H "Content-Type: application/json" \
  -d '{
    "origin_code": "HAUZ_KHAS",
    "destination_code": "RAJIV_CHOWK",
    "passengers": 1,
    "rider_name": "Rider",
    "rider_phone": "+919876543210"
  }' | python3 -m json.tool
```

#### 4. Turnstile AFC Gate Verification (`POST /metro/ticket/verify`)
```bash
# Entry Turnstile Scan:
curl -s -X POST http://localhost:8080/metro/ticket/verify \
  -H "Content-Type: application/json" \
  -d '{"qr_or_order_id": "latest", "action": "ENTRY_SCAN", "station_code": "HAUZ_KHAS"}' | python3 -m json.tool

# Exit Turnstile Scan:
curl -s -X POST http://localhost:8080/metro/ticket/verify \
  -H "Content-Type: application/json" \
  -d '{"qr_or_order_id": "latest", "action": "EXIT_SCAN", "station_code": "RAJIV_CHOWK"}' | python3 -m json.tool
```

---

## 7. Production Security & Readiness

To prepare for commercial operation on mainnet and ONDC live registries:

1. **Generate Cryptographic Keypairs**:
   ```bash
   python scripts/generate_production_keys.py
   ```
   Generates ONDC Ed25519 signing keys, X25519 encryption keys, and high-entropy agent seeds.
2. **Audit Production Readiness**:
   ```bash
   python scripts/verify_prod_readiness.py
   ```
   Verifies that no default seeds, mock gateways, or insecure configurations remain active.
3. **Follow the Production Roadmap**: Consult [docs/TAKE_TO_PRODUCTION.md](docs/TAKE_TO_PRODUCTION.md) for ONDC registry onboarding, RazorpayX bank payout setup, and Mainnet FET compliance.
