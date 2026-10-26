# Production Engineering Guide & Commercial Launch Roadmap

This document outlines the engineering specifications, compliance procedures, and operational milestones required to transition **`metrotickets`** from its current Google Cloud Run staging deployment to a commercially authorized public transit service.

---

## 1. Current State vs. Production Target

The system currently runs 24/7 on **Google Cloud Run** (`metrotickets`) using Docker and Agentverse Mailbox mode. The table below outlines what is production-grade today versus components scheduled for institutional integration:

| Component | Current Implementation (Staging / Cloud Run) | Production Target (Commercial Launch) | Priority |
| :--- | :--- | :--- | :--- |
| **Hosting & Reliability** | Google Cloud Run container (`min-instances=1`, auto-restart, port 8080). | Multi-region Cloud Run / GKE with Cloud SQL PostgreSQL and Memorystore Redis. | **Complete (P0)** |
| **Conversational Agent** | ASI:One Chat (`@metrotickets`) with interactive route, fare, and ticket cards. | Multi-channel: ASI:One + WhatsApp Business API + Telegram Bot via ONDC BAP. | P1 |
| **Crypto Payment (Rail 1)** | On-chain Fetch.ai Dorado Testnet (`atestfet`) with ledger verification and anti-replay. | Fetch.ai Mainnet (`afet` on Fetch-hub-4) with automated oracle price feeds. | P1 |
| **Fiat Payment (Rail 2)** | Simulated RSF 2.0 settlement producing a deterministic 12-digit NPCI UPI UTR. | Real INR payout via RazorpayX / Cashfree Payouts directly to DMRC's nodal bank account. | P1 |
| **ONDC Connectivity** | Local Beckn Sandbox responding with signed TRV11 v2.1.0 ACK messages. | Live ONDC Production Registry (`prod.gateway.ondc.org`) connecting to DMRC's live BPP. | P1 |
| **Station & Fare Data** | Live DMRC Station Graph API (254 active stations, route traversal, interchange logic). | Official DMRC Network Feed integrated directly via ONDC `on_search` and `on_select`. | P2 |
| **Live Transit Telemetry** | Delhi Government OTD GTFS-RT Protobuf Stream (`VehiclePositions.pb`). | Integrated multimodal GTFS feed covering Delhi Metro lines and feeder electric buses. | P2 |
| **Ticket Issuance & Gates** | Cryptographic Ed25519-signed QR code validated by software turnstile gate simulator. | DMRC-issued encrypted QR codes scanned by physical Delhi Metro AFC turnstile gates. | P2 |

---

## 2. The 7-Pillar Production Roadmap

### Pillar 1: Always-On Cloud Infrastructure (Completed)
- **Status**: Live on Google Cloud Run (`metrotickets`).
- **Configuration**: Deployed as an unbuffered Python 3.12 container with `--min-instances=1` and `--timeout=3600`.
- **Mailbox Protocol**: Outbound TLS WebSocket to Agentverse eliminates the need for dynamic DNS or ngrok tunnels.
- **Reference**: Detailed in [docs/DEPLOYMENT_GCP.md](file:///Users/ashwinsharma/hotcrunch/fetch-a-thon-dmrc/docs/DEPLOYMENT_GCP.md).

### Pillar 2: Dual Fiat & Crypto Payment Gateway
While crypto enthusiasts can pay in FET, the majority of metro riders require standard INR payment methods (UPI, RuPay, Debit Cards, NetBanking):
- **Provider Selection**: Razorpay / Cashfree Payments for Indian domestic acquiring.
- **Dynamic Payment Links**: The `metro_bap_coordinator` generates a dynamic Razorpay Payment Link / UPI Intent link embedded directly inside the ASI:One chat card.
- **Webhook Listener**: A secure Cloud Run webhook endpoint (`POST /webhooks/razorpay`) receives instant payment confirmation and triggers ticket emission.
- **Dynamic FET/INR Oracle**: Replace the fixed `INR_TO_TESTNET_FET = 0.01` with an automated CoinGecko/Binance price feed oracle refreshed every 60 seconds with a 5-minute price lock window for active booking sessions.

### Pillar 3: ONDC Live Network Participant Onboarding
To query real DMRC inventory and submit tickets to the official transit network:
1. **Entity Verification**: Register QMatus as a verified Network Participant (Buyer App / BAP) on the ONDC Portal ([portal.ondc.org](https://portal.ondc.org)).
2. **Domain Registration**: Register subscriber ID `bap.metro-agent.qmatus.com` under domain `ONDC:TRV11` (Mobility).
3. **Key Exchange**:
   - Generate production Ed25519 and X25519 keypairs using `python scripts/generate_production_keys.py`.
   - Upload public keys to the ONDC Staging Registry (`staging.registry.ondc.org`) for compliance testing.
4. **Log Verification & Certification**: Pass ONDC automated protocol verification test cases for `search`, `select`, `init`, and `confirm` flows.
5. **Production Migration**: Migrate credentials to `prod.registry.ondc.org`.

### Pillar 4: Automated Bank Settlement to DMRC (RSF 2.0)
ONDC's Reconciliation and Settlement Framework (RSF 2.0) dictates how rider funds are settled to transit authorities:
- **Nodal / Escrow Account**: Open an RBI-compliant nodal account with a designated sponsor bank (e.g., YES BANK or ICICI Bank).
- **Automated Payout Engine**: Configure `common/ondc_client.py` with `INR_PAYOUT_PROVIDER="live_webhook"`, utilizing RazorpayX Payouts API.
- **Settlement Execution**:
  ```python
  # Production remittance dispatch
  payload = {
      "account_number": cfg.DMRC_DEFAULT_BANK_ACCOUNT_NO,
      "ifsc": cfg.DMRC_DEFAULT_IFSC_CODE,
      "amount": fare_amount_in_paise,
      "purpose": "ONDC_TRV11_TICKET_SETTLEMENT",
      "reference_id": f"DMRC_{order_id}",
  }
  response = requests.post(cfg.INR_PAYOUT_WEBHOOK_URL, json=payload, headers=headers)
  npci_utr = response.json().get("utr")
  ```
- **Reconciliation Audit**: The returned 12-digit bank UTR is permanently bound to the ONDC `/confirm` transaction record.

### Pillar 5: Official DMRC Ticketing Integration
- In production, DMRC acts as the ONDC Seller Platform (BPP).
- The `metro_bpp_ticketing_agent` relays the booking request to DMRC's production BPP endpoint.
- DMRC generates its own proprietary, encrypted turnstile barcode/QR string in the Beckn `on_confirm` response.
- The coordinator formats and renders this authentic DMRC QR code directly inside the ASI:One chat UI.

### Pillar 6: Physical AFC Turnstile Validation
- Physical DMRC AFC turnstile gates scan DMRC-issued QR tokens at entry and exit readers.
- Gate status updates are delivered asynchronously via ONDC status webhooks or polled by the coordinator, providing real-time trip status in the chat feed.

### Pillar 7: Fetch.ai Mainnet Migration
- Switch `APP_ENV="prod"` in the environment configuration.
- The Bureau automatically sets `FETCHAI_NETWORK="mainnet"` and denom `afet`.
- Coordinator wallet is funded with real FET on the `fetch-hub-4` mainnet.
- Implement automated compliance logging for Indian Virtual Digital Asset (VDA) 1% TDS reporting where applicable.

---

## 3. Cryptographic Key Management & Storage

Never store production seeds or signing keys in plaintext files or source control.

```
                         KEY MANAGEMENT ARCHITECTURE
 ┌────────────────────────────────────────────────────────────────────────┐
 │ Google Secret Manager / Cloud HSM                                      │
 │                                                                        │
 │  ├── COORDINATOR_SEED: High-entropy BIP39 seed for agent identity      │
 │  ├── ONDC_SIGNING_PRIVATE_KEY: Ed25519 private key for Beckn headers   │
 │  ├── ONDC_ENCRYPTION_PRIVATE_KEY: X25519 private key for payload crypto│
 │  ├── TICKET_SIGNING_PRIVATE_KEY: Ed25519 private key for QR signatures│
 │  ├── AGENTVERSE_KEY: JWT API token for mailbox connection             │
 │  └── ASI_ONE_API_KEY: Authentication token for ASI:One LLM engine      │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                     Injected into Container at Runtime
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ Google Cloud Run: metrotickets                                         │
 │ (Keys exist only in process memory; never written to persistent disk)  │
 └────────────────────────────────────────────────────────────────────────┘
```

Run key generation before deployment:
```bash
python scripts/generate_production_keys.py
```

Audit configuration readiness:
```bash
python scripts/verify_prod_readiness.py
```

---

## 4. Database & State Architecture Migration

In the staging prototype, active booking sessions are stored in an in-memory dictionary (`BOOKING_SESSIONS`), and issued tickets are saved to local disk (`tickets/<env>/`). 

For high-availability horizontal scaling across multiple Cloud Run instances, state management must be decoupled:

```
                            STATE STORAGE DECOUPLING
 ┌───────────────────────────┐         ┌───────────────────────────┐
 │ Cloud Run Instance 1      │         │ Cloud Run Instance 2      │
 └─────────────┬─────────────┘         └─────────────┬─────────────┘
               │                                     │
               ├───────────────────┬─────────────────┤
               ▼                                     ▼
 ┌───────────────────────────┐         ┌───────────────────────────┐
 │ Google Cloud Memorystore  │         │ Google Cloud SQL          │
 │ (Redis 7.0)               │         │ (PostgreSQL 16)           │
 │                           │         │                           │
 │ • Active chat sessions    │         │ • Confirmed tickets       │
 │ • 5-minute booking quotes │         │ • On-chain tx audit log   │
 │ • Replay attack dedupe    │         │ • Bank UTR reconciliation │
 └───────────────────────────┘         └───────────────────────────┘
```

1. **Redis**: Stores volatile conversation state with automatic TTL expiration (15 minutes).
2. **PostgreSQL**: Stores persistent financial records, NPCI bank UTRs, and blockchain transaction hashes with unique constraints to mathematically prevent duplicate ticket issuance.
3. **Google Cloud Storage (GCS)**: Stores generated ticket PNG assets served behind a Cloud CDN endpoint.

---

## 5. Security & Threat Mitigation Runbook

| Security Threat | Impact | Operational Mitigation |
| :--- | :--- | :--- |
| **Agent Impersonation** | Malicious actor runs rogue agent claiming the coordinator address. | Rotate seeds immediately using `scripts/generate_production_keys.py`. Default repo seeds are blocked in production via `cfg.validate_environment()`. |
| **Dorado RPC Outage** | Agent cannot verify incoming FET transfers. | Configure fallback Dorado RPC endpoints in `common/fet_payment.py`. The agent prompts the rider: "Ledger check delayed, retrying automatically..." |
| **Concurrent Payment Race** | Two riders attempt to claim the same transaction hash simultaneously. | Database-level unique constraint (`tx_hash UNIQUE`) on confirmed orders table ensures only one transaction can successfully commit. |
| **Turnstile Anti-Passback** | Passenger attempts to share active QR ticket with another person behind them. | Software validator enforces `ISSUED -> IN_TRANSIT` transition. Second entry is immediately locked with red flap alarm. |

---

## 6. Pre-Launch Verification Gate

Before directing public users or ONDC production traffic to the service:

1. [x] Deploy container to Google Cloud Run with `--min-instances=1`.
2. [x] Connect agent to Agentverse Mailbox mode (`ENABLE_MAILBOX=true`).
3. [x] Verify chat discoverability on ASI:One (`@metrotickets`).
4. [ ] Pass `python scripts/verify_prod_readiness.py` with zero errors.
5. [ ] Execute end-to-end 7-stage test suite: `python test_full_lifecycle.py --env prod`.
6. [ ] Confirm bank webhook endpoint is reachable by RazorpayX / nodal bank.
7. [ ] Validate ONDC registry signature verification against live gateway.
