import random
from typing import Optional
from .storage import Store
from .config import DEFAULT_AD_TEXT, DEFAULT_AD_LINK


def pick_ad(store: Store) -> str:
    now = store.now()
    paid = [a for a in store.ads.get('paid', []) if a.get('start',0) <= now < a.get('end',0)]
    owner = [a for a in store.ads.get('owner', []) if a.get('start',0) <= now < a.get('end',0)]

    pool = paid if paid else owner
    if not pool:
        return f"{DEFAULT_AD_TEXT}" if not DEFAULT_AD_LINK else f"{DEFAULT_AD_TEXT}"

    a = random.choice(pool)
    text = a.get('text') or DEFAULT_AD_TEXT
    link = a.get('link')
    if link:
        return f"{text}\n{link}"
    return text
