"""
Pre-Flight Production Readiness Verifier.
Inspects `.env.prod` line-by-line and checks if your production configuration
is ready to book genuine tickets that open physical Delhi Metro turnstiles.
"""

import os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

def check_prod_readiness():
    prod_env_path = PROJECT_ROOT / ".env.prod"
    if not prod_env_path.exists():
        print("❌ .env.prod file not found!")
        return

    # Parse .env.prod
    env_vars = {}
    with prod_env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")

    print("\n" + "=" * 80)
    print("🔍 DELHI METRO MULTI-AGENT BAP — PRODUCTION READINESS AUDIT")
    print("=" * 80)

    score = 0
    total = 5

    # 1. OTD Delhi API Key
    otd_key = env_vars.get("OTD_DELHI_API_KEY", "")
    if otd_key and not otd_key.startswith("REPLACE"):
        print("  ✅ [1/5] Delhi OTD Live Transit API Key : CONFIGURED (Authorized for ~5,300+ live vehicles)")
        score += 1
    else:
        print("  ❌ [1/5] Delhi OTD Live Transit API Key : MISSING (Register on https://otd.delhi.gov.in/)")

    # 2. ASI:One LLM API Key
    asi_key = env_vars.get("ASI_ONE_API_KEY", "")
    if asi_key and not asi_key.startswith("REPLACE"):
        print("  ✅ [2/5] ASI:One AI Engine API Key    : CONFIGURED (Model: asi1-mini)")
        score += 1
    else:
        print("  ❌ [2/5] ASI:One AI Engine API Key    : MISSING (Get from https://api.asi1.ai/)")

    # 3. ONDC Production Signing Key
    ondc_key = env_vars.get("ONDC_SIGNING_PRIVATE_KEY", "")
    if ondc_key and not ondc_key.startswith("REPLACE") and len(ondc_key) >= 32:
        print("  ✅ [3/5] ONDC Ed25519 Signing Key      : CONFIGURED")
        score += 1
    else:
        print("  ❌ [3/5] ONDC Ed25519 Signing Key      : NOT CONFIGURED")
        print("      👉 Run `python scripts/generate_production_keys.py` to generate your keypair,")
        print("         then upload the Public Key to ONDC Portal (https://portal.ondc.org).")

    # 4. Fetch.ai Mainnet Wallet Seeds
    coord_seed = env_vars.get("COORDINATOR_SEED", "")
    if coord_seed and not coord_seed.startswith("REPLACE"):
        print("  ✅ [4/5] Fetch.ai Mainnet Wallet Seed  : CONFIGURED")
        score += 1
    else:
        print("  ❌ [4/5] Fetch.ai Mainnet Wallet Seed  : DEFAULT/PLACEHOLDER")
        print("      👉 Run `python scripts/generate_production_keys.py` to get high-entropy production seeds,")
        print("         and fund the coordinator wallet with real FET on Fetch-hub-4 mainnet.")

    # 5. Live Fiat Payout Webhook (DMRC RSF 2.0 Settlement)
    payout_url = env_vars.get("INR_PAYOUT_WEBHOOK_URL", "")
    payout_key = env_vars.get("INR_PAYOUT_API_KEY", "")
    if payout_url and not payout_url.startswith("REPLACE") and payout_key:
        print("  ✅ [5/5] Live INR Payout API Webhook   : CONFIGURED (RazorpayX / Cashfree)")
        score += 1
    else:
        print("  ⚠️ [5/5] Live INR Payout API Webhook   : INCOMPLETE")
        print("      👉 Configure `INR_PAYOUT_WEBHOOK_URL` (e.g. RazorpayX / Cashfree) and `INR_PAYOUT_API_KEY`")
        print("         so the BAP can remit real Indian Rupees to DMRC's account (dmrc.ondc@sbi).")

    print("\n" + "-" * 80)
    print(f"Readiness Score: {score}/{total} components configured.")
    if score == total:
        print("🚀 ALL PRODUCTION RAILS CONFIGURED! You can now execute:")
        print("   python chat_cli.py --env prod")
    else:
        print("ℹ️  You can continue testing freely in DEV mode (auto-sandbox + testnet):")
        print("   python chat_cli.py --env dev")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    check_prod_readiness()
