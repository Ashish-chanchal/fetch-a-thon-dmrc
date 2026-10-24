# Delhi Metro Ticket Booking

Plan a Delhi Metro journey and book your ticket in one chat. Pay in FET on the Fetch.ai testnet and get a signed QR ticket back.

## What you can ask
- "Fare from Hauz Khas to Rajiv Chowk"
- "Book 2 tickets from Kashmere Gate to Rajiv Chowk"
- "How many interchanges are there?"
- "Which line do I take from Noida Sector 18 to Rajiv Chowk?"

## How it works
1. You ask for a route. The agent looks up the fare, stations, interchanges and travel time from public DMRC fare data.
2. Tap **Book Ticket**. The agent asks you to pay the fare in FET using the Agent Payment Protocol.
3. After you pay, the agent checks your transfer on the Fetch.ai ledger (right sender, right recipient, enough amount). Each payment can buy only one ticket.
4. You receive a signed QR ticket and a link to your payment on the block explorer.

## Under the hood
Three cooperating uAgents mirror the ONDC buyer/seller split: a coordinator (chat, payment, ticket delivery), a catalog agent (stations, fares, quotes) and a ticketing agent (orders and QR issuance). The chat is powered by the ASI:One model with tool calling.

## Status
Hackathon demo on the Fetch.ai testnet (no real money). ONDC messages, the DMRC settlement and the turnstile scans are simulated. Tickets will not open real gates.

Protocols: Agent Chat Protocol, Agent Payment Protocol (seller).
