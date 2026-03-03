from typing import Dict, Any, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .templates import channel_buy_text, group_buy_text
from .ads import pick_ad
from .config import TRENDING_CHANNEL_CHAT_ID, DEFAULT_MIN_BUY_SOL, TRENDING_CHANNEL_USERNAME, BOT_DEEP_TRENDING


def make_group_buttons(ev: Dict[str, Any]) -> InlineKeyboardMarkup:
    tx = ev.get('tx_url') or ''
    dex = ev.get('chart_url') or ''
    tg = ev.get('tg_url') or TRENDING_CHANNEL_USERNAME
    tr = TRENDING_CHANNEL_USERNAME
    buttons = [
        InlineKeyboardButton('TX', url=tx) if tx else InlineKeyboardButton('TX', url=tr),
        InlineKeyboardButton('DexS', url=dex) if dex else InlineKeyboardButton('DexS', url=tr),
        InlineKeyboardButton('Trending', url=tr),
    ]
    return InlineKeyboardMarkup([buttons])


def make_channel_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton('📈 Book Trending', url=BOT_DEEP_TRENDING)]])


def should_post_group(store, chat_id: int, mint: str, sol_amt: float) -> bool:
    g = store.groups.get(str(chat_id))
    if not g:
        return False
    if mint not in (g.get('enabled_mints') or []):
        return False
    min_buy = float(g.get('min_buy', DEFAULT_MIN_BUY_SOL))
    return sol_amt >= min_buy


def should_post_channel(store, mint: str, sol_amt: float) -> bool:
    # channel posting controlled by tokens config: tokens[mint].post_to_channel
    t = store.tokens.get(mint) or {}
    if not t.get('post_to_channel', True):
        return False
    return sol_amt >= DEFAULT_MIN_BUY_SOL


def format_links(ev: Dict[str, Any]) -> Dict[str, Any]:
    sig = ev.get('tx_sig')
    mint = ev.get('mint')
    ev['tx_url'] = ev.get('tx_url') or (f"https://solscan.io/tx/{sig}" if sig else None)
    ev['chart_url'] = ev.get('chart_url') or (f"https://dexscreener.com/solana/{mint}" if mint else None)
    # placeholders; you can wire real buy link later
    ev['buy_url'] = ev.get('buy_url') or (f"https://jup.ag/swap/SOL-{mint}" if mint else None)
    ev['listing_url'] = ev.get('listing_url')
    ev['tg_url'] = ev.get('tg_url')
    return ev


def update_stats(store, mint: str, sol_amt: float):
    stats = store.seen.setdefault('stats', {})
    s = stats.setdefault(mint, {'score': 0, 'pct': '+0'})
    s['score'] = float(s.get('score', 0)) + sol_amt


def build_channel_message(store, ev: Dict[str, Any]) -> str:
    ad = pick_ad(store)
    return channel_buy_text(ev, ad_line=ad)


def build_group_message(store, ev: Dict[str, Any]) -> str:
    ad = pick_ad(store)
    return group_buy_text(ev, ad_line=ad)
