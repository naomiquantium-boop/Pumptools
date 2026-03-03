
import os, json, time, asyncio, logging, re, html, math, secrets
from typing import Any, Dict, Optional, List, Tuple
import requests

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, CallbackQueryHandler,
    MessageHandler, ChatMemberHandler, ContextTypes, filters
)

# -------------------- LOGGING --------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("spysol_buybot")

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Solana / Helius
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_BASE = os.getenv("HELIUS_BASE", "https://api-mainnet.helius-rpc.com").strip().rstrip("/")
POLL_INTERVAL = max(2.0, float(os.getenv("POLL_INTERVAL", "2.0")))
BURST_WINDOW_SEC = int(os.getenv("BURST_WINDOW_SEC", "30"))

# Channels
TRENDING_URL = os.getenv("TRENDING_URL", "https://t.me/SpySolTrending").strip()
LISTING_URL = os.getenv("LISTING_URL", "https://t.me/SpySolListing").strip()
DEFAULT_TOKEN_TG = os.getenv("DEFAULT_TOKEN_TG", "https://t.me/SpySolEco").strip()
LEADERBOARD_HEADER_HANDLE = os.getenv("LEADERBOARD_HEADER_HANDLE", "@SpySolTrending").strip()

TRENDING_POST_CHAT_ID = os.getenv("TRENDING_POST_CHAT_ID", "").strip()  # numeric id e.g. -100...
MIRROR_TO_TRENDING = str(os.getenv("MIRROR_TO_TRENDING", "1")).strip().lower() in ("1","true","yes","on")

# Owner + payments
OWNER_IDS = [int(x) for x in re.split(r"[ ,;]+", os.getenv("OWNER_IDS", "").strip()) if x.strip().isdigit()]
PAY_WALLET = os.getenv("PAY_WALLET", "").strip()  # Solana address to receive SOL

# Pricing (SOL)
TRENDING_PRICES = os.getenv("TRENDING_PRICES", "1h=0.2,6h=0.8,24h=2.5").strip()
ADS_PRICES = os.getenv("ADS_PRICES", "1d=0.5,3d=1.2,7d=2.5").strip()

# Data files
DATA_DIR = os.getenv("DATA_DIR", "").strip()
def _data_path(p: str) -> str:
    if not p:
        return p
    if DATA_DIR and (not os.path.isabs(p)):
        return os.path.join(DATA_DIR, p)
    return p
if DATA_DIR:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass

TOKENS_FILE = _data_path(os.getenv("TOKENS_FILE", "tokens_public.json"))
GROUPS_FILE = _data_path(os.getenv("GROUPS_FILE", "groups_public.json"))
SEEN_FILE = _data_path(os.getenv("SEEN_FILE", "seen_public.json"))
ADS_FILE = _data_path(os.getenv("ADS_FILE", "ads_public.json"))
BOOKINGS_FILE = _data_path(os.getenv("BOOKINGS_FILE", "bookings_public.json"))
INVOICES_FILE = _data_path(os.getenv("INVOICES_FILE", "invoices_public.json"))
LEADERBOARD_MSG_FILE = _data_path(os.getenv("LEADERBOARD_MSG_FILE", "leaderboard_msg.json"))

# Leaderboard
LEADERBOARD_ON = str(os.getenv("LEADERBOARD_ON", "1")).strip().lower() in ("1","true","yes","on")
LEADERBOARD_INTERVAL = max(30, int(float(os.getenv("LEADERBOARD_INTERVAL", "60"))))
LEADERBOARD_ORGANIC_TOPN = max(5, int(os.getenv("LEADERBOARD_ORGANIC_TOPN", "10")))

# Ads under buy posts
DEFAULT_AD_TEXT = os.getenv("DEFAULT_AD_TEXT", "Advertise here").strip()
DEFAULT_AD_LINK = os.getenv("DEFAULT_AD_LINK", "https://t.me/vseeton").strip()
AD_ROTATE_SEC = max(15, int(os.getenv("AD_ROTATE_SEC", "60")))

# DexScreener
DEX_BASE = os.getenv("DEX_BASE", "https://api.dexscreener.com").strip().rstrip("/")

LAMPORTS_PER_SOL = 1_000_000_000

# -------------------- JSON HELPERS --------------------
def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def is_owner(user_id: int) -> bool:
    return (user_id in OWNER_IDS) if OWNER_IDS else False

# -------------------- PRICING PARSE --------------------
def _parse_price_map(s: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for part in re.split(r"[;|]+", s):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k,v = part.split("=",1)
        k = k.strip().lower()
        try:
            out[k] = float(v.strip())
        except Exception:
            pass
    return out

TRENDING_PRICE_MAP = _parse_price_map(TRENDING_PRICES)
ADS_PRICE_MAP = _parse_price_map(ADS_PRICES)

# -------------------- STATE --------------------
TOKENS: Dict[str, Dict[str, Any]] = {}          # mint -> token dict
GROUPS: Dict[str, Any] = {}                     # chat_id -> settings
SEEN: Dict[str, float] = {}                     # signature -> ts seen
ADS: Dict[str, Any] = {}                        # token_mint -> list of ads
BOOKINGS: Dict[str, Any] = {}                   # {"trending": {mint: {...}}, "ads": {mint: {...}}}
INVOICES: Dict[str, Any] = {}                   # invoice_id -> invoice object
LEADERBOARD_MSG: Dict[str, Any] = {}            # {"chat_id":..., "message_id":...}

_last_ad_rotation_ts = 0.0
_ad_rotation_idx = 0

# -------------------- SOLANA / HELIUS --------------------
def helius_get_transactions_by_address(address: str, limit: int = 20) -> List[Dict[str, Any]]:
    # Docs: GET /v0/addresses/{address}/transactions?api-key=...
    url = f"{HELIUS_BASE}/v0/addresses/{address}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": str(limit)}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    return []

def _short(addr: str, n: int = 4) -> str:
    if not addr:
        return ""
    if len(addr) <= n*2+3:
        return addr
    return f"{addr[:n]}…{addr[-n:]}"

def solscan_tx(sig: str) -> str:
    return f"https://solscan.io/tx/{sig}"

def solscan_addr(a: str) -> str:
    return f"https://solscan.io/account/{a}"

# -------------------- MARKET DATA --------------------
_md_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

def dexscreener_token_pairs(mint: str) -> List[Dict[str, Any]]:
    # Docs: token-pairs endpoint (chainId/tokenAddress)
    url = f"{DEX_BASE}/token-pairs/v1/solana/{mint}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []

def get_market_data(mint: str) -> Dict[str, Any]:
    now = time.time()
    cached = _md_cache.get(mint)
    if cached and (now - cached[0]) < 20:
        return cached[1]

    out: Dict[str, Any] = {}
    try:
        pairs = dexscreener_token_pairs(mint)
        if pairs:
            # pick best liquidity.usd if present
            def liq(p): 
                try: return float(p.get("liquidity", {}).get("usd") or 0)
                except Exception: return 0.0
            best = max(pairs, key=liq)
            out["url"] = best.get("url") or ""
            out["priceUsd"] = best.get("priceUsd")
            out["fdv"] = best.get("fdv") or best.get("marketCap")
            out["liquidityUsd"] = (best.get("liquidity") or {}).get("usd")
            out["volumeH6"] = (best.get("volume") or {}).get("h6")
            out["volumeH24"] = (best.get("volume") or {}).get("h24")
            out["pairAddress"] = best.get("pairAddress")
            out["dexId"] = best.get("dexId")
    except Exception:
        pass

    _md_cache[mint] = (now, out)
    return out

# -------------------- BOOKING / PAYMENTS --------------------
def _now() -> int:
    return int(time.time())

def _clean_expired() -> None:
    changed = False
    t = _now()
    for kind in ("trending", "ads"):
        d = BOOKINGS.get(kind, {}) if isinstance(BOOKINGS.get(kind), dict) else {}
        expired = [mint for mint, rec in d.items() if int(rec.get("expires_at", 0)) <= t]
        for mint in expired:
            d.pop(mint, None)
            changed = True
        BOOKINGS[kind] = d
    if changed:
        _save_json(BOOKINGS_FILE, BOOKINGS)

def _mk_invoice(kind: str, mint: str, duration_key: str, price_sol: float, chat_id: int, user_id: int) -> Dict[str, Any]:
    invoice_id = secrets.token_hex(8)
    inv = {
        "id": invoice_id,
        "kind": kind,  # "trending" | "ads"
        "mint": mint,
        "duration_key": duration_key,
        "price_sol": price_sol,
        "pay_wallet": PAY_WALLET,
        "created_at": _now(),
        "status": "pending",  # pending|paid|expired
        "chat_id": chat_id,
        "user_id": user_id,
    }
    INVOICES[invoice_id] = inv
    _save_json(INVOICES_FILE, INVOICES)
    return inv

def _find_invoice_by_memo(memo: str) -> Optional[Dict[str, Any]]:
    memo = (memo or "").strip()
    if not memo:
        return None
    for inv in INVOICES.values():
        if inv.get("status") == "pending" and inv.get("id") == memo:
            return inv
    return None

def _activate_booking(inv: Dict[str, Any], tx_sig: str, payer: str) -> None:
    kind = inv["kind"]
    mint = inv["mint"]
    key = inv["duration_key"]
    seconds = duration_key_to_seconds(key)
    rec = {
        "mint": mint,
        "started_at": _now(),
        "expires_at": _now() + seconds,
        "paid_by": payer,
        "tx": tx_sig,
        "duration_key": key,
        "price_sol": inv.get("price_sol"),
    }
    BOOKINGS.setdefault(kind, {})
    BOOKINGS[kind][mint] = rec
    inv["status"] = "paid"
    inv["paid_at"] = _now()
    inv["tx"] = tx_sig
    inv["payer"] = payer
    _save_json(BOOKINGS_FILE, BOOKINGS)
    _save_json(INVOICES_FILE, INVOICES)

def duration_key_to_seconds(key: str) -> int:
    key = (key or "").strip().lower()
    m = re.match(r"^(\d+)\s*(h|hr|hrs|hour|hours|d|day|days)$", key)
    if not m:
        return 3600
    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith("h"):
        return n * 3600
    return n * 86400

def _extract_memo_from_tx(tx: Dict[str, Any]) -> Optional[str]:
    # Helius enhanced tx includes "instructions" with base64 "data"; memo program is MemoSq4gq...,
    # but enhanced tx also often provides a "description" containing memo.
    # We'll do best-effort: scan description for "Memo:" and also scan instructions for programId == memo program and decode as UTF-8 if possible.
    desc = tx.get("description") or ""
    m = re.search(r"Memo:\s*([0-9a-f]{16,})", desc, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    memo_pid = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
    for ix in tx.get("instructions") or []:
        if ix.get("programId") != memo_pid:
            continue
        data = ix.get("data")
        if not data:
            continue
        # data is base58? In example it is base64-like. We'll just try base64, then fallback.
        try:
            import base64
            raw = base64.b64decode(data + "===")
            s = raw.decode("utf-8", errors="ignore").strip()
            if s:
                return s
        except Exception:
            continue
    return None

def _amount_to_sol(lamports: Any) -> float:
    try:
        return float(lamports) / LAMPORTS_PER_SOL
    except Exception:
        return 0.0

async def poll_payments(app: Application) -> None:
    """Watch PAY_WALLET for incoming SOL with memo==invoice_id (best-effort)."""
    if not PAY_WALLET or not HELIUS_API_KEY:
        return
    last_check = 0
    while True:
        try:
            _clean_expired()
            txs = helius_get_transactions_by_address(PAY_WALLET, limit=25)
            # newest first -> process old to new
            for tx in reversed(txs):
                sig = tx.get("signature") or ""
                if not sig or sig in SEEN:
                    continue
                SEEN[sig] = time.time()
                memo = _extract_memo_from_tx(tx)
                inv = _find_invoice_by_memo(memo or "")
                if not inv:
                    continue
                # find payer + amount
                payer = tx.get("feePayer") or ""
                total_in = 0.0
                for nt in tx.get("nativeTransfers") or []:
                    if nt.get("toUserAccount") == PAY_WALLET:
                        total_in += _amount_to_sol(nt.get("amount"))
                if total_in + 1e-9 < float(inv.get("price_sol") or 0):
                    continue
                _activate_booking(inv, sig, payer)
                # notify user
                try:
                    await app.bot.send_message(
                        chat_id=inv["chat_id"],
                        text=f"✅ Payment confirmed.\n\n• Type: {inv['kind']}\n• Token: {inv['mint']}\n• Duration: {inv['duration_key']}\n• Tx: {solscan_tx(sig)}"
                    )
                except Exception:
                    pass

            _save_json(SEEN_FILE, SEEN)
        except Exception as e:
            log.warning("poll_payments error: %s", e)
        await asyncio.sleep(max(4.0, POLL_INTERVAL))

# -------------------- BUY DETECTION --------------------
def parse_buy_from_tx(tx: Dict[str, Any], token_mint: str) -> Optional[Dict[str, Any]]:
    # We treat as "buy" when swap.nativeInput > 0 and tokenOutputs include token_mint.
    ev = (tx.get("events") or {}).get("swap") or {}
    token_outputs = ev.get("tokenOutputs") or []
    native_in = (ev.get("nativeInput") or {}).get("amount")
    sol_in = _amount_to_sol(native_in) if native_in is not None else 0.0

    out_amt = None
    out_dec = None
    out_user = None
    for t in token_outputs:
        if (t.get("mint") or "") == token_mint:
            raw = (t.get("rawTokenAmount") or {})
            try:
                out_amt = float(raw.get("tokenAmount"))
            except Exception:
                # fallback: sometimes tokenAmount exists
                try:
                    out_amt = float(t.get("tokenAmount"))
                except Exception:
                    out_amt = None
            try:
                out_dec = int(raw.get("decimals"))
            except Exception:
                out_dec = None
            out_user = t.get("userAccount") or t.get("toUserAccount")
            break

    if out_amt is None:
        # fallback: scan tokenTransfers
        for tt in tx.get("tokenTransfers") or []:
            if (tt.get("mint") or "") == token_mint:
                try:
                    out_amt = float(tt.get("tokenAmount"))
                except Exception:
                    out_amt = None
                out_user = tt.get("toUserAccount") or tt.get("userAccount")
                break

    if sol_in <= 0 or out_amt is None:
        return None

    return {
        "signature": tx.get("signature"),
        "timestamp": tx.get("timestamp") or int(time.time()),
        "buyer": out_user or tx.get("feePayer") or "",
        "sol_in": sol_in,
        "token_out": out_amt,
        "token_decimals": out_dec,
        "source": tx.get("source") or "",
        "type": tx.get("type") or "",
    }

def bubbles(sol_amt: float) -> str:
    # 1 bubble per 0.1 SOL capped 22
    n = max(1, min(22, int(math.ceil(sol_amt / 0.1))))
    return "🟢" * n

def fmt_num(x: float, decimals: int = 2) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):,.{decimals}f}"
    except Exception:
        return str(x)

def fmt_token_amt(raw_amt: float, decimals: Optional[int]) -> float:
    if raw_amt is None:
        return 0.0
    if decimals is None:
        return float(raw_amt)
    try:
        return float(raw_amt) / (10 ** int(decimals))
    except Exception:
        return float(raw_amt)

def pick_ad_for_post(token_mint: str) -> Tuple[str, str]:
    global _last_ad_rotation_ts, _ad_rotation_idx
    now = time.time()
    # If token has active paid ad booking, show that token's ad list (owner can add via /adsettoken)
    _clean_expired()
    paid = BOOKINGS.get("ads", {}).get(token_mint)
    ad_list = []
    if paid:
        ad_list = (ADS.get(token_mint) or [])
    if not ad_list:
        # fallback to global ads list stored under "*"
        ad_list = (ADS.get("*") or [])

    if not ad_list:
        return DEFAULT_AD_TEXT, DEFAULT_AD_LINK

    if (now - _last_ad_rotation_ts) > AD_ROTATE_SEC:
        _ad_rotation_idx = (_ad_rotation_idx + 1) % len(ad_list)
        _last_ad_rotation_ts = now
    ad = ad_list[_ad_rotation_idx % len(ad_list)]
    return (ad.get("text") or DEFAULT_AD_TEXT, ad.get("link") or DEFAULT_AD_LINK)

def build_buy_message(token: Dict[str, Any], buy: Dict[str, Any]) -> str:
    sym = token.get("symbol") or "TOKEN"
    name = token.get("name") or sym
    mint = token.get("mint") or ""
    tg = token.get("tg") or DEFAULT_TOKEN_TG
    chart = token.get("chart") or ""
    md = get_market_data(mint)
    if not chart:
        chart = md.get("url") or f"https://dexscreener.com/solana/{mint}"

    buyer = buy.get("buyer") or ""
    sol_in = float(buy.get("sol_in") or 0.0)
    tok_out = fmt_token_amt(float(buy.get("token_out") or 0.0), buy.get("token_decimals") or token.get("decimals"))

    price_usd = md.get("priceUsd")
    fdv = md.get("fdv")
    liq = md.get("liquidityUsd")
    mcap_line = f"💰 MCap/FDV: ${fmt_num(float(fdv),0)}" if fdv else "💰 MCap/FDV: -"
    liq_line = f"💧 Liquidity: ${fmt_num(float(liq),0)}" if liq else "💧 Liquidity: -"
    price_line = f"💵 Price: ${fmt_num(float(price_usd), 8)}" if price_usd else "💵 Price: -"

    ad_text, ad_link = pick_ad_for_post(mint)

    return (
        f"| {html.escape(sym)} Buy!\n\n"
        f"{bubbles(sol_in)}\n\n"
        f"💎 <b>{fmt_num(sol_in, 4)} SOL</b>\n"
        f"🪙 <b>{fmt_num(tok_out, 4)} {html.escape(sym)}</b>\n\n"
        f"👤 <a href=\"{solscan_addr(buyer)}\">{html.escape(_short(buyer))}</a> | "
        f"<a href=\"{solscan_tx(buy.get('signature') or '')}\">Txn</a>\n"
        f"{liq_line}\n"
        f"{mcap_line}\n"
        f"{price_line}\n\n"
        f"🔗 <a href=\"{chart}\">Chart</a> | <a href=\"{tg}\">Telegram</a> | <a href=\"{TRENDING_URL}\">Trending</a>\n\n"
        f"— <a href=\"{ad_link}\">{html.escape(ad_text)}</a>"
    )

# -------------------- LEADERBOARD --------------------
def _format_time_left(expires_at: int) -> str:
    sec = max(0, int(expires_at) - _now())
    h = sec // 3600
    m = (sec % 3600) // 60
    if h >= 24:
        d = h // 24
        h = h % 24
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"

def build_leaderboard_text() -> str:
    _clean_expired()
    lines: List[str] = []
    lines.append(f"🏆 <b>{html.escape(LEADERBOARD_HEADER_HANDLE)} Leaderboard</b>")
    lines.append("")
    # Trending (paid)
    t = BOOKINGS.get("trending", {}) or {}
    if t:
        lines.append("🔥 <b>Trending (Booked)</b>")
        items = sorted(t.items(), key=lambda kv: kv[1].get("expires_at", 0), reverse=True)
        for i,(mint, rec) in enumerate(items[:10], start=1):
            tok = TOKENS.get(mint, {"symbol":"TOKEN","name":"Token"})
            sym = tok.get("symbol") or "TOKEN"
            md = get_market_data(mint)
            vol6 = md.get("volumeH6")
            vol_txt = f"${fmt_num(float(vol6),0)}" if vol6 else "-"
            lines.append(f"{i}. <b>{html.escape(sym)}</b> | 6h Vol: {vol_txt} | ⏳ {_format_time_left(int(rec.get('expires_at',0)))}")
        lines.append("")
    # Organic top by DexScreener 6h volume for all tracked tokens
    if TOKENS:
        vols: List[Tuple[float,str]] = []
        for mint, tok in TOKENS.items():
            md = get_market_data(mint)
            v = md.get("volumeH6") or 0
            try:
                vols.append((float(v), mint))
            except Exception:
                continue
        vols.sort(reverse=True, key=lambda x: x[0])
        lines.append("🌿 <b>Organic (Top 6h Volume)</b>")
        for i,(v,mint) in enumerate(vols[:LEADERBOARD_ORGANIC_TOPN], start=1):
            tok = TOKENS.get(mint, {"symbol":"TOKEN"})
            sym = tok.get("symbol") or "TOKEN"
            lines.append(f"{i}. <b>{html.escape(sym)}</b> | 6h Vol: ${fmt_num(v,0)}")
        lines.append("")
    lines.append(f"📌 Book: /trending  •  Ads: /ads")
    return "\n".join(lines).strip()

async def ensure_leaderboard_message(app: Application) -> Optional[Tuple[int,int]]:
    if not TRENDING_POST_CHAT_ID:
        return None
    try:
        chat_id = int(TRENDING_POST_CHAT_ID)
    except Exception:
        return None
    if LEADERBOARD_MSG.get("chat_id") == chat_id and LEADERBOARD_MSG.get("message_id"):
        return chat_id, int(LEADERBOARD_MSG["message_id"])
    # create one
    msg = await app.bot.send_message(chat_id=chat_id, text=build_leaderboard_text(), parse_mode="HTML", disable_web_page_preview=True)
    LEADERBOARD_MSG.update({"chat_id": chat_id, "message_id": msg.message_id})
    _save_json(LEADERBOARD_MSG_FILE, LEADERBOARD_MSG)
    return chat_id, msg.message_id

async def leaderboard_loop(app: Application) -> None:
    if not LEADERBOARD_ON:
        return
    while True:
        try:
            if not TRENDING_POST_CHAT_ID:
                await asyncio.sleep(LEADERBOARD_INTERVAL)
                continue
            ref = await ensure_leaderboard_message(app)
            if not ref:
                await asyncio.sleep(LEADERBOARD_INTERVAL)
                continue
            chat_id, mid = ref
            await app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=mid,
                text=build_leaderboard_text(),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.warning("leaderboard_loop error: %s", e)
        await asyncio.sleep(LEADERBOARD_INTERVAL)

# -------------------- CONFIG LOAD --------------------
def load_all() -> None:
    global TOKENS, GROUPS, SEEN, ADS, BOOKINGS, INVOICES, LEADERBOARD_MSG
    TOKENS = {t["mint"]: t for t in _load_json(TOKENS_FILE, []) if isinstance(t, dict) and t.get("mint")}
    GROUPS = _load_json(GROUPS_FILE, {})
    SEEN = _load_json(SEEN_FILE, {})
    ADS = _load_json(ADS_FILE, {})
    BOOKINGS = _load_json(BOOKINGS_FILE, {"trending": {}, "ads": {}})
    INVOICES = _load_json(INVOICES_FILE, {})
    LEADERBOARD_MSG = _load_json(LEADERBOARD_MSG_FILE, {})
    if not isinstance(BOOKINGS, dict):
        BOOKINGS = {"trending": {}, "ads": {}}

def save_tokens() -> None:
    _save_json(TOKENS_FILE, list(TOKENS.values()))

def save_ads() -> None:
    _save_json(ADS_FILE, ADS)

def save_groups() -> None:
    _save_json(GROUPS_FILE, GROUPS)

# -------------------- TELEGRAM COMMANDS --------------------
def start_text() -> str:
    return (
        "🤖 SpySOL BuyBot\n\n"
        "Commands:\n"
        "• /tokens — list tracked tokens\n"
        "• /trending — book trending (paid)\n"
        "• /ads — book ads under buy posts\n\n"
        "Owner:\n"
        "• /addtoken <mint> <SYMBOL> <Name...>\n"
        "• /deltoken <mint>\n"
        "• /adset * <text> | <link>\n"
        "• /adsettoken <mint> <text> | <link>\n"
        "• /adclear <mint|*>\n"
    )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Trending Channel", url=TRENDING_URL), InlineKeyboardButton("Listing", url=LISTING_URL)],
    ])
    await update.effective_message.reply_text(start_text(), reply_markup=kb, disable_web_page_preview=True)

async def cmd_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not TOKENS:
        await update.effective_message.reply_text("No tokens configured yet.")
        return
    lines = ["📌 Tracked tokens:"]
    for t in TOKENS.values():
        lines.append(f"• {t.get('symbol','TOKEN')} — {t.get('mint')}")
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_addtoken(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /addtoken <mint> <SYMBOL> <Name...>\nOptional: add watch address later with /setwatch <mint> <address>")
        return
    mint = context.args[0].strip()
    symbol = context.args[1].strip()
    name = " ".join(context.args[2:]).strip() or symbol
    TOKENS[mint] = {
        "mint": mint,
        "symbol": symbol,
        "name": name,
        "decimals": None,
        "tg": DEFAULT_TOKEN_TG,
        "chart": f"https://dexscreener.com/solana/{mint}",
        "watch_address": "",   # REQUIRED to detect buys (pool/bonding curve)
        "kind": "solana",
    }
    save_tokens()
    await update.effective_message.reply_text(f"✅ Added {symbol}.\nNow set watch address:\n/setwatch {mint} <pool_or_bonding_curve_address>")

async def cmd_setwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /setwatch <mint> <pool_or_bonding_curve_address>")
        return
    mint = context.args[0].strip()
    addr = context.args[1].strip()
    tok = TOKENS.get(mint)
    if not tok:
        await update.effective_message.reply_text("Unknown mint.")
        return
    tok["watch_address"] = addr
    save_tokens()
    await update.effective_message.reply_text("✅ Watch address set.")

async def cmd_deltoken(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /deltoken <mint>")
        return
    mint = context.args[0].strip()
    TOKENS.pop(mint, None)
    # remove bookings/ads too
    for kind in ("trending","ads"):
        (BOOKINGS.get(kind, {}) or {}).pop(mint, None)
    ADS.pop(mint, None)
    save_tokens()
    save_ads()
    _save_json(BOOKINGS_FILE, BOOKINGS)
    await update.effective_message.reply_text("✅ Removed.")

async def cmd_adset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    txt = update.effective_message.text or ""
    m = re.match(r"^/adset\s+(\S+)\s+(.+)$", txt, flags=re.I | re.S)
    if not m:
        await update.effective_message.reply_text('Usage: /adset <mint|*> <text> | <link>')
        return
    mint = m.group(1).strip()
    rest = m.group(2).strip()
    if "|" not in rest:
        await update.effective_message.reply_text('Usage: /adset <mint|*> <text> | <link>')
        return
    text_part, link_part = [x.strip() for x in rest.split("|",1)]
    ADS.setdefault(mint, [])
    ADS[mint].append({"text": text_part, "link": link_part})
    save_ads()
    await update.effective_message.reply_text("✅ Ad added.")

async def cmd_adsettoken(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # alias to /adset
    await cmd_adset(update, context)

async def cmd_adclear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /adclear <mint|*>")
        return
    mint = context.args[0].strip()
    ADS.pop(mint, None)
    save_ads()
    await update.effective_message.reply_text("✅ Cleared.")

def _pricing_text(kind: str) -> str:
    mp = TRENDING_PRICE_MAP if kind=="trending" else ADS_PRICE_MAP
    rows = [f"{k} = {v} SOL" for k,v in mp.items()]
    return "\n".join(rows) if rows else "Not set."

async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PAY_WALLET:
        await update.effective_message.reply_text("Payments not configured. Set PAY_WALLET in env.")
        return
    if not TOKENS:
        await update.effective_message.reply_text("No tokens configured yet.")
        return
    # if user provides mint+duration -> make invoice
    if len(context.args) >= 2:
        mint = context.args[0].strip()
        dur = context.args[1].strip().lower()
        if mint not in TOKENS:
            await update.effective_message.reply_text("Unknown mint.")
            return
        price = TRENDING_PRICE_MAP.get(dur)
        if price is None:
            await update.effective_message.reply_text("Invalid duration.\n\nAvailable:\n" + _pricing_text("trending"))
            return
        inv = _mk_invoice("trending", mint, dur, float(price), update.effective_chat.id, update.effective_user.id)
        await update.effective_message.reply_text(
            "🔥 <b>Trending booking</b>\n\n"
            f"Token: <code>{mint}</code>\n"
            f"Duration: <b>{dur}</b>\n"
            f"Price: <b>{price} SOL</b>\n\n"
            f"Send <b>{price} SOL</b> to:\n<code>{PAY_WALLET}</code>\n\n"
            f"IMPORTANT: Add this MEMO / NOTE in the transfer:\n<code>{inv['id']}</code>\n\n"
            "If your wallet can't add memo, after paying use:\n"
            f"/confirm {inv['id']} <tx_signature>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    await update.effective_message.reply_text(
        "🔥 <b>Book Trending</b>\n\n"
        "Use:\n"
        "<code>/trending &lt;mint&gt; &lt;duration&gt;</code>\n\n"
        "Pricing:\n" + html.escape(_pricing_text("trending")),
        parse_mode="HTML"
    )

async def cmd_ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PAY_WALLET:
        await update.effective_message.reply_text("Payments not configured. Set PAY_WALLET in env.")
        return
    if not TOKENS:
        await update.effective_message.reply_text("No tokens configured yet.")
        return
    if len(context.args) >= 2:
        mint = context.args[0].strip()
        dur = context.args[1].strip().lower()
        if mint not in TOKENS:
            await update.effective_message.reply_text("Unknown mint.")
            return
        price = ADS_PRICE_MAP.get(dur)
        if price is None:
            await update.effective_message.reply_text("Invalid duration.\n\nAvailable:\n" + _pricing_text("ads"))
            return
        inv = _mk_invoice("ads", mint, dur, float(price), update.effective_chat.id, update.effective_user.id)
        await update.effective_message.reply_text(
            "📣 <b>Ads booking</b>\n\n"
            f"Token: <code>{mint}</code>\n"
            f"Duration: <b>{dur}</b>\n"
            f"Price: <b>{price} SOL</b>\n\n"
            f"Send <b>{price} SOL</b> to:\n<code>{PAY_WALLET}</code>\n\n"
            f"MEMO / NOTE:\n<code>{inv['id']}</code>\n\n"
            "If your wallet can't add memo, after paying use:\n"
            f"/confirm {inv['id']} <tx_signature>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    await update.effective_message.reply_text(
        "📣 <b>Book Ads</b>\n\n"
        "Use:\n"
        "<code>/ads &lt;mint&gt; &lt;duration&gt;</code>\n\n"
        "Pricing:\n" + html.escape(_pricing_text("ads")),
        parse_mode="HTML"
    )

async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /confirm <invoice_id> <tx_signature>")
        return
    inv_id = context.args[0].strip()
    sig = context.args[1].strip()
    inv = INVOICES.get(inv_id)
    if not inv or inv.get("status") != "pending":
        await update.effective_message.reply_text("Invoice not found / already processed.")
        return

    # verify tx hits PAY_WALLET and has enough SOL
    try:
        # easiest: fetch txs for pay wallet and find signature
        txs = helius_get_transactions_by_address(PAY_WALLET, limit=50)
        tx = next((t for t in txs if t.get("signature") == sig), None)
        if not tx:
            await update.effective_message.reply_text("Tx not found yet. Try again in 30s.")
            return
        total_in = 0.0
        for nt in tx.get("nativeTransfers") or []:
            if nt.get("toUserAccount") == PAY_WALLET:
                total_in += _amount_to_sol(nt.get("amount"))
        if total_in + 1e-9 < float(inv.get("price_sol") or 0):
            await update.effective_message.reply_text("Payment amount is less than required.")
            return
        payer = tx.get("feePayer") or ""
        _activate_booking(inv, sig, payer)
        await update.effective_message.reply_text("✅ Confirmed and activated.")
    except Exception as e:
        await update.effective_message.reply_text(f"Error verifying tx: {e}")

# -------------------- GROUP ACTIVITY --------------------
async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # store groups where bot is admin
    chat = update.effective_chat
    if not chat:
        return
    if chat.type not in ("group","supergroup"):
        return
    member = update.my_chat_member
    if not member:
        return
    status = member.new_chat_member.status
    if status in ("member","administrator"):
        GROUPS[str(chat.id)] = {"enabled": True}
        save_groups()

# -------------------- BUY POLLER --------------------
async def buy_poller(app: Application) -> None:
    if not HELIUS_API_KEY:
        log.warning("HELIUS_API_KEY not set; buy detection disabled.")
        return
    while True:
        try:
            _clean_expired()
            for mint, tok in list(TOKENS.items()):
                watch = (tok.get("watch_address") or "").strip()
                if not watch:
                    continue
                try:
                    txs = helius_get_transactions_by_address(watch, limit=20)
                except Exception:
                    continue
                # newest first -> process old to new
                for tx in reversed(txs):
                    sig = tx.get("signature") or ""
                    if not sig or sig in SEEN:
                        continue
                    SEEN[sig] = time.time()
                    buy = parse_buy_from_tx(tx, mint)
                    if not buy:
                        continue
                    msg = build_buy_message(tok, buy)
                    await broadcast_buy(app, msg)
            _save_json(SEEN_FILE, SEEN)
        except Exception as e:
            log.warning("buy_poller error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)

async def broadcast_buy(app: Application, text: str) -> None:
    # send to all enabled groups
    for chat_id, cfg in (GROUPS or {}).items():
        try:
            if not (cfg or {}).get("enabled", True):
                continue
            await app.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            continue
    # mirror into trending channel
    if MIRROR_TO_TRENDING and TRENDING_POST_CHAT_ID:
        try:
            await app.bot.send_message(chat_id=int(TRENDING_POST_CHAT_ID), text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass

# -------------------- HEALTHCHECK --------------------
flask_app = Flask(__name__)

@flask_app.get("/")
def health():
    return "ok", 200

async def _run_flask() -> None:
    port = int(os.getenv("PORT", "8080"))
    flask_app.run(host="0.0.0.0", port=port)

# -------------------- APP INIT --------------------
async def post_init(app: Application) -> None:
    load_all()
    # background tasks
    app.create_task(buy_poller(app))
    app.create_task(poll_payments(app))
    app.create_task(leaderboard_loop(app))
    # run flask in executor-like task
    app.create_task(_run_flask())

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is required.")
    load_all()
    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("tokens", cmd_tokens))
    application.add_handler(CommandHandler("addtoken", cmd_addtoken))
    application.add_handler(CommandHandler("setwatch", cmd_setwatch))
    application.add_handler(CommandHandler("deltoken", cmd_deltoken))
    application.add_handler(CommandHandler("adset", cmd_adset))
    application.add_handler(CommandHandler("adsettoken", cmd_adsettoken))
    application.add_handler(CommandHandler("adclear", cmd_adclear))
    application.add_handler(CommandHandler("trending", cmd_trending))
    application.add_handler(CommandHandler("ads", cmd_ads))
    application.add_handler(CommandHandler("confirm", cmd_confirm))
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
