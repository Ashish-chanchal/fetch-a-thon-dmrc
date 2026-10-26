# Delhi Metro Ticket Booking (@metrotickets)

Plan your Delhi Metro journeys and book tickets directly within ASI:One chat. Powered by Fetch.ai uAgents, ONDC TRV11 mobility standards, real-time transit telemetry, and on-chain settlement.

Deployed 24/7 on **Google Cloud Run** (`metrotickets`) using Docker and Agentverse Mailbox mode.

---

## What You Can Ask
- "Fare from Hauz Khas to Rajiv Chowk"
- "Book 2 tickets from Kashmere Gate to Rajiv Chowk"
- "How many interchanges between Noida Sector 18 and Rajiv Chowk?"
- "Check live stations and travel time from Gurugram to Kashmere Gate"

---

## How It Works
1. **Interactive Route & Fare Discovery**: Ask for any journey across all 254 active Delhi Metro stations. The agent retrieves real-time station graphs, interchange details, travel times, and exact fares in INR and FET.
2. **Instant 1-Click Booking**: Tap **`[⚡ Book Ticket]`** on the interactive Journey Card. The agent initializes the order and requests payment in FET.
3. **On-Chain Blockchain Settlement**: Approve payment in your ASI:One wallet or send FET on the Fetch.ai Dorado testnet. The agent verifies the transaction on-chain (confirming recipient, amount, and anti-replay protection).
4. **Instant Cryptographic QR Ticket**: Receive a high-resolution, Ed25519-signed digital QR ticket with simulated DMRC RSF 2.0 bank remittance reference and NPCI UPI UTR.
5. **Turnstile Gate Verification**: Test gate actions directly in the chat with simulated origin turnstile entry and destination exit scans.

---

## Under the Hood
The service coordinates three autonomous uAgents running in a containerized Fetch.ai Bureau:
- **`metro_bap_coordinator`**: Interfaces with riders via `AgentChatProtocol` and handles wallet payments via `AgentPaymentProtocol`.
- **`metro_bpp_catalog_agent`**: Resolves station graphs, live Delhi OTD GTFS-RT vehicle telemetry (`VehiclePositions.pb`), and computes fares.
- **`metro_bpp_ticketing_agent`**: Generates cryptographic QR tokens, manages order fulfillment, and executes dual-rail settlement.

---

## Environment & Status
- **Deployment**: Google Cloud Run (`metrotickets`), Python 3.12 Docker Container.
- **Connection Mode**: Agentverse Mailbox (`ENABLE_MAILBOX=true`) — 24/7 always-on connection.
- **Blockchain**: Fetch.ai Dorado Testnet (`atestfet`).
- **Protocols**: Agent Chat Protocol, Agent Payment Protocol (role="seller"), ONDC:TRV11 v2.1.0 (Mobility).
