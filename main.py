
import os, json, time, asyncio, logging, re, html, math, secrets
from typing import Any, Dict, Optional, List, Tuple
import requests



# --- Solscan (holders) helper ---
_SOLSCAN_HOLDERS_CACHE: Dict[str, Tuple[float, Optional[int]]] = {}
_SOLSCAN_TTL_SEC = int(os.getenv("SOLSCAN_TTL_SEC", "300"))

def solscan_get_holders(mint: str) -> Optional[int]:
    """Best-effort holders count via Solscan public endpoints (no key).
    Returns None if unavailable."""
    if not mint:
        return None
    now = time.time()
    cached = _SOLSCAN_HOLDERS_CACHE.get(mint)
    if cached and (now - cached[0]) < _SOLSCAN_TTL_SEC:
        return cached[1]

    headers = {"accept": "application/json"}

    # Try token meta first
    try:
        r = requests.get(
            "https://public-api.solscan.io/token/meta",
            params={"tokenAddress": mint},
            headers=headers,
            timeout=10,
        )
        if r.ok:
            j = r.json()
            for k in ("holder", "holders", "holderCount", "holdersCount"):
                v = j.get(k) if isinstance(j, dict) else None
                if isinstance(v, (int, float)) and v >= 0:
                    val = int(v)
                    _SOLSCAN_HOLDERS_CACHE[mint] = (now, val)
                    return val
    except Exception:
        pass

    # Fallback: holders list endpoint sometimes returns 'total'
    try:
        r = requests.get(
            "https://public-api.solscan.io/token/holders",
            params={"tokenAddress": mint, "limit": 1, "offset": 0},
            headers=headers,
            timeout=10,
        )
        if r.ok:
            j = r.json()
            if isinstance(j, dict):
                v = j.get("total") or j.get("totalCount") or j.get("count")
                if isinstance(v, (int, float)) and v >= 0:
                    val = int(v)
                    _SOLSCAN_HOLDERS_CACHE[mint] = (now, val)
                    return val
    except Exception:
        pass

    _SOLSCAN_HOLDERS_CACHE[mint] = (now, None)
    return None

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
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")

# Solana / Helius
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
# Helius Enhanced API base (NOT the RPC base). The enhanced endpoint we use is:
#   GET https://api.helius.xyz/v0/addresses/<address>/transactions?api-key=...
# Many people mistakenly use the RPC hostname (api-mainnet.helius-rpc.com),
# which will NEVER work with /v0/addresses endpoints.
HELIUS_BASE = os.getenv("HELIUS_BASE", "https://api.helius.xyz").strip().rstrip("/")
POLL_INTERVAL = max(2.0, float(os.getenv("POLL_INTERVAL", "2.0")))
BURST_WINDOW_SEC = int(os.getenv("BURST_WINDOW_SEC", "30"))

# Channels
TRENDING_URL = os.getenv("TRENDING_URL", "https://t.me/PumpToolsTrending").strip()
LISTING_URL = os.getenv("LISTING_URL", "https://t.me/PumpToolsListing").strip()

BOT_BRAND = os.getenv("BOT_BRAND", "PumpTools").strip()
BOT_USERNAME_ENV = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
ADS_LINK_OVERRIDE = os.getenv("ADS_LINK", "").strip()
DEFAULT_TOKEN_TG = os.getenv("DEFAULT_TOKEN_TG", "https://t.me/PumpToolsListing").strip()
LEADERBOARD_HEADER_HANDLE = os.getenv("LEADERBOARD_HEADER_HANDLE", "@PumpToolsTrending").strip()

TRENDING_POST_CHAT_ID = os.getenv("TRENDING_POST_CHAT_ID", "").strip()  # numeric id e.g. -100...
MIRROR_TO_TRENDING = str(os.getenv("MIRROR_TO_TRENDING", "1")).strip().lower() in ("1","true","yes","on")

# Owner + payments
OWNER_IDS = [int(x) for x in re.split(r"[ ,;]+", os.getenv("OWNER_IDS", "").strip()) if x.strip().isdigit()]
PAY_WALLET = os.getenv("PAY_WALLET", "").strip()  # Solana address to receive SOL
# Owner fallback (if OWNER_IDS env not set): allow claiming owner once and persist to file
DATA_DIR = os.getenv("DATA_DIR", ".")
OWNER_IDS_FILE = os.path.join(DATA_DIR or ".", "owner_ids.json")

def _load_owner_ids() -> List[int]:
    try:
        with open(OWNER_IDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = data.get("owner_ids") or []
        return [int(x) for x in ids if str(x).isdigit()]
    except Exception:
        return []

def _save_owner_ids(ids: List[int]) -> None:
    try:
        with open(OWNER_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump({"owner_ids": ids}, f)
    except Exception:
        pass

if not OWNER_IDS:
    OWNER_IDS = _load_owner_ids()


# Pricing (SOL)
TRENDING_PRICES = os.getenv("TRENDING_PRICES", "1h=0.2,6h=0.8,24h=2.5").strip()
ADS_PRICES = os.getenv("ADS_PRICES", "6h=1,12h=1.5,24h=3").strip()


# Package pricing for interactive /trending & /ads flows (defaults match your screenshots)
TOP3_PRICES = os.getenv("TOP3_PRICES", "2h=0.14,3h=0.73,6h=1.47,12h=2.03,24h=2.92").strip()
TOP10_PRICES = os.getenv("TOP10_PRICES", "3h=0.46,6h=1.09,12h=1.37,24h=2.19").strip()
ADS_PACKAGES = os.getenv("ADS_PACKAGES", "6h=1,12h=1.5,24h=3").strip()


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
DEFAULT_AD_LINK = os.getenv("DEFAULT_AD_LINK", "").strip()
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
    for part in re.split(r"[;|,]+", s):
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
# Interactive package maps
TOP3_PRICE_MAP = _parse_price_map(TOP3_PRICES)
TOP10_PRICE_MAP = _parse_price_map(TOP10_PRICES)
ADS_PACKAGE_MAP = _parse_price_map(ADS_PACKAGES)

_ALPH = "ABCDEFGH23456789"

def _mk_ref(kind: str) -> str:
    # kind: trending|ads
    import secrets
    tail = "".join(secrets.choice(_ALPH) for _ in range(5))
    if kind == "trending":
        return f"PT-TRND-{tail}"
    return f"PT-ADS-{tail}"


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


def _mk_invoice_ref(kind: str, mint: str, duration_key: str, price_sol: float, chat_id: int, user_id: int) -> Dict[str, Any]:
    # Create invoice with a human reference id (PT-TRND-XXXXX / PT-ADS-XXXXX).
    ref = _mk_ref(kind)
    inv = {
        "id": ref,
        "kind": kind,
        "mint": mint,
        "duration_key": duration_key,
        "price_sol": price_sol,
        "pay_wallet": PAY_WALLET,
        "created_at": _now(),
        "status": "pending",
        "chat_id": chat_id,
        "user_id": user_id,
    }
    INVOICES[ref] = inv
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
    if out_amt is None:
        return None

    # accept buys paid with SOL / WSOL / USDC etc.
    if spent_amount is None or spent_amount <= 0:
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


def diamonds(sol_amt: float) -> str:
    # 1 diamond per 0.1 SOL capped 12 (group style)
    n = max(1, min(12, int(math.ceil(sol_amt / 0.1))))
    return "💎" * n

def fmt_k(x: float) -> str:
    try:
        x = float(x)
    except Exception:
        return "-"
    if x >= 1_000_000:
        return f"{x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"{x/1_000:.2f}K"
    return f"{x:.0f}"

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

def _bot_username(app: Optional[Application]=None) -> str:
    # Best-effort bot username for deep links (t.me/<bot>?start=...)
    if BOT_USERNAME_ENV:
        return BOT_USERNAME_ENV
    try:
        if app and getattr(app, "bot", None):
            return (app.bot.username or "").lstrip("@")
    except Exception:
        pass
    return ""

def _ads_deeplink(mint: str, app: Optional[Application]=None) -> str:
    if ADS_LINK_OVERRIDE:
        return ADS_LINK_OVERRIDE
    uname = _bot_username(app)
    if uname:
        return f"https://t.me/{uname}?start=ads_{mint}"
    # fallback: channel listing link
    return LISTING_URL

def _buy_link(mint: str) -> str:
    # Pump.fun is the common buy page; safe fallback if not a pump token
    return f"https://pump.fun/coin/{mint}"

def build_buy_message_group(token: Dict[str, Any], buy: Dict[str, Any], app: Optional[Application]=None) -> str:
    sym = token.get("symbol") or "TOKEN"
    mint = token.get("mint") or ""
    tg = token.get("telegram") or token.get("tg") or DEFAULT_TOKEN_TG
    md = get_market_data(mint)
    chart = token.get("chart") or md.get("url") or f"https://dexscreener.com/solana/{mint}"

    buyer = buy.get("buyer") or ""
    sol_in = float(buy.get("sol_in") or 0.0)
    tok_out = fmt_token_amt(float(buy.get("token_out") or 0.0), buy.get("token_decimals") or token.get("decimals"))

    price_usd = md.get("priceUsd")
    fdv = md.get("fdv")
    liq = md.get("liquidityUsd")
    holders = buy.get("holders")  # optional (not always available)
    if not holders:
        holders = solscan_get_holders(mint)

    price_line = f"Price: ${fmt_num(float(price_usd), 8)}" if price_usd else "Price: -"
    liq_line = f"Liquidity: ${fmt_num(float(liq),0)}" if liq else "Liquidity: -"
    mcap_line = f"MCap: ${fmt_num(float(fdv),0)}" if fdv else "MCap: -"
    holders_line = f"Holders: {fmt_num(float(holders),0)}" if holders else "Holders: -"

    ad_text, ad_link = pick_ad_for_post(mint)
    if not ad_link:
        ad_link = _ads_deeplink(mint, app)

    # Group style (like your screenshot "3) Group buy style")
    return (
        f"<b>{html.escape(sym)} Buy!</b>\n"
        f"{diamonds(sol_in)}\n\n"
        f"Spent:  <b>{fmt_num(sol_in, 4)} SOL</b>\n"
        f"Got:    <b>{fmt_num(tok_out, 4)} {html.escape(sym)}</b>\n\n"
        f"<a href=\"{solscan_addr(buyer)}\">{html.escape(_short(buyer))}</a> | "
        f"<a href=\"{solscan_tx(buy.get('signature') or '')}\">Txn</a>\n\n"
        f"{price_line}\n"
        f"{liq_line}\n"
        f"{mcap_line}\n"
        f"{holders_line}\n\n"
        f"<a href=\"{solscan_tx(buy.get('signature') or '')}\">TX</a> | "
        f"<a href=\"{_buy_link(mint)}\">GT</a> | "
        f"<a href=\"{chart}\">DexS</a> | "
        f"<a href=\"{tg}\">Telegram</a> | "
        f"<a href=\"{TRENDING_URL}\">Trending</a>\n\n"
        f"ad: <a href=\"{ad_link}\">{html.escape(ad_text)}</a>"
    )

def build_buy_message_channel(token: Dict[str, Any], buy: Dict[str, Any], app: Optional[Application]=None) -> str:
    sym = token.get("symbol") or "TOKEN"
    mint = token.get("mint") or ""
    tg = token.get("telegram") or token.get("tg") or DEFAULT_TOKEN_TG
    md = get_market_data(mint)
    chart = token.get("chart") or md.get("url") or f"https://dexscreener.com/solana/{mint}"

    buyer = buy.get("buyer") or ""
    sol_in = float(buy.get("sol_in") or 0.0)
    tok_out = fmt_token_amt(float(buy.get("token_out") or 0.0), buy.get("token_decimals") or token.get("decimals"))
    usd_val = buy.get("usd_value")
    usd_txt = f"${fmt_num(float(usd_val),2)}" if usd_val else "$XXX.XX"

    price_usd = md.get("priceUsd")
    fdv = md.get("fdv")
    holders = buy.get("holders")

    price_line = f"💵 Price: ${fmt_num(float(price_usd), 8)}" if price_usd else "💵 Price: -"
    mcap_line = f"💰 MarketCap: ${fmt_num(float(fdv),0)}" if fdv else "💰 MarketCap: -"
    holders_line = f"↩ {fmt_k(float(holders))} Holders" if holders else "↩ Holders: -"

    ad_text, ad_link = pick_ad_for_post(mint)
    if not ad_link:
        ad_link = _ads_deeplink(mint, app)

    checks = "✅" * 22
    return (
        f"<b>${html.escape(sym)} Buy!</b>\n\n"
        f"{checks}\n\n"
        f"▽  <b>{fmt_num(sol_in, 4)} SOL</b> ({usd_txt})\n"
        f"↩  <b>{fmt_num(tok_out, 4)} ${html.escape(sym)}</b>\n"
        f"{holders_line}\n"
        f"👤 {html.escape(_short(buyer))}: +0.1% | <a href=\"{solscan_tx(buy.get('signature') or '')}\">Txn</a>\n"
        f"{price_line}\n"
        f"{mcap_line}\n\n"
        f"💎 <a href=\"{LISTING_URL}\">Listing</a> | "
        f"🐸 <a href=\"{_buy_link(mint)}\">Buy</a> | "
        f"📊 <a href=\"{chart}\">Chart</a>\n"
        f"ad: <a href=\"{ad_link}\">{html.escape(ad_text)}</a>"
    )

# Backward-compat for any old calls
def build_buy_message(token: Dict[str, Any], buy: Dict[str, Any]) -> str:
    return build_buy_message_group(token, buy, None)

# -------------------- LEADERBOARD# -------------------- LEADERBOARD --------------------
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
    """
    Style requested (code-block look):
    🟢 @PumpToolsTrending
    1□ - $SYMBOL | +0%
    ...
    divider after 3
    """
    _clean_expired()

    # Build a ranked list: first booked trending tokens, then fill with organic by 6h volume.
    ranked: List[str] = []
    booked = list((BOOKINGS.get("trending", {}) or {}).keys())
    for mint in booked:
        if mint in TOKENS:
            ranked.append(mint)

    if TOKENS:
        vols: List[Tuple[float, str]] = []
        for mint in TOKENS.keys():
            if mint in ranked:
                continue
            md = get_market_data(mint)
            v = md.get("volumeH6") or 0
            try:
                vols.append((float(v), mint))
            except Exception:
                pass
        vols.sort(reverse=True)
        ranked += [mint for _, mint in vols]

    ranked = ranked[:10]
    # Fill placeholders
    while len(ranked) < 10:
        ranked.append("")

    handle = LEADERBOARD_HEADER_HANDLE or "@PumpToolsTrending"
    lines = [f"🟢 {handle}"]
    for i in range(1, 11):
        mint = ranked[i-1]
        if mint and mint in TOKENS:
            sym = TOKENS[mint].get("symbol") or "SYMBOL"
            pct = "+0%"
            lines.append(f"{i}□  - ${sym}  | {pct}")
        else:
            lines.append(f"{i}□  - $SYMBOL  | +0%")
        if i == 3:
            lines.append("------------------------------------------------")

    # Use <pre> to keep alignment on Telegram
    return "<pre>\n" + "\n".join(lines) + "\n</pre>"

async def ensure_leaderboard_message(app: Application) -> Optional[Tuple[int,int]]:
    """Ensure a single leaderboard message exists in the trending channel and return (chat_id, message_id).
    Stores the reference in LEADERBOARD_MSG_FILE so we edit the same message (no spam)."""
    if not TRENDING_POST_CHAT_ID:
        return None
    chat_id = await get_trending_chat_id(app)
    if not chat_id:
        return None

    # If we already have a message id, use it
    try:
        mid = int((LEADERBOARD_MSG or {}).get("message_id") or 0)
        cid = int((LEADERBOARD_MSG or {}).get("chat_id") or 0)
        if mid and cid == chat_id:
            return (chat_id, mid)
    except Exception:
        pass

    # Create it
    try:
        msg = await app.bot.send_message(
            chat_id=chat_id,
            text=build_leaderboard_text(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        LEADERBOARD_MSG.clear()
        LEADERBOARD_MSG.update({"chat_id": chat_id, "message_id": msg.message_id})
        _save_json(LEADERBOARD_MSG_FILE, LEADERBOARD_MSG)
        return (chat_id, msg.message_id)
    except Exception as e:
        log.warning("Failed to create leaderboard message: %s", e)
        return None


async def maybe_init_leaderboard(app):
    """Create leaderboard message once (if TRENDING_POST_CHAT_ID is set) and store message_id."""
    try:
        chat_id = TRENDING_POST_CHAT_ID
        if not chat_id:
            return
        # If already saved, skip
        lb = _load_json(LEADERBOARD_MSG_FILE, default={})
        if lb.get("chat_id") == str(chat_id) and lb.get("message_id"):
            return
        # Create a placeholder leaderboard message
        text = render_leaderboard()
        msg = await app.bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        _save_json(LEADERBOARD_MSG_FILE, {"chat_id": str(chat_id), "message_id": msg.message_id})
    except Exception as e:
        log.warning("Leaderboard init failed: %s", e)

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



# -------------------- CHAT ID RESOLUTION --------------------
_TRENDING_CHAT_ID_CACHE: Optional[int] = None

async def resolve_chat_id(app: Application, chat_ref: Any) -> Optional[int]:
    """Resolve chat id from an int-like value or @username / t.me link."""
    if chat_ref is None:
        return None
    try:
        return int(chat_ref)
    except Exception:
        pass
    s = str(chat_ref).strip()
    if not s:
        return None
    if "t.me/" in s:
        s = s.split("t.me/")[-1].split("/")[0]
        s = "@" + s.lstrip("@")
    if s.startswith("@"):
        try:
            chat = await app.bot.get_chat(s)
            return int(chat.id)
        except Exception:
            return None
    return None

async def get_trending_chat_id(app: Application) -> Optional[int]:
    global _TRENDING_CHAT_ID_CACHE
    if _TRENDING_CHAT_ID_CACHE:
        return _TRENDING_CHAT_ID_CACHE
    cid = await resolve_chat_id(app, TRENDING_POST_CHAT_ID)
    _TRENDING_CHAT_ID_CACHE = cid
    return cid
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
def build_deeplink(param: str) -> str:
    # param is appended to start=... to open bot DM
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start={param}"
    return ""

def build_startgroup() -> str:
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?startgroup=1"
    return ""

def start_text() -> str:
    # Keep this as a single valid string (Railway will crash if a quote is left open).
    return (
        "🎃 Welcome to the Mythic PumpTools!\n\n"
        "Unlock Mythic-fast Buy Alerts, Premium Trending, and ads for your token.\n\n"
        "✅ How to activate:\n"
        " 1. Add  to your group\n"
        " 2. Make the bot Admin with Write permissions\n"
        " 3. If you don’t see the setup message after adding, type /continue in your group\n\n"
        "⚡️ Ready to boost your hype? Let’s go."
    )




async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Handle deep-link flows (DM)
    if context.args:
        arg0 = context.args[0].strip()
        if arg0.startswith("continue_"):
            gid = arg0.split("_", 1)[1]
            context.user_data["setup_group_id"] = gid
            context.user_data["awaiting_ca"] = True
            await update.effective_message.reply_text("➤ Send your SOL Contract Address:")
            return

        if arg0.startswith("ads_"):
            mint = arg0.split("_", 1)[1]
            await update.effective_message.reply_text(
                f"""📣 <b>Buy Alert Ads</b>

Token: <code>{html.escape(mint)}</code>

Book with:
<code>/ads &lt;mint&gt; &lt;duration&gt;</code>

Pricing:
{html.escape(_pricing_text("ads"))}""",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

        if arg0.startswith("trending_"):
            mint = arg0.split("_", 1)[1]
            await update.effective_message.reply_text(
                f"""🔥 <b>Trending Booking</b>

Token: <code>{html.escape(mint)}</code>

Book with:
<code>/trending &lt;mint&gt; &lt;duration&gt;</code>

Pricing:
{html.escape(_pricing_text("trending"))}""",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

        if arg0 == "ads":
            await update.effective_message.reply_text(
                f"""📣 <b>Buy Alert Ads</b>

Use:
<code>/ads &lt;mint&gt; &lt;duration&gt;</code>

Pricing:
{html.escape(_pricing_text("ads"))}""",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

        if arg0 == "trending":
            await update.effective_message.reply_text(
                f"""🔥 <b>Trending Booking</b>

Use:
<code>/trending &lt;mint&gt; &lt;duration&gt;</code>

Pricing:
{html.escape(_pricing_text("trending"))}""",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add me to your Group", url=build_startgroup())],
        [InlineKeyboardButton("Trending Channel", url=TRENDING_URL),
         InlineKeyboardButton("Listing", url=LISTING_URL)],
    ])
    await update.effective_message.reply_text(start_text(), reply_markup=kb, disable_web_page_preview=True)



async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's Telegram ID (useful for OWNER_IDS)."""
    u = update.effective_user
    await update.effective_message.reply_text(f"Your Telegram ID: {u.id}")


async def cmd_claim_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim ownership if OWNER_IDS is empty (one-time)."""
    u = update.effective_user
    if not u:
        return
    if OWNER_IDS:
        await update.effective_message.reply_text("Owner already configured.")
        return
    OWNER_IDS.append(u.id)
    _save_owner_ids(OWNER_IDS)
    await update.effective_message.reply_text(f"✅ Owner claimed: {u.id}\nNow only this ID can use owner commands.")

async def cmd_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    # If used inside group: send button that opens DM with deep link carrying group id
    if chat.type in ("group","supergroup"):
        link = build_deeplink(f"continue_{chat.id}")
        if not link:
            await update.effective_message.reply_text("Set BOT_USERNAME env var to enable the continue link.")
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Click here to continue!", url=link)]])
        await update.effective_message.reply_text("✅ Setup: Click below to continue in DM.", reply_markup=kb)
        return

    # If used in DM with an argument: /continue -100123...
    if context.args:
        gid = context.args[0]
        context.user_data["setup_group_id"] = gid
        context.user_data["awaiting_ca"] = True
        await update.effective_message.reply_text("➤ Send your SOL Contract Address:")
        return

    await update.effective_message.reply_text("Use /continue in your group (recommended), or /continue <group_id> in DM.")

# -------------------- SETUP / UI FLOW --------------------
def _group_token_settings(group_id: str, mint: str) -> Dict[str, Any]:
    g = GROUPS.setdefault(str(group_id), {"enabled": True})
    tmap = g.setdefault("tokens", {})
    s = tmap.get(mint)
    if not s:
        s = {
            "emoji": "🎩",
            "min_buy_usd": 0.0,
            "buy_steps": [15, 50, 100],
            "show_media": True,
            "show_chart": True,
            "show_notifications": True,
            "show_socials": True,
        }
        tmap[mint] = s
        save_groups()
    return s

def _settings_keyboard(mint: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Emoji (🎩)", callback_data=f"cfg|{mint}|emoji"),
         InlineKeyboardButton("Total Supply", callback_data=f"cfg|{mint}|supply")],
        [InlineKeyboardButton("Min. Buy ($15)", callback_data=f"cfg|{mint}|minbuy"),
         InlineKeyboardButton("Buy Steps ($15)", callback_data=f"cfg|{mint}|steps")],
        [InlineKeyboardButton("✅ Media", callback_data=f"cfg|{mint}|media"),
         InlineKeyboardButton("📊 Chart", callback_data=f"cfg|{mint}|chart")],
        [InlineKeyboardButton("🔔 Notifications", callback_data=f"cfg|{mint}|notif"),
         InlineKeyboardButton("🌐 Socials", callback_data=f"cfg|{mint}|socials")],
        [InlineKeyboardButton("🗑️ Delete Token", callback_data=f"cfg|{mint}|delete"),
         InlineKeyboardButton("⬅️ Back", callback_data="cfg|back|0")],
    ])


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callback presses."""
    q = update.callback_query
    if not q:
        return
    data = (q.data or "").strip()
    try:
        await q.answer()
    except Exception:
        pass

    # ----- Copy helpers for payments -----
    if data.startswith("copy|"):
        try:
            _, what, inv_id = data.split("|", 2)
        except Exception:
            return
        if what == "wallet":
            await q.message.reply_text(f"<pre>{html.escape(PAY_WALLET)}</pre>", parse_mode="HTML", disable_web_page_preview=True)
        elif what == "ref":
            await q.message.reply_text(f"<pre>{html.escape(inv_id)}</pre>", parse_mode="HTML", disable_web_page_preview=True)
        return

    # ----- Pair selection during setup -----
    if data.startswith("pair|"):
        _, arg = data.split("|", 1)
        if arg == "back":
            context.user_data["awaiting_ca"] = True
            context.user_data.pop("pending_mint", None)
            context.user_data.pop("pair_candidates", None)
            await q.message.reply_text("➤ Send your SOL Contract Address:")
            return

        try:
            idx = int(arg)
        except Exception:
            return

        pairs = context.user_data.get("pair_candidates") or []
        mint = context.user_data.get("pending_mint")
        if not mint or idx < 0 or idx >= len(pairs):
            await q.message.reply_text("Selection expired. Send the SOL Contract Address again.")
            context.user_data["awaiting_ca"] = True
            return

        p = pairs[idx]
        base = (p.get("baseToken") or {})
        sym = base.get("symbol") or "TOKEN"
        name = base.get("name") or sym
        pair_addr = p.get("pairAddress") or ""

        TOKENS[mint] = {
            **(TOKENS.get(mint) or {}),
            "mint": mint,
            "symbol": sym,
            "name": name,
            "pair": pair_addr,
            "chart": f"https://dexscreener.com/solana/{pair_addr}" if pair_addr else f"https://dexscreener.com/solana/{mint}",
            "watch_address": (pair_addr or (TOKENS.get(mint) or {}).get("watch_address", "")),
            "kind": "solana",
        }
        save_tokens()

        gid = context.user_data.get("setup_group_id")
        if gid:
            _group_token_settings(gid, mint)
            save_groups()

        context.user_data.pop("awaiting_ca", None)
        context.user_data.pop("pair_candidates", None)
        context.user_data.pop("pending_mint", None)

        await q.message.reply_text(
            "⚙️ Choose from the following options to customize your Buy Bot:",
            reply_markup=_settings_keyboard(mint),
        )
        return

    # ----- Settings callbacks -----
    if data.startswith("cfg|"):
        parts = data.split("|")
        if len(parts) >= 2 and parts[1] == "back":
            context.user_data["awaiting_ca"] = True
            await q.message.reply_text("➤ Send your SOL Contract Address:")
            return

        if len(parts) < 3:
            return
        mint = parts[1]
        action = parts[2]
        gid = context.user_data.get("setup_group_id")
        if gid and mint:
            _group_token_settings(gid, mint)

        if action == "emoji":
            context.user_data["awaiting_setting"] = {"kind": "emoji", "mint": mint, "group_id": gid}
            await q.message.reply_text("Send the emoji you want to use (example: 🎩)")
            return

        if action == "supply":
            context.user_data["awaiting_setting"] = {"kind": "supply", "mint": mint, "group_id": gid}
            await q.message.reply_text("Send total supply number (example: 1000000000)")
            return

        if action == "minbuy":
            context.user_data["awaiting_setting"] = {"kind": "minbuy", "mint": mint, "group_id": gid}
            await q.message.reply_text("Send minimum buy in USD (example: 15)")
            return

        if action == "steps":
            context.user_data["awaiting_setting"] = {"kind": "steps", "mint": mint, "group_id": gid}
            await q.message.reply_text("Send buy steps in USD, comma separated (example: 15,50,100)")
            return

        if action == "media":
            if gid:
                s = _group_token_settings(gid, mint)
                s["show_media"] = not bool(s.get("show_media", True))
                save_groups()
            context.user_data["awaiting_setting"] = {"kind": "mediafile", "mint": mint, "group_id": gid}
            await q.message.reply_text("Send a photo/video/GIF for media (or type 'remove' to clear).")
            return

        if action == "chart":
            if gid:
                s = _group_token_settings(gid, mint)
                s["show_chart"] = not bool(s.get("show_chart", True))
                save_groups()
            await q.message.reply_text("✅ Chart setting updated.", reply_markup=_settings_keyboard(mint))
            return

        if action == "notif":
            if gid:
                s = _group_token_settings(gid, mint)
                s["show_notifications"] = not bool(s.get("show_notifications", True))
                save_groups()
            await q.message.reply_text("✅ Notifications setting updated.", reply_markup=_settings_keyboard(mint))
            return

        if action == "socials":
            context.user_data["awaiting_setting"] = {"kind": "socials", "mint": mint, "group_id": gid}
            await q.message.reply_text("Send socials like: tg=https://t.me/yourchat website=https://site.com x=https://x.com/name", disable_web_page_preview=True)
            return

        if action == "delete":
            if gid:
                g = GROUPS.get(str(gid)) or {}
                tmap = g.get("tokens") or {}
                if mint in tmap:
                    tmap.pop(mint, None)
                    save_groups()
            await q.message.reply_text("🗑️ Token removed from this group.")
            return

        return

    # ----- Booking flows (Trending / Ads) -----
    if data == "trmain":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⬇️ Top 3 ⬇️", callback_data="trcat|top3"),
            InlineKeyboardButton("⬇️ Top 10 ⬇️", callback_data="trcat|top10"),
        ]])
        await q.message.edit_text("<pre>📈 PumpTools Trending - Book a Slot\n\nSelect category:</pre>", parse_mode="HTML", reply_markup=kb)
        return

    if data.startswith("trcat|"):
        _, cat = data.split("|", 1)
        if cat not in ("top3","top10"): return
        if cat == "top3":
            rows = [
                [InlineKeyboardButton("2 hours | 0.14 SOL", callback_data="trdur|top3|2h")],
                [InlineKeyboardButton("3 hours | 0.73 SOL", callback_data="trdur|top3|3h")],
                [InlineKeyboardButton("6 hours | 1.47 SOL", callback_data="trdur|top3|6h")],
                [InlineKeyboardButton("12 hours | 2.03 SOL", callback_data="trdur|top3|12h")],
                [InlineKeyboardButton("24 hours | 2.92 SOL", callback_data="trdur|top3|24h")],
            ]
        else:
            rows = [
                [InlineKeyboardButton("3 hours | 0.46 SOL", callback_data="trdur|top10|3h")],
                [InlineKeyboardButton("6 hours | 1.09 SOL", callback_data="trdur|top10|6h")],
                [InlineKeyboardButton("12 hours | 1.37 SOL", callback_data="trdur|top10|12h")],
                [InlineKeyboardButton("24 hours | 2.19 SOL", callback_data="trdur|top10|24h")],
            ]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="trmain"), InlineKeyboardButton("🏠 Main Menu", callback_data="mainmenu")])
        await q.message.edit_text("<pre>Top packages (prices already -30%)\n\nButtons:</pre>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("trdur|"):
        _, cat, dur = data.split("|", 2)
        dur = dur.lower()
        price = (TOP3_PRICE_MAP if cat=="top3" else TOP10_PRICE_MAP).get(dur)
        if price is None:
            await q.message.reply_text("Invalid package.")
            return
        context.user_data["booking_flow"] = {"kind":"trending","slot":cat,"duration":dur,"price":float(price)}
        context.user_data["awaiting_booking_mint"] = True
        await q.message.reply_text("Send token mint address:")
        return

    if data.startswith("adsdur|"):
        _, dur = data.split("|", 1)
        dur = dur.lower()
        price = ADS_PACKAGE_MAP.get(dur)
        if price is None:
            await q.message.reply_text("Invalid package.")
            return
        context.user_data["booking_flow"] = {"kind":"ads","duration":dur,"price":float(price)}
        context.user_data["awaiting_booking_mint"] = True
        await q.message.reply_text("Send token mint address:")
        return

    if data.startswith("paid|"):
        _, inv_id = data.split("|", 1)
        inv = INVOICES.get(inv_id)
        if not inv or inv.get("status") != "pending":
            await q.message.reply_text("Invoice not found / already processed.")
            return
        try:
            txs = helius_get_transactions_by_address(PAY_WALLET, limit=50)
            matched = None
            for tx in txs:
                memo = (_extract_memo_from_tx(tx) or "").strip()
                if memo == inv_id:
                    matched = tx
                    break
            if not matched:
                await q.message.reply_text("Not found yet. Try again in 30s. If your wallet can’t add memo, use /confirm <REF> <tx_signature>.")
                return
            total_in = 0.0
            for nt in matched.get("nativeTransfers") or []:
                if nt.get("toUserAccount") == PAY_WALLET:
                    total_in += _amount_to_sol(nt.get("amount"))
            if total_in + 1e-9 < float(inv.get("price_sol") or 0):
                await q.message.reply_text("Payment amount is less than required.")
                return
            payer = matched.get("feePayer") or ""
            _activate_booking(inv, matched.get("signature") or "", payer)
            if inv.get("kind") == "ads":
                ad = inv.get("ad") or {}
                if ad.get("text") and ad.get("link"): 
                    ADS[inv["mint"]] = {"text": ad["text"], "link": ad["link"], "media_type": ad.get("media_type"), "media_file_id": ad.get("media_file_id"), "added_by": "paid"}
                    save_ads()
            if LEADERBOARD_ON and TRENDING_POST_CHAT_ID:
                ref = await ensure_leaderboard_message(context.application)
                if ref:
                    chat_id, mid = ref
                    await context.application.bot.edit_message_text(chat_id=chat_id, message_id=mid, text=build_leaderboard_text(), parse_mode="HTML", disable_web_page_preview=True)
            sym = TOKENS.get(inv.get("mint"), {}).get("symbol", "TOKEN")
            await q.message.reply_text(f"✅ {inv.get('kind','').title()} activated for ${sym} ({inv.get('duration_key')}).")
        except Exception as e:
            await q.message.reply_text(f"Error verifying tx: {e}")
        return

    if data.startswith("cancel|"):
        _, inv_id = data.split("|", 1)
        inv = INVOICES.get(inv_id)
        if inv and inv.get("status") == "pending":
            inv["status"] = "expired"
            _save_json(INVOICES_FILE, INVOICES)
        await q.message.reply_text("❌ Canceled.")
        return

    if data == "mainmenu":
        await q.message.reply_text("Use /trending or /ads to book.")
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return

    text_in = msg.text or (msg.caption or "")
    if not text_in and not (msg.photo or msg.video or msg.animation):
        return

    # --- Interactive booking flow state machine ---
    if context.user_data.get("awaiting_booking_mint") and text_in:
        mint = text_in.strip()
        flow = context.user_data.get("booking_flow") or {}
        if mint not in TOKENS:
            await msg.reply_text("Unknown mint. Ask owner to add it first, or send a valid tracked mint.")
            return
        kind = flow.get("kind")
        dur = flow.get("duration")
        price = float(flow.get("price") or 0)
        if kind == "ads":
            flow["mint"] = mint
            context.user_data["booking_flow"] = flow
            context.user_data.pop("awaiting_booking_mint", None)
            context.user_data["awaiting_ad_content"] = True
            await msg.reply_text("Send ad text + link (format: text | https://link). You can also send an image/video with the caption in that format.", disable_web_page_preview=True)
            return
        inv = _mk_invoice_ref("trending", mint, dur, price, update.effective_chat.id, update.effective_user.id)
        sym = TOKENS.get(mint, {}).get("symbol") or "TOKEN"
        slot = flow.get("slot", "top10")
        summary_html = (
            "✅ <b>Order Summary</b>\n\n"
            f"Token: <b>${html.escape(sym)}</b>\n"
            f"Slot: <b>{'Top 3' if slot=='top3' else 'Top 10'}</b>\n"
            f"Duration: <b>{html.escape(dur)}</b>\n"
            f"Price: <b>{price} SOL</b>\n\n"
            "Pay to (Solana):\n"
            f"<pre>{html.escape(PAY_WALLET)}</pre>\n"
            "Reference:\n"
            f"<pre>{html.escape(inv['id'])}</pre>\n"
            "(Include this reference in the transfer note/memo)\n\n"
            "After payment tap: ✅ <b>I Paid</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I Paid", callback_data=f"paid|{inv['id']}"), InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{inv['id']}")],
            [InlineKeyboardButton("📋 Wallet", callback_data=f"copy|wallet|{inv['id']}"), InlineKeyboardButton("📋 Reference", callback_data=f"copy|ref|{inv['id']}")],
        ])
        context.user_data.pop("awaiting_booking_mint", None)
        await msg.reply_text(summary_html, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        return

    if context.user_data.get("awaiting_ad_content"):
        flow = context.user_data.get("booking_flow") or {}
        mint = flow.get("mint")
        if not mint:
            context.user_data.pop("awaiting_ad_content", None)
            await msg.reply_text("Session expired. Use /ads again.")
            return
        content = (text_in or '').strip()
        if '|' in content:
            ad_text, ad_link = [x.strip() for x in content.split('|',1)]
        else:
            ad_text, ad_link = content, ''
        if not ad_link.startswith('http'):
            await msg.reply_text("Please include a valid link. Format: your text | https://link", disable_web_page_preview=True)
            return
        media_type = None
        file_id = None
        if msg.photo:
            media_type = 'photo'
            file_id = msg.photo[-1].file_id
        elif msg.video:
            media_type = 'video'
            file_id = msg.video.file_id
        elif msg.animation:
            media_type = 'animation'
            file_id = msg.animation.file_id
        dur = flow.get('duration')
        price = float(flow.get('price') or 0)
        inv = _mk_invoice_ref('ads', mint, dur, price, update.effective_chat.id, update.effective_user.id)
        inv['ad'] = {'text': ad_text, 'link': ad_link, 'media_type': media_type, 'media_file_id': file_id}
        _save_json(INVOICES_FILE, INVOICES)
        context.user_data.pop('awaiting_ad_content', None)
        sym = TOKENS.get(mint, {}).get('symbol') or 'TOKEN'
        summary = (
            "✅ Order Summary\n\n"
            f"Token: ${sym}\n"
            f"Duration: {dur}\n"
            f"Price: {price} SOL\n\n"
            f"Pay to (Solana):\n{PAY_WALLET}\n\n"
            f"Reference: {inv['id']}\n"
            "(Include this reference in the transfer note/memo)\n\n"
            "After payment tap: ✅ I Paid"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ I Paid", callback_data=f"paid|{inv['id']}"), InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{inv['id']}")]])
        await msg.reply_text(summary_html, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        return
    txt = msg.text.strip()

    # Setting value input
    awaiting = context.user_data.get("awaiting_setting")
    if awaiting:
        kind = awaiting.get("kind")
        mint = awaiting.get("mint")
        gid = awaiting.get("group_id")
        if not mint:
            context.user_data.pop("awaiting_setting", None)
            return

        if kind == "emoji":
            _group_token_settings(gid, mint)["emoji"] = txt[:6]
            save_groups()
            context.user_data.pop("awaiting_setting", None)
            await msg.reply_text("✅ Emoji updated.", reply_markup=_settings_keyboard(mint))
            return

        if kind == "minbuy":
            try:
                val = float(txt.replace("$", "").strip())
            except Exception:
                await msg.reply_text("Send a number like 15")
                return
            _group_token_settings(gid, mint)["min_buy_usd"] = val
            save_groups()
            context.user_data.pop("awaiting_setting", None)
            await msg.reply_text("✅ Min buy updated.", reply_markup=_settings_keyboard(mint))
            return

        if kind == "steps":
            parts = [p.strip() for p in txt.replace("$", "").split(",") if p.strip()]
            try:
                vals = [float(p) for p in parts]
            except Exception:
                await msg.reply_text("Send values like: 15,50,100")
                return
            _group_token_settings(gid, mint)["buy_steps"] = vals
            save_groups()
            context.user_data.pop("awaiting_setting", None)
            await msg.reply_text("✅ Buy steps updated.", reply_markup=_settings_keyboard(mint))
            return

        if kind == "mediafile":
            if txt.lower() == "remove":
                t = TOKENS.get(mint) or {}
                t.pop("media_type", None)
                t.pop("media_file_id", None)
                TOKENS[mint] = t
                save_tokens()
                context.user_data.pop("awaiting_setting", None)
                await msg.reply_text("✅ Media removed.", reply_markup=_settings_keyboard(mint))
                return
            await msg.reply_text("Send a photo/video/GIF for media (or type 'remove' to clear).")
            return

        if kind == "socials":
            # format: tg=<url> website=<url> x=<url>
            t = TOKENS.get(mint) or {}
            for part in txt.split():
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.lower().strip()
                v = v.strip()
                if k in ("tg", "telegram"):
                    t["telegram"] = v
                elif k in ("x", "twitter"):
                    t["twitter"] = v
                elif k in ("web", "website"):
                    t["website"] = v
            TOKENS[mint] = t
            save_tokens()
            context.user_data.pop("awaiting_setting", None)
            await msg.reply_text("✅ Socials updated.", reply_markup=_settings_keyboard(mint), disable_web_page_preview=True)
            return

        if kind == "supply":
            try:
                val = float(txt.replace(",", "").strip())
            except Exception:
                await msg.reply_text("Send a number like 1000000000")
                return
            t = TOKENS.get(mint) or {}
            t["total_supply"] = val
            TOKENS[mint] = t
            save_tokens()
            context.user_data.pop("awaiting_setting", None)
            await msg.reply_text("✅ Total supply updated.", reply_markup=_settings_keyboard(mint))
            return

    # Setup flow: contract address input
    if context.user_data.get("awaiting_ca"):
        mint = txt
        # basic Solana mint check (base58-ish length)
        if len(mint) < 30 or len(mint) > 50:
            await msg.reply_text("That doesn't look like a Solana mint. Send the SOL Contract Address again.")
            return
        try:
            pairs = dexscreener_token_pairs(mint)
        except Exception:
            pairs = []
        if not pairs:
            await msg.reply_text("No pairs found for that mint. Try another contract address.")
            return

        # store candidates in user_data, show top 6 by volume
        def vol(p):
            try:
                return float((p.get("volume") or {}).get("h24") or 0)
            except Exception:
                return 0.0

        pairs = sorted(pairs, key=vol, reverse=True)[:6]
        context.user_data["pending_mint"] = mint
        context.user_data["pair_candidates"] = pairs
        btns = []
        for i, p in enumerate(pairs):
            base = (p.get("baseToken") or {})
            sym = base.get("symbol") or "TOKEN"
            quote = (p.get("quoteToken") or {}).get("symbol") or "SOL"
            dex = (p.get("dexId") or "").title()
            v = vol(p)
            vtxt = fmt_k(v)
            btns.append([InlineKeyboardButton(f"{sym} - {quote} - ({dex} V: {vtxt})", callback_data=f"pair|{i}")])
        btns.append([InlineKeyboardButton("Back", callback_data="pair|back")])
        await msg.reply_text("➤ Match found! Confirm your pair below:", reply_markup=InlineKeyboardMarkup(btns))
        return


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    awaiting = context.user_data.get("awaiting_setting")
    if not awaiting or awaiting.get("kind") != "mediafile":
        return

    mint = awaiting.get("mint")
    if not mint:
        return

    t = TOKENS.get(mint) or {}
    media_type = None
    file_id = None

    if msg.photo:
        media_type = "photo"
        file_id = msg.photo[-1].file_id
    elif msg.video:
        media_type = "video"
        file_id = msg.video.file_id
    elif msg.animation:
        media_type = "animation"
        file_id = msg.animation.file_id

    if not media_type or not file_id:
        await msg.reply_text("Please send a photo, video, or GIF.")
        return

    t["media_type"] = media_type
    t["media_file_id"] = file_id
    TOKENS[mint] = t
    save_tokens()
    context.user_data.pop("awaiting_setting", None)
    await msg.reply_text("✅ Media updated.", reply_markup=_settings_keyboard(mint))


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



async def cmd_force_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /force_trending <CA> <duration> (example: 6h)")
        return
    mint = context.args[0].strip(); dur = context.args[1].strip().lower()
    if mint not in TOKENS:
        await update.effective_message.reply_text("Unknown mint.")
        return
    seconds = duration_key_to_seconds(dur)
    BOOKINGS.setdefault('trending', {})
    BOOKINGS['trending'][mint] = {'mint': mint, 'started_at': _now(), 'expires_at': _now()+seconds, 'paid_by': 'owner', 'tx': 'owner', 'duration_key': dur, 'price_sol': 0}
    _save_json(BOOKINGS_FILE, BOOKINGS)
    if LEADERBOARD_ON and TRENDING_POST_CHAT_ID:
        ref = await ensure_leaderboard_message(context.application)
        if ref:
            chat_id, mid = ref
            await context.application.bot.edit_message_text(chat_id=chat_id, message_id=mid, text=build_leaderboard_text(), parse_mode='HTML', disable_web_page_preview=True)
    await update.effective_message.reply_text("✅ Trending added by owner.")


async def cmd_force_ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 3:
        await update.effective_message.reply_text("Usage: /force_ads <mint> <duration> <text | link>")
        return
    mint = context.args[0].strip(); dur = context.args[1].strip().lower(); rest = ' '.join(context.args[2:]).strip()
    if mint not in TOKENS:
        await update.effective_message.reply_text("Unknown mint.")
        return
    if '|' not in rest:
        await update.effective_message.reply_text("Format: text | https://link", disable_web_page_preview=True)
        return
    ad_text, ad_link = [x.strip() for x in rest.split('|',1)]
    if not ad_link.startswith('http'):
        await update.effective_message.reply_text("Invalid link.")
        return
    ADS[mint] = {'text': ad_text, 'link': ad_link, 'added_by': 'owner'}
    save_ads()
    seconds = duration_key_to_seconds(dur)
    BOOKINGS.setdefault('ads', {})
    BOOKINGS['ads'][mint] = {'mint': mint, 'started_at': _now(), 'expires_at': _now()+seconds, 'paid_by': 'owner', 'tx': 'owner', 'duration_key': dur, 'price_sol': 0}
    _save_json(BOOKINGS_FILE, BOOKINGS)
    await update.effective_message.reply_text("✅ Ads activated by owner.")


async def cmd_leaderboard_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    ref = await ensure_leaderboard_message(context.application)
    if not ref:
        await update.effective_message.reply_text("Failed. Check TRENDING_POST_CHAT_ID and bot admin permissions in channel.")
        return
    await update.effective_message.reply_text("✅ Leaderboard message created/linked.")


async def cmd_leaderboard_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    LEADERBOARD_MSG.clear(); _save_json(LEADERBOARD_MSG_FILE, LEADERBOARD_MSG)
    ref = await ensure_leaderboard_message(context.application)
    if ref:
        chat_id, mid = ref
        await context.application.bot.edit_message_text(chat_id=chat_id, message_id=mid, text=build_leaderboard_text(), parse_mode='HTML', disable_web_page_preview=True)
    await update.effective_message.reply_text("✅ Leaderboard reset.")
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
        inv = _mk_invoice_ref("trending", mint, dur, float(price), update.effective_chat.id, update.effective_user.id)
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
        "<pre>📈 PumpTools Trending - Book a Slot\n\nSelect category:</pre>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬇️ Top 3 ⬇️", callback_data="trcat|top3"),
            InlineKeyboardButton("⬇️ Top 10 ⬇️", callback_data="trcat|top10"),
        ]])
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
        inv = _mk_invoice_ref("ads", mint, dur, float(price), update.effective_chat.id, update.effective_user.id)
        sym = TOKENS.get(mint, {}).get("symbol") or "TOKEN"
        summary_html = (
            "📣 <b>Ads booking</b>\n\n"
            f"Token: <b>${html.escape(sym)}</b>\n"
            f"Duration: <b>{html.escape(dur)}</b>\n"
            f"Price: <b>{price} SOL</b>\n\n"
            "Pay to (Solana):\n"
            f"<pre>{html.escape(PAY_WALLET)}</pre>\n"
            "Reference:\n"
            f"<pre>{html.escape(inv['id'])}</pre>\n"
            "(Include this reference in the transfer note/memo)\n\n"
            "After payment tap: ✅ <b>I Paid</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I Paid", callback_data=f"paid|{inv['id']}"), InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{inv['id']}")],
            [InlineKeyboardButton("📋 Wallet", callback_data=f"copy|wallet|{inv['id']}"), InlineKeyboardButton("📋 Reference", callback_data=f"copy|ref|{inv['id']}")],
        ])
        await update.effective_message.reply_text(summary_html, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        return

    await update.effective_message.reply_text(
        "<pre>📣 PumpTools Ads (shown under buy alerts)\n\nChoose duration:</pre>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("6H | 1 SOL", callback_data="adsdur|6h"),
            InlineKeyboardButton("12H | 1.5 SOL", callback_data="adsdur|12h"),
            InlineKeyboardButton("24H | 3 SOL", callback_data="adsdur|24h"),
        ],[
            InlineKeyboardButton("⬅ Back", callback_data="mainmenu"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="mainmenu"),
        ]])
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
    # When the bot is added to a group, store group and post "continue" button like PumpTools Buy Bot.
    chat = update.effective_chat
    if not chat or chat.type not in ("group","supergroup"):
        return
    member = update.my_chat_member
    if not member:
        return
    status = member.new_chat_member.status
    if status in ("member","administrator"):
        GROUPS[str(chat.id)] = {"enabled": True}
        save_groups()

        link = build_deeplink(f"continue_{chat.id}")
        if link:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Click here to continue!", url=link)]])
            await context.bot.send_message(
                chat_id=chat.id,
                text="✅ Buy Bot added to the group successfully!",
                reply_markup=kb
            )
        else:
            await context.bot.send_message(
                chat_id=chat.id,
                text="✅ Buy Bot added to the group successfully! Type /continue to continue setup."
            )


# -------------------- BUY POLLER --------------------
async def buy_poller(app: Application) -> None:
    """
    Poll Helius Enhanced Transactions by watch address.
    IMPORTANT: prevent "old buy spam" by keeping a per-token cursor signature.
    """
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
                if not txs:
                    continue

                last_sig = (tok.get("last_sig") or "").strip()

                # First run for this token: set cursor to newest and DO NOT backfill.
                newest_sig = (txs[0].get("signature") or "").strip()
                if not last_sig and newest_sig:
                    tok["last_sig"] = newest_sig
                    TOKENS[mint] = tok
                    save_tokens()
                    continue

                # Collect new txs newer than last_sig
                new_txs = []
                for tx in txs:  # newest -> oldest
                    sig = (tx.get("signature") or "").strip()
                    if not sig:
                        continue
                    if sig == last_sig:
                        break
                    new_txs.append(tx)

                if not new_txs:
                    continue

                # Update cursor to newest we saw
                tok["last_sig"] = (new_txs[0].get("signature") or newest_sig)
                TOKENS[mint] = tok
                save_tokens()

                # Process old -> new
                for tx in reversed(new_txs):
                    sig = (tx.get("signature") or "").strip()
                    if not sig or sig in SEEN:
                        continue
                    SEEN[sig] = time.time()
                    buy = parse_buy_from_tx(tx, mint)
                    if not buy:
                        continue
                    await broadcast_buy(app, mint, buy)

            _save_json(SEEN_FILE, SEEN)
        except Exception as e:
            log.warning("buy_poller error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)

async def _send_buy_to_chat(app: Application, chat_id: int, mint: str, buy: Dict[str, Any], is_channel: bool) -> None:
    tok = TOKENS.get(mint) or {}
    # group_id for settings: chat_id string; for channel use "0"
    gid = "0" if is_channel else str(chat_id)
    s = _group_token_settings(gid, mint)
    show_media = bool(s.get("show_media", True))

    text = build_buy_message_channel(tok, buy, app) if is_channel else build_buy_message_group(tok, buy, app)

    media_type = tok.get("media_type")
    media_file = tok.get("media_file_id")

    if show_media and media_type and media_file:
        try:
            if media_type == "photo":
                await app.bot.send_photo(chat_id=chat_id, photo=media_file, caption=text, parse_mode="HTML")
                return
            if media_type == "video":
                await app.bot.send_video(chat_id=chat_id, video=media_file, caption=text, parse_mode="HTML")
                return
            if media_type == "animation":
                await app.bot.send_animation(chat_id=chat_id, animation=media_file, caption=text, parse_mode="HTML")
                return
        except Exception:
            # fallback to plain message
            pass

    await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)

async def broadcast_buy(app: Application, mint: str, buy: Dict[str, Any]) -> None:
    # send to all enabled groups
    for chat_id, cfg in (GROUPS or {}).items():
        try:
            if not (cfg or {}).get("enabled", True):
                continue
            await _send_buy_to_chat(app, int(chat_id), mint, buy, is_channel=False)
        except Exception:
            continue

    # mirror into trending channel
    if MIRROR_TO_TRENDING and TRENDING_POST_CHAT_ID:
        try:
            cid = await get_trending_chat_id(app)
            if cid:
                await _send_buy_to_chat(app, cid, mint, buy, is_channel=True)
        except Exception:
            pass

# -------------------- HEALTHCHECK# -------------------- HEALTHCHECK --------------------
# Railway doesn't require an HTTP server for Telegram polling bots.
# But if you want a health endpoint, we run Flask in a *separate thread*
# so it never blocks the asyncio event loop used by python-telegram-bot.
flask_app = Flask(__name__)

@flask_app.get("/")
def health():
    return "ok", 200

def start_health_server() -> None:
    port = int(os.getenv("PORT", "8080"))
    flask_app.run(host="0.0.0.0", port=port)

# -------------------- APP INIT --------------------
async def post_init(app: Application) -> None:
    load_all()
    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            BOT_USERNAME = (await app.bot.get_me()).username or ""
        except Exception:
            BOT_USERNAME = BOT_USERNAME or ""
    # background tasks (create on the running loop, not via PTB's create_task)
    loop = asyncio.get_running_loop()
    loop.create_task(buy_poller(app))
    loop.create_task(poll_payments(app))
    loop.create_task(leaderboard_loop(app))

    # health server in a daemon thread (non-blocking)
    import threading
    threading.Thread(target=start_health_server, daemon=True).start()

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is required.")

    # Build PTB Application. Any async initialization (e.g., fetching bot username)
    # is done inside post_init().
    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("myid", cmd_myid))
    application.add_handler(CommandHandler("claim_owner", cmd_claim_owner))
    application.add_handler(CommandHandler("continue", cmd_continue))
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

    application.add_handler(CommandHandler("leaderboard_init", cmd_leaderboard_init))
    application.add_handler(CommandHandler("leaderboard_reset", cmd_leaderboard_reset))
    application.add_handler(CommandHandler("force_trending", cmd_force_trending))
    application.add_handler(CommandHandler("force_ads", cmd_force_ads))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION) & ~filters.COMMAND, on_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()