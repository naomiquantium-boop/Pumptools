from typing import Dict, Any, Optional
from .config import TRENDING_CHANNEL_USERNAME, DEFAULT_AD_TEXT


def fmt_num(x: float, decimals: int = 2) -> str:
    try:
        return f"{x:,.{decimals}f}"
    except Exception:
        return str(x)


def short_addr(addr: str, n: int = 4) -> str:
    if not addr:
        return ""
    if len(addr) <= 2*n:
        return addr
    return f"{addr[:n]}...{addr[-n:]}"


def leaderboard_text(rows):
    # NO HEADER per user
    lines = [f"🟢 {TRENDING_CHANNEL_USERNAME}", ""]
    for i, r in enumerate(rows[:3], start=1):
        lines.append(f"{i}️⃣ - ${r['symbol']} | {r['pct']}%")
    lines.append("-------------------------------")
    for i, r in enumerate(rows[3:10], start=4):
        emoji = "🔟" if i == 10 else f"{i}️⃣"
        lines.append(f"{emoji} - ${r['symbol']} | {r['pct']}%")
    return "\n".join(lines)


def channel_buy_text(ev: Dict[str, Any], ad_line: Optional[str] = None) -> str:
    sym = ev.get("symbol", "TOKEN")
    sol_amt = ev.get("sol", 0.0)
    usd = ev.get("usd")
    tok_amt = ev.get("token_amount")
    holders = ev.get("holders")
    buyer = ev.get("buyer")
    pct = ev.get("pct", "+0.0")
    tx_url = ev.get("tx_url")
    price = ev.get("price")
    mcap = ev.get("mcap")
    chart = ev.get("chart_url")
    buy = ev.get("buy_url")
    listing = ev.get("listing_url")

    bar = "✅" * 20
    lines = [
        "PUMPTOOLS TRENDING",
        f"| ${sym} Buy!",
        "",
        bar,
        "",
        f"▽  {fmt_num(sol_amt,2)} SOL" + (f" (${fmt_num(usd,2)})" if usd is not None else ""),
        f"🔁  {fmt_num(tok_amt,2)} ${sym}" if tok_amt is not None else f"🔁  ${sym}",
    ]
    if holders is not None:
        lines.append(f"🔁  {holders} Holders")
    if buyer and tx_url:
        lines.append(f"👤  {short_addr(buyer)}: {pct} | Txn")
    if price is not None:
        lines.append(f"💵  Price: {price}")
    if mcap is not None:
        lines.append(f"💰  MarketCap: {mcap}")

    lines.append("")
    footer = []
    if listing: footer.append("💎 Listing")
    if buy: footer.append("🐸 Buy")
    if chart: footer.append("📊 Chart")
    if footer:
        lines.append(" | ".join(footer))

    lines.append(f"ad: {ad_line or DEFAULT_AD_TEXT}")
    return "\n".join(lines)


def group_buy_text(ev: Dict[str, Any], ad_line: Optional[str] = None) -> str:
    sym = ev.get("symbol", "TOKEN")
    sol_amt = ev.get("sol", 0.0)
    tok_amt = ev.get("token_amount")
    buyer = ev.get("buyer")
    tx_url = ev.get("tx_url")

    price = ev.get("price")
    liq = ev.get("liq")
    mcap = ev.get("mcap")
    holders = ev.get("holders")

    bar = "💎" * 12

    lines = [
        f"${sym} Buy!",
        bar,
        "",
        f"Spent:  {fmt_num(sol_amt,2)} SOL",
        (f"Got:    {fmt_num(tok_amt,2)} ${sym}" if tok_amt is not None else f"Got:    ${sym}"),
        "",
    ]
    if buyer and tx_url:
        lines.append(f"{short_addr(buyer)} | Txn")
        lines.append("")

    if price is not None:
        lines.append(f"Price: {price}")
    if liq is not None:
        lines.append(f"Liquidity: {liq}")
    if mcap is not None:
        lines.append(f"MCap: {mcap}")
    if holders is not None:
        lines.append(f"Holders: {holders}")

    lines.append("")
    lines.append("TX | GT | DexS | Telegram | Trending")
    lines.append("")
    lines.append(f"ad: {ad_line or DEFAULT_AD_TEXT}")
    return "\n".join(lines)
