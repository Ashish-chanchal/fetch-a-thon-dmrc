"""
On-chain verification of a FET payment made by a rider to the coordinator's wallet.

The check reads the transaction from the Fetch.ai REST API and requires a successful tx
containing a `transfer` event with the expected sender, recipient and at least the expected amount.
"""

from decimal import Decimal
import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from common.config import FETCHAI_DENOM, FETCHAI_NETWORK

logger = logging.getLogger("fet_payment")

_COIN_RE = re.compile(r"^(\d+)([a-zA-Z][a-zA-Z0-9/:._-]*)$")


def rest_base_url() -> str:
    if FETCHAI_NETWORK == "mainnet":
        return "https://rest-fetchhub.fetch.ai"
    return "https://rest-dorado.fetch.ai"


def explorer_tx_url(tx_hash: str) -> str:
    if FETCHAI_NETWORK == "mainnet":
        return f"https://fetchstation.app/transactions/{tx_hash}"
    return f"https://explore-dorado.fetch.ai/transactions/{tx_hash}"


def fet_to_base_units(amount_fet: str) -> int:
    return int(Decimal(str(amount_fet)) * (10**18))


def _transfers(tx_response: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flattens every `transfer` event into {sender, recipient, amount} dicts."""
    events: List[Dict[str, Any]] = list(tx_response.get("events") or [])
    for log in tx_response.get("logs") or []:
        events.extend(log.get("events") or [])

    found: List[Dict[str, str]] = []
    seen = set()
    for ev in events:
        if ev.get("type") != "transfer":
            continue
        current: Dict[str, str] = {}
        for attr in ev.get("attributes") or []:
            key = attr.get("key")
            if isinstance(key, bytes):
                key = key.decode()
            current[str(key)] = str(attr.get("value", ""))
        # A transfer event can repeat recipient/sender/amount groups; the dict form keeps the last group,
        # which is enough here because we compare against a single expected transfer.
        sig = (current.get("sender"), current.get("recipient"), current.get("amount"))
        if sig not in seen:
            seen.add(sig)
            found.append(current)
    return found


def _amount_in_denom(amount_field: str, denom: str) -> int:
    total = 0
    for part in str(amount_field).split(","):
        m = _COIN_RE.match(part.strip())
        if m and m.group(2) == denom:
            total += int(m.group(1))
    return total


async def verify_fet_payment(
    tx_hash: str, payer: str, recipient: str, expected_amount_fet: str
) -> Tuple[bool, str]:
    """
    Returns (ok, reason). Retries briefly because a fresh tx may not be indexed yet.
    An empty `payer` accepts a transfer from any sender (used when the rider pastes a tx hash by hand).
    """
    if not tx_hash or not recipient:
        return False, "missing tx hash or recipient"

    expected = fet_to_base_units(expected_amount_fet)
    url = f"{rest_base_url()}/cosmos/tx/v1beta1/txs/{tx_hash}"
    last_reason = "transaction not found"

    import asyncio

    for attempt in range(6):
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                tx_response = resp.json().get("tx_response") or {}
                if int(tx_response.get("code", -1)) != 0:
                    return False, f"transaction failed on chain (code {tx_response.get('code')})"
                for t in _transfers(tx_response):
                    if (
                        (not payer or t.get("sender") == payer)
                        and t.get("recipient") == recipient
                        and _amount_in_denom(t.get("amount", ""), FETCHAI_DENOM) >= expected
                    ):
                        return True, "verified"
                return False, "no matching transfer (sender, recipient or amount differs)"
            last_reason = f"explorer returned HTTP {resp.status_code}"
        except Exception as exc:
            last_reason = f"ledger query error: {exc}"
        await asyncio.sleep(2.0)

    return False, last_reason


def _tx_time(tx_response: Dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(tx_response.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


async def find_incoming_payment(
    recipient: str, expected_amount_fet: str, since_ts: float, exclude: Iterable[str] = ()
) -> Optional[Tuple[str, str]]:
    """
    Looks for a successful transfer of at least `expected_amount_fet` to `recipient` made after `since_ts`
    (unix seconds). Returns (tx_hash, sender) or None. Lets a rider who paid from any wallet just tap
    "I've paid" instead of pasting a transaction hash.
    """
    expected = fet_to_base_units(expected_amount_fet)
    skip = {h.upper() for h in exclude}
    params_variants = [
        # Cosmos SDK >= 0.50 uses `query`, older nodes use `events`.
        {"query": f"transfer.recipient='{recipient}'", "order_by": "ORDER_BY_DESC", "pagination.limit": "25"},
        {"events": f"transfer.recipient='{recipient}'", "order_by": "ORDER_BY_DESC", "pagination.limit": "25"},
    ]
    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
        for params in params_variants:
            try:
                resp = await client.get(f"{rest_base_url()}/cosmos/tx/v1beta1/txs", params=params)
            except Exception as exc:
                logger.warning(f"tx search failed: {exc}")
                continue
            if resp.status_code != 200:
                continue
            for tx_response in resp.json().get("tx_responses") or []:
                tx_hash = str(tx_response.get("txhash", "")).upper()
                if not tx_hash or tx_hash in skip or int(tx_response.get("code", -1)) != 0:
                    continue
                if _tx_time(tx_response) < since_ts - 5:
                    continue
                for t in _transfers(tx_response):
                    if t.get("recipient") == recipient and _amount_in_denom(t.get("amount", ""), FETCHAI_DENOM) >= expected:
                        return tx_hash, str(t.get("sender", ""))
            return None
    return None
