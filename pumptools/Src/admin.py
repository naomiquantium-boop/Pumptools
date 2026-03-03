from telegram import Update
from telegram.ext import ContextTypes

from .config import OWNER_IDS


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(
        "Owner commands:\n"
        "/addtoken <mint> <symbol>\n"
        "/deltoken <mint>\n"
        "/post_to_channel <mint> on|off\n"
        "/addownerad <text> | <link(optional)>\n"
        "/delownerad <index>\n"
    )


async def addtoken(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    store = context.application.bot_data['store']
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /addtoken <mint> <symbol>")
        return
    mint, symbol = args[0], args[1].upper()
    store.tokens[mint] = {"mint": mint, "symbol": symbol, "post_to_channel": True}
    store.flush()
    await update.message.reply_text(f"✅ Added {symbol} {mint}")


async def deltoken(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    store = context.application.bot_data['store']
    if not context.args:
        await update.message.reply_text("Usage: /deltoken <mint>")
        return
    mint = context.args[0]
    store.tokens.pop(mint, None)
    store.flush()
    await update.message.reply_text("✅ Removed")


async def post_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    store = context.application.bot_data['store']
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /post_to_channel <mint> on|off")
        return
    mint, v = context.args[0], context.args[1].lower()
    t = store.tokens.get(mint) or {"mint": mint, "symbol": "TOKEN"}
    t['post_to_channel'] = v in ('on','1','true','yes')
    store.tokens[mint] = t
    store.flush()
    await update.message.reply_text(f"✅ post_to_channel={t['post_to_channel']}")


async def addownerad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    store = context.application.bot_data['store']
    raw = update.message.text.split(' ',1)
    if len(raw) < 2:
        await update.message.reply_text("Usage: /addownerad <text> | <link(optional)>")
        return
    body = raw[1]
    parts = [p.strip() for p in body.split('|')]
    text = parts[0]
    link = parts[1] if len(parts) > 1 else None
    ad = {"text": text, "link": link, "start": 0, "end": 2**31-1}
    store.ads.setdefault('owner', []).append(ad)
    store.flush()
    await update.message.reply_text("✅ Owner ad added")


async def delownerad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    store = context.application.bot_data['store']
    if not context.args:
        await update.message.reply_text("Usage: /delownerad <index>")
        return
    i = int(context.args[0])
    arr = store.ads.get('owner', [])
    if i < 0 or i >= len(arr):
        await update.message.reply_text("Index out of range")
        return
    arr.pop(i)
    store.ads['owner'] = arr
    store.flush()
    await update.message.reply_text("✅ Removed")
