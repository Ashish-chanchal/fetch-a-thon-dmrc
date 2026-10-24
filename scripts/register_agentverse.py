"""
One-time registration script for Agentverse & ASI:One.
Proves identity via cryptographic challenge and registers the BAP Coordinator agent ('dmrcbook') in the Almanac.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load project config and agents
import common.config as cfg
from agents.coordinator_agent import coordinator
import asyncio
from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
from uagents_core.registration import RegistrationRequest
from uagents_core.utils.registration import (
    register_chat_agent,
    RegistrationRequestCredentials,
    AgentverseRequestError,
)

def main():
    agentverse_key = cfg.AGENTVERSE_KEY or os.environ.get("AGENTVERSE_KEY", "").strip()
    agent_seed = cfg.COORDINATOR_SEED or os.environ.get("AGENT_SEED_PHRASE", "").strip()

    if not agentverse_key:
        print("❌ Error: AGENTVERSE_KEY is not set in .env")
        return
    if not agent_seed:
        print("❌ Error: COORDINATOR_SEED / AGENT_SEED_PHRASE is not set")
        return

    agent_handle = cfg.AGENT_HANDLE or os.environ.get("AGENT_HANDLE", "dmrcmetro").strip()
    agent_name = cfg.AGENT_NAME or os.environ.get("AGENT_NAME", "Delhi Metro Ticket Booking").strip()

    print(f"🚀 Registering '{agent_handle}' ({agent_name}) with Agentverse...")
    print(f"  • Mode:                {'Mailbox' if cfg.ENABLE_MAILBOX else 'Webhook/Endpoint'}")
    print(f"  • Coordinator Name:    {coordinator.name}")
    print(f"  • Coordinator Handle:  {agent_handle}")
    print(f"  • Coordinator Address: {coordinator.address}")
    print(f"  • Seed Prefix:         {agent_seed[:10]}...")

    if cfg.ENABLE_MAILBOX:
        print("\n📬 Registering Mailbox agent directly with Agentverse challenge-response...")
        try:
            profile = coordinator._build_registration_profile()
            reg_data = RegistrationRequest(
                address=coordinator.address,
                name=coordinator.name,
                handle=coordinator._handle,
                url=None,
                profile=profile,
                endpoints=coordinator._endpoints,
                protocols=list(coordinator.protocols.keys()),
                metadata=coordinator.metadata,
            )
            req = AgentverseConnectRequest(
                user_token=agentverse_key,
                agent_type="mailbox",
            )
            resp = asyncio.run(register_in_agentverse(
                request=req,
                identity=coordinator._identity,
                prefix=coordinator._prefix,
                agentverse=coordinator._agentverse,
                agent_details=reg_data,
            ))
            if resp.success:
                print(f"\n✅ SUCCESS: Mailbox registered for '{agent_handle}' ({coordinator.address}) on Agentverse!")
                print("👉 The agent is now connected to Agentverse mailbox. No Inspector click needed!")
            else:
                print(f"\n⚠️ Registration returned unsuccessful: {resp.detail}")
        except Exception as exc:
            print(f"\n❌ Error registering mailbox: {exc}")
        return

    endpoint = cfg.AGENT_PUBLIC_ENDPOINT or os.environ.get("AGENT_ENDPOINT", "").strip()
    if not endpoint:
        endpoint = "https://bullheadedly-noncrustaceous-abdul.ngrok-free.dev"
    if not endpoint.endswith("/submit"):
        endpoint = f"{endpoint.rstrip('/')}/submit"

    print(f"  • Endpoint:            {endpoint}")
    print(f"  • Seed Prefix:         {agent_seed[:10]}...")

    try:
        success = register_chat_agent(
            agent_handle,
            endpoint,
            active=True,
            credentials=RegistrationRequestCredentials(
                agentverse_api_key=agentverse_key,
                agent_seed_phrase=agent_seed,
            ),
            description="Delhi Metro multi-agent booking system powered by ONDC:TRV11 and live vehicle telemetry.",
            starter_prompts=[
                "Book 2 metro tickets from Kashmere Gate to Rajiv Chowk",
                "Check fare from Hauz Khas to Noida Sector 18",
            ],
        )
        if success:
            print(f"\n✅ SUCCESS: 'dmrcbook' ({coordinator.address}) successfully registered and activated on Agentverse!")
            print("👉 You can now return to the Agentverse tab and click 'Evaluate my agent's registration'.")
        else:
            print("\n⚠️ Registration returned False.")
    except AgentverseRequestError as exc:
        print(f"\n❌ AgentverseRequestError: {exc}")
    except Exception as exc:
        print(f"\n❌ Unexpected Error: {exc}")

if __name__ == "__main__":
    main()
