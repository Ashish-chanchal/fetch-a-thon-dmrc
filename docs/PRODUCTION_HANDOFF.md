# Metro Agents: Status, Flow and Production Handoff

As of 2026-10-24, written against `main` after PR #1.

## Current status

A rider can chat with our agent on ASI:One, get a Delhi Metro route and fare, pay for it in testnet FET from their own wallet, and receive a signed QR ticket. The money and the chain are real (Fetch.ai Dorado testnet). Everything on the DMRC and ONDC side of that payment is simulated.

What changed in PR #1 (merged into `main` on 2026-10-23):

- **The rider now pays.** The coordinator agent acts as the seller in the Fetch.ai payment protocol. It sends a `RequestPayment`, the rider approves it in ASI:One (or sends FET from any wallet and taps "I've paid"), and the coordinator checks the transfer on chain before any ticket is issued. Before this PR, the coordinator paid from the team's own wallet.
- **No ticket without money.** The ticketing agent no longer issues a ticket when the transaction failed, and a replayed transaction hash never issues a second ticket.
- **Stricter booking confirmation.** Only a short, explicit reply confirms a booking. Unresolved station names no longer fall back to a default station.
- **Mailbox mode.** With `ENABLE_MAILBOX=true` the coordinator registers as an Agentverse mailbox agent, so it no longer needs ngrok or a public endpoint.
- **Chat cards.** Journey, passenger, confirm-and-pay and ticket cards, plus a popular-routes carousel, drive the flow with buttons.
- **OTD API key removed from the code.** It must still be rotated, because it remains in git history.

Still open:

- The agents still run on a teammate's laptop. Nothing is hosted always-on yet.
- The default agent seeds are still public strings in `common/config.py`. The code now warns but does not refuse to start.
- The QR and ONDC signing keys still fall back to keys derived from public strings.
- Booking sessions and the ticket registry live in process memory and a local JSON file, so a restart loses pending payments.
- Fare conversion is a fixed `INR_TO_TESTNET_FET = 0.01` (a ₹40 fare costs 0.4 testnet FET), not a market rate.

## End-to-end flow

A booking takes 12 steps across three agents and the Dorado chain. Steps 1 to 8 are real Fetch.ai machinery; most of steps 9 to 12 stand in for DMRC and ONDC.

| # | Step | What happens | Real or simulated |
| --- | --- | --- | --- |
| 1 | Rider asks | In ASI:One chat, e.g. "Rajiv Chowk to Noida" | Real |
| 2 | Coordinator | BAP agent resolves stations and sends cards | Real |
| 3 | Catalog agent | Plays the DMRC BPP; ONDC search goes to a local sandbox | Simulated |
| 4 | Route and fare | From a community DMRC API, not an official source | Partly real |
| 5 | Pay request | `RequestPayment` in FET, 0.01 per rupee | Real |
| 6 | Rider approves | In the ASI:One wallet, or taps "I've paid" | Real |
| 7 | FET transfer | Rider to coordinator on Dorado testnet | Real |
| 8 | Chain check | Dorado REST: success, recipient, amount | Real |
| 9 | Ticketing agent | Plays the DMRC BPP; ONDC init and confirm go to the sandbox | Simulated |
| 10 | DMRC payout | No money moves; the UTR is a hash | Simulated |
| 11 | QR ticket | Ed25519-signed with our own key, shown in chat | Partly real |
| 12 | Gate scan | Buttons run a local entry and exit check | Simulated |

All three agents run in one Bureau process (`metro_booking_agents.py`). Only the coordinator talks to ASI:One, through its Agentverse mailbox. The catalog and ticketing agents only talk to the coordinator inside that process. A ticket is issued only after step 8 passes, and the same transaction hash can never buy a second ticket.

## Knowledge primer

Two worlds meet in this project: Fetch.ai's agent network, which carries the chat and the FET payment, and India's ONDC network, which is how a real app would buy a DMRC ticket. Read each term against where it lives in our code.

| Concept | What it is | Where it shows up for us |
| --- | --- | --- |
| uAgents | Fetch.ai's Python framework for agents. Each agent has an address derived from a seed, and handlers for typed messages grouped into protocols. | All three agents in `agents/`. |
| Seed and agent address | The seed string is the agent's private key. Anyone who knows the seed can run an agent with the same address and wallet. | `COORDINATOR_SEED` and friends in `common/config.py`. |
| Bureau | Runs several agents in one Python process on one port. | `metro_booking_agents.py` starts the coordinator, catalog and ticketing agents together. |
| Almanac | An on-chain registry that maps agent addresses to their endpoints and protocols, so other agents can find them. | Registration happens on startup. It needs a small amount of testnet FET in the agent wallet. |
| Agentverse | Fetch.ai's hosted platform. It lists agents so ASI:One can discover them, and can also host agent code. | We register the coordinator there with a name, description and README (`agent_readme.md`). |
| Mailbox agent | An agent that runs on your own machine but receives messages through an Agentverse mailbox, so it needs no public URL. | `ENABLE_MAILBOX=true`. This replaced the ngrok tunnel. |
| Hosted agent | Code that runs inside Agentverse itself. Only a limited set of Python packages is allowed and state resets. | Not used. Our code needs `qrcode`, `pillow` and GTFS bindings, so it would need a rewrite. |
| ASI:One | Fetch.ai's chat assistant. It routes a user's request to a registered agent that speaks the chat protocol. | The rider's entry point. |
| Chat protocol | The standard message format (`ChatMessage`, `ChatAcknowledgement`) that ASI:One uses with agents. | `chat_protocol` in `coordinator_agent.py`. |
| Payment protocol | Standard messages for agent payments: `RequestPayment`, `CommitPayment`, `CompletePayment`, `CancelPayment`, `RejectPayment`. | The coordinator is the seller and the rider's ASI:One wallet is the buyer. |
| FET and Dorado | FET is Fetch.ai's token. Dorado is its testnet, where tokens are free from a faucet. Amounts are in `atestfet` (testnet) or `afet` (mainnet), 10^18 per FET. | `common/fet_payment.py` verifies transfers through the Dorado REST API. |
| ONDC | Open Network for Digital Commerce, India's government-backed open commerce network. Mobility (metro, bus) is the TRV11 domain. | `common/ondc_client.py` builds TRV11 v2.1.0 payloads. |
| BAP and BPP | Beckn terms. The BAP (buyer app) faces the user. The BPP (provider platform) is the seller, here DMRC's ticketing system. | Coordinator = BAP. Catalog and ticketing agents play the DMRC BPP. |
| Beckn flow | The ONDC call sequence: `search`, `select`, `init`, `confirm`, each answered asynchronously by an `on_*` callback. | The catalog agent does search/select, the ticketing agent does init/confirm. |
| ONDC signing | Every ONDC request is signed with an Ed25519 key registered in the ONDC registry for that participant. | `ONDC_SIGNING_PRIVATE_KEY`, falling back to a public default. |
| RSF and UTR | RSF is ONDC's Reconciliation and Settlement Framework, which governs how the buyer app pays the seller. A UTR is the bank's reference number for a real transfer. | Our UTR is generated from a hash, not issued by a bank. |
| AFC gate | DMRC's Automatic Fare Collection gates that read QR tickets at entry and exit. | Simulated by the gate-scan buttons and `common/ticket_verifier.py`. |

## Real vs simulated

Four pieces are real today, four are partly real, and seven are simulated. The rule of thumb: anything on the Fetch.ai side is real, anything that touches DMRC, ONDC or Indian banking is simulated.

| Piece | Status | What actually happens today | Where in code |
| --- | --- | --- | --- |
| ASI:One chat and cards | Real | Riders chat with the coordinator through the standard chat protocol. | `agents/coordinator_agent.py`, `agents/chat_cards.py` |
| Agentverse listing and Almanac registration | Real | The coordinator is discoverable by ASI:One, in mailbox mode. | `scripts/register_agentverse.py` |
| Rider pays in FET | Real | The rider's wallet sends testnet FET to the coordinator's wallet. Testnet FET has no market value. | `coordinator_agent.py` payment protocol |
| On-chain payment check | Real | The coordinator reads the transaction from the Dorado REST API and checks success, recipient and amount. | `common/fet_payment.py` |
| Stations, route and fare | Partly real | Live data, but from a community API (`dmrc-rest-api.vercel.app`), not an official DMRC source. | `common/dmrc_client.py` |
| Live train positions | Partly real | Delhi OTD GTFS-RT feed, only when an API key is set. The old key is in git history. | `common/otd_client.py` |
| QR ticket signature | Partly real | Really Ed25519-signed, but with our own key, so no DMRC gate would accept it. | `common/ticket_verifier.py` |
| Hosting | Partly real | Real agents on a teammate's laptop. Offline when the laptop sleeps. | `metro_booking_agents.py` |
| INR to FET price | Simulated | Fixed rate of 0.01 FET per rupee, not a market rate. | `INR_TO_TESTNET_FET` in `common/config.py` |
| ONDC search, select, init, confirm | Simulated | Payloads are built and signed, then sent to a local sandbox on `localhost:3000` that always answers ACK. Quotes fall back to the DMRC API fare. | `common/ondc_client.py`, `agents/catalog_agent.py` |
| ONDC participant identity | Simulated | Our BAP id and signing key are not registered with ONDC. The key falls back to one derived from a public string. | `ONDC_BAP_ID`, `ONDC_SIGNING_PRIVATE_KEY` |
| INR settlement to DMRC and UTR | Simulated | No money moves. Provider is `SIMULATED_RSF_2_0` and the 12-digit UTR is a hash. DMRC and nodal bank details are placeholders. | `common/ondc_client.py` settlement, `common/config.py` |
| Gate entry and exit | Simulated | "Simulate entry gate" and "Simulate exit gate" buttons run a local state machine. | `common/ticket_verifier.py` |
| Rider details | Simulated | Name, phone and email fall back to defaults such as "Dev Rider". No KYC. | `RIDER_DEFAULT_*` in `common/config.py` |
| Ticket and session storage | Simulated | Sessions in a Python dict, tickets in a local JSON file and PNGs. Lost on restart or a new machine. | `BOOKING_SESSIONS`, `tickets/<env>/` |

## Take-to-production TODOs

Ashish owns the research. Start with always-on hosting, because it is the only item that blocks judges from trying the agent at any hour. ONDC onboarding is the biggest item and decides how fares, tickets, gates and DMRC settlement work, so research it early even though it ships later.

| # | Item | Priority | Research status | Unblocks |
| --- | --- | --- | --- | --- |
| 1 | Always-on hosting | P0 now | Not started | Judges can use the agent any time |
| 2 | INR payments via Razorpay | P1 next | Not started | Riders without crypto can pay |
| 3 | ONDC live network onboarding | P1 next | Not started | Items 4, 5 and 6 |
| 4 | Settlement and payout to DMRC with a real UTR | P2 later | Not started | DMRC gets paid |
| 5 | Official DMRC fare and ticket | P2 later | Not started | A ticket DMRC honours |
| 6 | Real gate validation | P2 later | Not started | Riders can enter the station |
| 7 | Mainnet FET | P1 next | Not started | Real-value crypto payments |

### 1. Always-on hosting

Today the agents run on a laptop, so the agent disappears from ASI:One whenever it sleeps.

- **Research:** a small always-on Linux VM (any cloud with an India region) running `metro_booking_agents.py` as a mailbox agent under a process supervisor (systemd or Docker with restart). Compare with a full rewrite as an Agentverse hosted agent.
- **Research:** moving `BOOKING_SESSIONS` and the ticket registry from memory and JSON into SQLite or Postgres, so a restart keeps pending payments and issued tickets.
- **Research:** where secrets live (seeds, Agentverse key, ASI:One key) instead of a `.env` on a laptop.
- **Open question:** does Agentverse hosted support `qrcode`, `pillow` and the GTFS protobuf package today, or is the mailbox VM the only option?
- **Open question:** what does the VM cost per month, and does the Almanac registration need periodic FET top-ups?

### 2. INR payments via Razorpay

Stripe is not available to us in India, so Razorpay is the INR route. FET stays as a second payment option.

- **Research:** Razorpay Payment Links (or Checkout) created by the coordinator, shown as a button in the ASI:One card, with a webhook or a status poll that tells the coordinator when the rider paid.
- **Research:** how the coordinator receives the webhook in mailbox mode, where it has no public URL. A small HTTPS endpoint on the hosting VM is the likely answer.
- **Research:** Razorpay account requirements for QMatus (business KYC, website, which payment methods are enabled, settlement cycle, fee per transaction).
- **Open question:** can test mode be used for the hackathon demo with no KYC?
- **Open question:** does the ASI:One payment protocol allow a non-FET method (for example a card or UPI link) in `RequestPayment`, or do we handle INR outside the protocol?

### 3. ONDC live network onboarding

This is the gate to every DMRC-side item. As an ONDC buyer app we would call DMRC's real seller platform instead of our own catalog and ticketing agents.

- **Research:** the ONDC network participant onboarding steps for a buyer app in the TRV11 (mobility) domain: staging, pre-production and production registries, the subscriber id and domain, and key registration.
- **Research:** the compliance and log-verification checks ONDC requires before production access, and how long they take.
- **Research:** which entity must register. Can QMatus apply as a company, and what documents does that need?
- **Research:** whether DMRC is live as a seller on ONDC for metro QR tickets today, and which buyer apps already sell them.
- **Open question:** does onboarding replace our catalog and ticketing agents entirely, or do they stay as thin wrappers that call DMRC's seller platform?
- **Open question:** can a Fetch.ai agent be the registered buyer app, or must the subscriber be a regular HTTPS web service that the agent calls?

### 4. Settlement and payout to DMRC with a real UTR

Today no money reaches DMRC, and the UTR is a hash. In ONDC the buyer app usually collects the fare and settles to the seller under the network's settlement rules.

- **Research:** ONDC's Reconciliation and Settlement Framework for TRV11: who collects, when the buyer app must pay the seller, and how the settlement is reported back.
- **Research:** ONDC's settlement agency option, versus paying DMRC ourselves with RazorpayX Payouts or Razorpay Route. Payouts return a real bank UTR for each transfer.
- **Research:** whether a nodal or escrow account is required to hold riders' money before settlement, and what that needs from a bank.
- **Open question:** what buyer-app commission or finder fee does DMRC pay, if any? That is our revenue line.
- **Open question:** if a rider pays in FET, who converts it to INR before we pay DMRC, and is that allowed?

### 5. Official DMRC fare and ticket

Our fares come from a community API and our QR is signed by us. In production both would come from DMRC.

- **Research:** whether the ONDC `on_search` and `on_select` responses from DMRC carry the official fare, so the community API becomes a fallback for station lookup only.
- **Research:** whether DMRC's `on_confirm` returns the ticket QR itself. If so, we render DMRC's QR and stop signing our own, which also removes our QR signing-key problem.
- **Research:** DMRC's own published data (OTD Delhi GTFS) for station codes and lines, as an official source for route display.
- **Open question:** does DMRC offer any ticketing API outside ONDC, for example to partners?

### 6. Real gate validation

Our gates are buttons. Real AFC gates read DMRC-issued QR codes only.

- **Research:** if item 5 confirms that DMRC issues the QR, gate validation needs no work from us. Confirm this.
- **Research:** the ticket status updates ONDC sends after use (entry, exit, expiry), so the chat can show "ticket used".
- **Open question:** how are refunds and cancellations handled for an unused ticket?

### 7. Mainnet FET

Switching networks is mostly configuration, since `APP_ENV=prod` already selects mainnet and `afet`. The hard parts are price and law.

- **Research:** a live FET to INR price source, with a quote that stays valid for a few minutes, instead of the fixed 0.01 rate.
- **Research:** Indian tax rules on accepting crypto (virtual digital assets) as payment, including TDS, and whether QMatus can legally accept FET for a metro fare.
- **Research:** how the coordinator's mainnet wallet key is stored and who can move its funds.
- **Open question:** do the Fetch-a-thon judges care about mainnet, or is testnet acceptable for the hackathon?

## Security fixes before any real money

Two of the five known risks were fixed in PR #1, and a sixth turned up while this doc was written. The seed risk matters even for the hackathon, because anyone who reads the repo can impersonate our live agent.

| Risk | Status | Why it matters | Fix |
| --- | --- | --- | --- |
| Default agent seeds committed in `common/config.py` (`metro_bap_coordinator_<env>_seed_2026`) | Open. PR #1 only warns. | The seed is the agent's private key. Anyone can run an agent with our coordinator's address and wallet. | Generate random seeds (`scripts/generate_production_keys.py`), keep them in secrets, refuse to start in mailbox mode with a default seed. The coordinator gets a new address, so re-register it on Agentverse. |
| OTD Delhi API key committed | Removed from code in PR #1. Still in git history. | Anyone with the repo history can use our quota. | Rotate the key with OTD Delhi. Rewriting history is optional once the key is dead. |
| QR and ONDC signing keys derived from public strings | Open. | Anyone can forge a valid-looking QR ticket or sign ONDC requests as us. | Require `ONDC_SIGNING_PRIVATE_KEY` outside dev. Use separate keys for QR and ONDC. If DMRC issues the QR (TODO 5), drop our QR key. |
| Failed transaction still issued a ticket | Fixed in PR #1. | Free tickets. | Done. Replayed tx hashes are also refused. |
| "ok" or "please" confirmed a booking | Fixed in PR #1. | Accidental bookings. | Done. Only a short explicit reply confirms. |
| "I've paid" accepts any matching transfer | Open, found while writing this doc. | `find_incoming_payment` takes the first transfer of at least the fare to our wallet since the booking started, from any sender. Two riders with the same fare could claim one payment. | Give each booking a unique amount or require a memo with the booking reference, and match on it. |
