from typing import List, Dict, Any
from .storage import Store


def active_booked(store: Store) -> List[Dict[str, Any]]:
    now = store.now()
    return [b for b in store.bookings.get('trending', []) if b.get('start',0) <= now < b.get('end',0)]


def compute_organic(store: Store) -> List[Dict[str, Any]]:
    """Placeholder organic ranking.

    You can later plug real analytics (volume velocity, unique buyers, etc).
    For now it uses recent buy counts stored in store.seen['stats'].
    """
    stats = store.seen.get('stats', {})
    items = []
    for mint, s in stats.items():
        sym = (store.tokens.get(mint, {}) or {}).get('symbol') or 'TOKEN'
        score = float(s.get('score', 0))
        pct = s.get('pct', '+0')
        items.append({"mint": mint, "symbol": sym, "score": score, "pct": pct})
    items.sort(key=lambda x: x['score'], reverse=True)
    return items


def build_top10_combined(store: Store) -> List[Dict[str, Any]]:
    booked = active_booked(store)
    # booked tokens get guaranteed presence
    booked_rows = []
    for b in booked:
        mint = b.get('mint')
        sym = (store.tokens.get(mint, {}) or {}).get('symbol') or 'TOKEN'
        booked_rows.append({"mint": mint, "symbol": sym, "pct": '+0'})

    organic = compute_organic(store)
    rows = []
    used = set()
    for r in booked_rows:
        if r['mint'] in used:
            continue
        rows.append(r)
        used.add(r['mint'])
        if len(rows) >= 10:
            break

    if len(rows) < 10:
        for r in organic:
            if r['mint'] in used:
                continue
            rows.append({"mint": r['mint'], "symbol": r['symbol'], "pct": r.get('pct','+0')})
            used.add(r['mint'])
            if len(rows) >= 10:
                break

    # pad
    while len(rows) < 10:
        rows.append({"mint": "", "symbol": "SYMBOL", "pct": "+0"})

    return rows
