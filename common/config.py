"""
Environment-driven configuration for OTD Delhi, ONDC:TRV11, and Fetch.ai uAgents.
Supports dynamic multi-environment switching: `dev`, `test`, `prod`.
"""

import logging
import os
from pathlib import Path
from typing import List, Tuple
import certifi
from dotenv import load_dotenv

logger = logging.getLogger("config")

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", certifi.where())

# Cloud Run / latency fix: uAgents defaults ALMANAC_API_TIMEOUT_SECONDS to 1.0s,
# which causes aiohttp asyncio.TimeoutError during overseas TLS handshakes to Agentverse.
try:
    import uagents.config as _uagents_cfg
    import uagents.registration as _uagents_reg

    _uagents_cfg.ALMANAC_API_TIMEOUT_SECONDS = 15.0
    _uagents_reg.ALMANAC_API_TIMEOUT_SECONDS = 15.0
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 0. Multi-Environment Resolution (dev | test | prod) ──
# 1. Base .env file (if present)
load_dotenv(PROJECT_ROOT / ".env")

# 2. Determine active APP_ENV (default: "dev")
APP_ENV: str = os.getenv("APP_ENV", "dev").lower().strip()
if APP_ENV not in ("dev", "test", "staging", "prod", "production"):
    logger.warning(f"Unrecognized APP_ENV='{APP_ENV}', defaulting to 'dev'")
    APP_ENV = "dev"
if APP_ENV in ("staging", "test"):
    APP_ENV = "test"
if APP_ENV in ("production", "prod"):
    APP_ENV = "prod"

# 3. Environment-specific overrides (.env.dev, .env.test, .env.prod)
env_specific_file = PROJECT_ROOT / f".env.{APP_ENV}"
if env_specific_file.exists():
    load_dotenv(env_specific_file, override=True)


# ── Environment Preset Defaults ──
ENV_PRESETS = {
    "dev": {
        "ONDC_GATEWAY_URL": "http://localhost:3000",
        "ONDC_DEFAULT_BPP_ID": "bpp.sandbox.ondc.org",
        "ONDC_DEFAULT_BPP_URI": "http://localhost:3000",
        "FETCHAI_NETWORK": "testnet",
        "FETCHAI_DENOM": "atestfet",
        "ENABLE_LOCAL_BECKN_SANDBOX": True,
        "INR_PAYOUT_PROVIDER": "simulated",
        "STRICT_CONFIG_VALIDATION": False,
        "RIDER_DEFAULT_NAME": "Dev Rider",
        "RIDER_DEFAULT_PHONE": "+919876543210",
        "RIDER_DEFAULT_EMAIL": "rider@dev.local",
    },
    "test": {
        "ONDC_GATEWAY_URL": "https://staging.gateway.ondc.org",
        "ONDC_DEFAULT_BPP_ID": "dmrc-bpp.staging.ondc.org",
        "ONDC_DEFAULT_BPP_URI": "https://dmrc-bpp.staging.ondc.org",
        "FETCHAI_NETWORK": "testnet",
        "FETCHAI_DENOM": "atestfet",
        "ENABLE_LOCAL_BECKN_SANDBOX": False,
        "INR_PAYOUT_PROVIDER": "staging_webhook",
        "STRICT_CONFIG_VALIDATION": False,
        "RIDER_DEFAULT_NAME": "Staging QA Tester",
        "RIDER_DEFAULT_PHONE": "+919876543210",
        "RIDER_DEFAULT_EMAIL": "qa@metro-bap.staging",
    },
    "prod": {
        "ONDC_GATEWAY_URL": "https://prod.gateway.ondc.org",
        "ONDC_DEFAULT_BPP_ID": "dmrc-bpp.ondc.org",
        "ONDC_DEFAULT_BPP_URI": "https://dmrc-bpp.ondc.org",
        "FETCHAI_NETWORK": "mainnet",
        "FETCHAI_DENOM": "afet",
        "ENABLE_LOCAL_BECKN_SANDBOX": False,
        "INR_PAYOUT_PROVIDER": "live_webhook",
        "STRICT_CONFIG_VALIDATION": True,
        "RIDER_DEFAULT_NAME": "Metro Passenger",
        "RIDER_DEFAULT_PHONE": "",
        "RIDER_DEFAULT_EMAIL": "",
    },
}

current_preset = ENV_PRESETS[APP_ENV]


# ── 1. Delhi Government Open Transit Database (OTD) ──
OTD_DELHI_API_KEY: str = os.getenv(
    "OTD_DELHI_API_KEY", ""
)
OTD_REALTIME_VEHICLE_POSITIONS_URL: str = os.getenv(
    "OTD_REALTIME_VEHICLE_POSITIONS_URL",
    "https://otd.delhi.gov.in/api/realtime/VehiclePositions.pb",
)
OTD_DMRC_STATIC_GTFS_DIR: Path = Path(
    os.getenv("OTD_DMRC_STATIC_GTFS_DIR", str(PROJECT_ROOT / "data" / "dmrc_gtfs"))
)
# Live Official DMRC REST API Endpoint
DMRC_API_BASE_URL: str = os.getenv(
    "DMRC_API_BASE_URL", "https://dmrc-rest-api.vercel.app/api/v1/dmrc"
).rstrip("/")


# ── 2. ONDC:TRV11 (v2.1.0) Gateway & BPP Configuration ──
ONDC_GATEWAY_URL: str = os.getenv(
    "ONDC_GATEWAY_URL", current_preset["ONDC_GATEWAY_URL"]
).rstrip("/")
ONDC_DEFAULT_BPP_ID: str = os.getenv(
    "ONDC_DEFAULT_BPP_ID", current_preset["ONDC_DEFAULT_BPP_ID"]
)
ONDC_DEFAULT_BPP_URI: str = os.getenv(
    "ONDC_DEFAULT_BPP_URI", current_preset["ONDC_DEFAULT_BPP_URI"]
).rstrip("/")
ONDC_BAP_ID: str = os.getenv(
    "ONDC_BAP_ID", f"bap.metro-agent.{APP_ENV}.agentverse.ai"
)
ONDC_BAP_URI: str = os.getenv("ONDC_BAP_URI", "http://localhost:8000/beckn").rstrip("/")
ONDC_CITY_CODE: str = os.getenv("ONDC_CITY_CODE", "std:011")
ONDC_COUNTRY_CODE: str = os.getenv("ONDC_COUNTRY_CODE", "IND")
ONDC_UNIQUE_KEY_ID: str = os.getenv("ONDC_UNIQUE_KEY_ID", f"key-{APP_ENV}-1")
ONDC_SIGNING_PRIVATE_KEY: str = os.getenv("ONDC_SIGNING_PRIVATE_KEY", "").strip()
ONDC_HTTP_TIMEOUT: float = float(os.getenv("ONDC_HTTP_TIMEOUT", "15.0"))

ENABLE_LOCAL_BECKN_SANDBOX: bool = (
    os.getenv(
        "ENABLE_LOCAL_BECKN_SANDBOX",
        str(current_preset["ENABLE_LOCAL_BECKN_SANDBOX"]),
    ).lower()
    in ("1", "true", "yes")
)


# ── 3. Fetch.ai uAgents & Blockchain Settlement Configuration ──
FETCHAI_NETWORK: str = os.getenv(
    "FETCHAI_NETWORK", current_preset["FETCHAI_NETWORK"]
).lower().strip()
FETCHAI_DENOM: str = os.getenv(
    "FETCHAI_DENOM", current_preset["FETCHAI_DENOM"]
).strip()

COORDINATOR_SEED: str = os.getenv(
    "COORDINATOR_SEED", f"metro_bap_coordinator_{APP_ENV}_seed_2026"
)
CATALOG_AGENT_SEED: str = os.getenv(
    "CATALOG_AGENT_SEED", f"metro_bpp_catalog_{APP_ENV}_seed_2026"
)
TICKETING_AGENT_SEED: str = os.getenv(
    "TICKETING_AGENT_SEED", f"metro_bpp_ticketing_{APP_ENV}_seed_2026"
)
INR_TO_TESTNET_FET: float = float(os.getenv("INR_TO_TESTNET_FET", "0.01"))


# ── 4. ONDC:TRV11 DMRC INR Settlement & BAP Nodal Payout (RSF 2.0) ──
INR_PAYOUT_PROVIDER: str = os.getenv(
    "INR_PAYOUT_PROVIDER", current_preset["INR_PAYOUT_PROVIDER"]
).lower().strip()

BAP_SETTLEMENT_TYPE: str = os.getenv("BAP_SETTLEMENT_TYPE", "UPI")
BAP_NODAL_BANK_NAME: str = os.getenv("BAP_NODAL_BANK_NAME", "YES BANK")
BAP_NODAL_ACCOUNT_NO: str = os.getenv("BAP_NODAL_ACCOUNT_NO", "000180900001234")
BAP_NODAL_IFSC: str = os.getenv("BAP_NODAL_IFSC", "YESB0000001")
BAP_NODAL_UPI_VPA: str = os.getenv("BAP_NODAL_UPI_VPA", "bap.metro-agent@yesbank")

DMRC_DEFAULT_BENEFICIARY_NAME: str = os.getenv(
    "DMRC_DEFAULT_BENEFICIARY_NAME", "Delhi Metro Rail Corporation Ltd"
)
DMRC_DEFAULT_UPI_VPA: str = os.getenv("DMRC_DEFAULT_UPI_VPA", "dmrc.ondc@sbi")
DMRC_DEFAULT_BANK_ACCOUNT_NO: str = os.getenv(
    "DMRC_DEFAULT_BANK_ACCOUNT_NO", "10891234567"
)
DMRC_DEFAULT_IFSC_CODE: str = os.getenv("DMRC_DEFAULT_IFSC_CODE", "SBIN0000691")
DMRC_DEFAULT_BANK_NAME: str = os.getenv(
    "DMRC_DEFAULT_BANK_NAME", "State Bank of India"
)

INR_PAYOUT_WEBHOOK_URL: str = os.getenv("INR_PAYOUT_WEBHOOK_URL", "").strip()
INR_PAYOUT_API_KEY: str = os.getenv("INR_PAYOUT_API_KEY", "").strip()


# ── 5. Passenger / Rider Defaults ──
RIDER_DEFAULT_NAME: str = os.getenv(
    "RIDER_DEFAULT_NAME", current_preset["RIDER_DEFAULT_NAME"]
)
RIDER_DEFAULT_PHONE: str = os.getenv(
    "RIDER_DEFAULT_PHONE", current_preset["RIDER_DEFAULT_PHONE"]
)
RIDER_DEFAULT_EMAIL: str = os.getenv(
    "RIDER_DEFAULT_EMAIL", current_preset["RIDER_DEFAULT_EMAIL"]
)


# ── 6. Storage & Tickets Path ──
TICKETS_DIR: Path = PROJECT_ROOT / "tickets" / APP_ENV
TICKETS_DIR.mkdir(parents=True, exist_ok=True)


# ── 7. ASI:One LLM API Configuration ──
ASI_ONE_API_KEY: str = os.getenv("ASI_ONE_API_KEY", "").strip()
ASI_ONE_BASE_URL: str = os.getenv("ASI_ONE_BASE_URL", "https://api.asi1.ai/v1").rstrip("/")
ASI_ONE_MODEL: str = os.getenv("ASI_ONE_MODEL", "asi1-mini").strip()

# Optional Agentverse Mailbox & Public Endpoint
ENABLE_MAILBOX: bool = os.getenv("ENABLE_MAILBOX", "false").lower() in ("1", "true", "yes")
AGENT_PUBLIC_ENDPOINT: str = os.getenv("AGENT_ENDPOINT", os.getenv("AGENT_PUBLIC_ENDPOINT", "")).strip()
AGENTVERSE_KEY: str = os.getenv("AGENTVERSE_KEY", "").strip()
AGENT_HANDLE: str = os.getenv("AGENT_HANDLE", "dmrcmetro").strip()
AGENT_NAME: str = os.getenv("AGENT_NAME", "Delhi Metro Ticket Booking").strip()

STRICT_CONFIG_VALIDATION: bool = (
    os.getenv(
        "STRICT_CONFIG_VALIDATION",
        str(current_preset["STRICT_CONFIG_VALIDATION"]),
    ).lower()
    in ("1", "true", "yes")
)


def validate_environment() -> Tuple[bool, List[str]]:
    """
    Validates current environment settings against production safety and operational requirements.
    In 'prod' mode, fails if dummy seeds, mock gateways, or empty bank payout credentials are used.
    """
    issues: List[str] = []

    for label, value in (
        ("COORDINATOR_SEED", COORDINATOR_SEED),
        ("CATALOG_AGENT_SEED", CATALOG_AGENT_SEED),
        ("TICKETING_AGENT_SEED", TICKETING_AGENT_SEED),
    ):
        if value.endswith("_seed_2026") or value.endswith("_dev_seed"):
            issues.append(
                f"[SECURITY] {label} is a public default from this repo; anyone can impersonate the agent. "
                "Set a private random seed in .env."
            )

    if APP_ENV == "prod":
        if not ONDC_SIGNING_PRIVATE_KEY:
            issues.append(
                "[PROD CRITICAL] ONDC_SIGNING_PRIVATE_KEY is missing. Production ONDC requires an Ed25519 private key registered on registry.ondc.org."
            )
        if "localhost" in ONDC_GATEWAY_URL or "127.0.0.1" in ONDC_GATEWAY_URL:
            issues.append(
                f"[PROD CRITICAL] ONDC_GATEWAY_URL is pointing to localhost ({ONDC_GATEWAY_URL}). In production, use 'https://prod.gateway.ondc.org'."
            )
        if ENABLE_LOCAL_BECKN_SANDBOX:
            issues.append(
                "[PROD CRITICAL] ENABLE_LOCAL_BECKN_SANDBOX cannot be True in production environment."
            )
        if FETCHAI_NETWORK != "mainnet":
            issues.append(
                f"[PROD WARNING] FETCHAI_NETWORK is set to '{FETCHAI_NETWORK}' instead of 'mainnet'."
            )
        if INR_PAYOUT_PROVIDER == "live_webhook" and not INR_PAYOUT_WEBHOOK_URL:
            issues.append(
                "[PROD CRITICAL] INR_PAYOUT_WEBHOOK_URL is required in production for real fiat payout remittance to DMRC."
            )
    elif APP_ENV == "test":
        if "localhost" in ONDC_GATEWAY_URL and not ENABLE_LOCAL_BECKN_SANDBOX:
            issues.append(
                "[TEST] ONDC_GATEWAY_URL is localhost but ENABLE_LOCAL_BECKN_SANDBOX is False. Local sandbox will not respond."
            )

    return len(issues) == 0, issues
