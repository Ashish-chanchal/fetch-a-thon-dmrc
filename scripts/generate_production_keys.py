"""
Production Cryptographic Key Generator for ONDC BAP & Fetch.ai Agents.
Generates:
1. ONDC Ed25519 Signing Keypair (RFC 8032, Raw 32-byte Base64) for Beckn Request Authorization.
2. ONDC X25519 Encryption Keypair (RFC 7748, Raw 32-byte Base64) for Beckn Payload Decryption.
3. Cryptographically secure random seeds for Fetch.ai Mainnet Agents.
"""

import base64
import os
import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption


def generate_ondc_keys():
    # 1. Ed25519 Signing Keypair (32-byte raw bytes)
    signing_priv = Ed25519PrivateKey.generate()
    signing_priv_bytes = signing_priv.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    signing_pub_bytes = signing_priv.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )

    signing_priv_b64 = base64.b64encode(signing_priv_bytes).decode("utf-8")
    signing_pub_b64 = base64.b64encode(signing_pub_bytes).decode("utf-8")

    # 2. X25519 Encryption Keypair (32-byte raw bytes)
    enc_priv = X25519PrivateKey.generate()
    enc_priv_bytes = enc_priv.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    enc_pub_bytes = enc_priv.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )

    enc_priv_b64 = base64.b64encode(enc_priv_bytes).decode("utf-8")
    enc_pub_b64 = base64.b64encode(enc_pub_bytes).decode("utf-8")

    # 3. High-entropy seeds for Fetch.ai mainnet agents
    coordinator_seed = f"metro_bap_coord_prod_{secrets.token_hex(16)}"
    catalog_seed = f"metro_bpp_cat_prod_{secrets.token_hex(16)}"
    ticketing_seed = f"metro_bpp_tick_prod_{secrets.token_hex(16)}"

    print("=" * 80)
    print("🔑 GENERATED ONDC PRODUCTION KEYS & MAINNET AGENT SEEDS")
    print("=" * 80)
    print("\n[1] ONDC SIGNING KEYPAIR (For Beckn Request Signing):")
    print(f"  • Private Key (ONDC_SIGNING_PRIVATE_KEY) : {signing_priv_b64}")
    print(f"  • Public Key  (Upload to ONDC Registry) : {signing_pub_b64}")
    print("\n[2] ONDC ENCRYPTION KEYPAIR (For Beckn Payload Encryption):")
    print(f"  • Private Key (Store safely in vault)   : {enc_priv_b64}")
    print(f"  • Public Key  (Upload to ONDC Registry) : {enc_pub_b64}")
    print("\n[3] FETCH.AI MAINNET AGENT SEEDS:")
    print(f"  • COORDINATOR_SEED                      : {coordinator_seed}")
    print(f"  • CATALOG_AGENT_SEED                    : {catalog_seed}")
    print(f"  • TICKETING_AGENT_SEED                  : {ticketing_seed}")
    print("=" * 80)
    print("\n👉 To use these in production, copy the values above into your `.env.prod` file.")

    return {
        "signing_priv_b64": signing_priv_b64,
        "signing_pub_b64": signing_pub_b64,
        "enc_priv_b64": enc_priv_b64,
        "enc_pub_b64": enc_pub_b64,
        "coordinator_seed": coordinator_seed,
        "catalog_seed": catalog_seed,
        "ticketing_seed": ticketing_seed,
    }


if __name__ == "__main__":
    generate_ondc_keys()
