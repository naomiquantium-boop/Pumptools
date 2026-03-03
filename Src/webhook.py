"""Webhook ingestion endpoint.

This is where your Solana indexer (Helius webhook recommended) POSTs swap/buy events.

We keep it provider-agnostic:
- Expect JSON with at least: mint, symbol, buyer, sol, token_amount, tx_sig

If using Helius Enhanced Webhooks, you will likely need a small mapper. You can either:
- configure Helius to send parsed swaps and update `extract_event()` below
- or use a tiny middleware.
"""

from typing import Any, Dict, Optional


def extract_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Accept either direct format or list
    if isinstance(payload, list) and payload:
        payload = payload[0]

    # Direct passthrough format
    if payload.get('mint') and payload.get('tx_sig'):
        return payload

    # Helius enhanced: try to map
    # Common structure: {"type":"SWAP", "source":"JUPITER", "tokenTransfers": [...], "nativeTransfers": [...], ...}
    try:
        tx_sig = payload.get('signature') or payload.get('transactionSignature')
        if not tx_sig:
            return None

        # find SOL spent (native transfer out by buyer)
        buyer = None
        sol_spent = 0.0
        for nt in payload.get('nativeTransfers', []) or []:
            if (nt.get('amount') or 0) > 0 and nt.get('fromUserAccount') and nt.get('toUserAccount'):
                # heuristic: buyer is fromUserAccount
                buyer = buyer or nt.get('fromUserAccount')
                sol_spent = max(sol_spent, float(nt.get('amount', 0)) / 1e9)

        mint = None
        token_amt = None
        symbol = None
        for tt in payload.get('tokenTransfers', []) or []:
            mint = mint or tt.get('mint')
            token_amt = token_amt or tt.get('tokenAmount')
            # symbol not always present

        if not mint:
            return None

        return {
            "mint": mint,
            "symbol": symbol or "TOKEN",
            "buyer": buyer or "",
            "sol": sol_spent,
            "token_amount": token_amt,
            "tx_sig": tx_sig,
        }
    except Exception:
        return None
