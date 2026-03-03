import secrets
from typing import Dict, Any
from .config import PRICES


def new_ref(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


def make_order(kind: str, package: str, mint: str, user_id: int) -> Dict[str, Any]:
    # kind: trending_top3 / trending_top10 / ads
    price = PRICES[kind][package]
    hours = int(package.replace('h',''))
    ref = new_ref("PT")
    return {
        "ref": ref,
        "kind": kind,
        "package": package,
        "hours": hours,
        "price": price,
        "mint": mint,
        "user_id": user_id,
        "status": "pending"
    }
