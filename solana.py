import requests
from typing import Optional, Dict, Any
from .config import SOLANA_RPC_URL, PAY_VERIFY_LOOKBACK, BOOKING_WALLET


def rpc(method: str, params: list) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(SOLANA_RPC_URL, json=payload, timeout=25)
    r.raise_for_status()
    return r.json().get("result")


def verify_payment(reference: str, expected_sol: float) -> Optional[Dict[str, Any]]:
    """Basic verification: scan recent txns to BOOKING_WALLET for a SOL transfer with memo containing reference.

    Returns dict with tx signature and payer if found.

    NOTE: wallets don't always include memo; encourage users to include it.
    """
    try:
        sigs = rpc("getSignaturesForAddress", [BOOKING_WALLET, {"limit": PAY_VERIFY_LOOKBACK}]) or []
    except Exception:
        return None

    for s in sigs:
        sig = s.get("signature")
        if not sig:
            continue
        try:
            tx = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        except Exception:
            continue
        if not tx:
            continue

        meta = tx.get("meta") or {}
        txn = tx.get("transaction") or {}
        msg = (txn.get("message") or {})
        accs = msg.get("accountKeys") or []

        # memo check
        has_ref = False
        for ix in (msg.get("instructions") or []):
            prog = (ix.get("program") or "")
            if prog == "spl-memo" or ix.get("programId") == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                memo = (ix.get("parsed") or {}).get("memo")
                if isinstance(memo, str) and reference in memo:
                    has_ref = True
                    break
            # some parsers put memo in data
            data = ix.get("data")
            if isinstance(data, str) and reference in data:
                has_ref = True
                break
        if not has_ref:
            continue

        # Estimate SOL amount received by BOOKING_WALLET: compare pre/post balances for that account
        # Find index
        idx = None
        for i, k in enumerate(accs):
            # jsonParsed accountKeys may be dicts
            if isinstance(k, dict):
                if k.get("pubkey") == BOOKING_WALLET:
                    idx = i
                    break
            elif k == BOOKING_WALLET:
                idx = i
                break
        if idx is None:
            continue
        pre = (meta.get("preBalances") or [])[idx] if meta.get("preBalances") else None
        post = (meta.get("postBalances") or [])[idx] if meta.get("postBalances") else None
        if pre is None or post is None:
            continue
        received = (post - pre) / 1e9
        if received + 1e-9 < expected_sol:
            continue

        # payer is usually first account
        payer = None
        if accs:
            k0 = accs[0]
            payer = k0.get("pubkey") if isinstance(k0, dict) else k0

        return {"signature": sig, "payer": payer, "received": received}

    return None
