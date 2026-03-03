import asyncio
import logging
import os
from flask import Flask, request, jsonify

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from .config import (
    BOT_TOKEN, DATA_DIR, TRENDING_CHANNEL_CHAT_ID, TRENDING_CHANNEL_USERNAME,
    LEADERBOARD_INTERVAL_SEC, LEADERBOARD_MESSAGE_ID, DEFAULT_MIN_BUY_SOL,
    BOOKING_WALLET, BOT_DEEP_TRENDING, BOT_DEEP_ADS, BOT_USERNAME
)
from .storage import Store
from .keyboards import leaderboard_kb, paid_confirm_kb, back_home_kb
from .templates import leaderboard_text
from .leaderboard import build_top10_combined
from .booking import make_order
from .solana import verify_payment
from .webhook import extract_event
from .buys import (
    should_post_group, should_post_channel, format_links, update_stats,
    build_group_message, build_channel_message, make_group_buttons, make_channel_buttons
)
from .admin import admin_help, addtoken, deltoken, post_to_channel, addownerad, delownerad

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pumptools")

app = Flask(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Welcome to PumpTools\n\n"
        "Commands:\n"
        "/trending - book trending\n"
        "/ads - book ads\n"
        "/setminbuy <sol> - set min buy for this group (default 0.05)\n"
        "/enable <mint> <symbol> - enable token buys in this group\n"
        "/disable <mint> - disable token\n"
    )
    await update.message.reply_text(txt)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_setminbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store: Store = context.application.bot_data['store']
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("Use this in a group.")
        return
    if not context.args:
        await update.message.reply_text(f"Usage: /setminbuy 0.05 (current default {DEFAULT_MIN_BUY_SOL})")
        return
    try:
        v = float(context.args[0])
    except Exception:
        await update.message.reply_text("Invalid number")
        return
    g = store.groups.setdefault(str(chat.id), {"enabled_mints": [], "min_buy": DEFAULT_MIN_BUY_SOL})
    g['min_buy'] = v
    store.groups[str(chat.id)] = g
    store.flush()
    await update.message.reply_text(f"✅ Min buy set to {v} SOL")


async def cmd_enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store: Store = context.application.bot_data['store']
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("Use this in a group.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /enable <mint> <symbol>")
        return
    mint, symbol = context.args[0], context.args[1].upper()
    # group
    g = store.groups.setdefault(str(chat.id), {"enabled_mints": [], "min_buy": DEFAULT_MIN_BUY_SOL})
    if mint not in g['enabled_mints']:
        g['enabled_mints'].append(mint)
    store.groups[str(chat.id)] = g
    # token registry
    t = store.tokens.get(mint) or {"mint": mint}
    t['symbol'] = symbol
    t.setdefault('post_to_channel', True)
    store.tokens[mint] = t
    store.flush()
    await update.message.reply_text(f"✅ Enabled ${symbol} in this group")


async def cmd_disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store: Store = context.application.bot_data['store']
    chat = update.effective_chat
    if chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("Use this in a group.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /disable <mint>")
        return
    mint = context.args[0]
    g = store.groups.setdefault(str(chat.id), {"enabled_mints": [], "min_buy": DEFAULT_MIN_BUY_SOL})
    g['enabled_mints'] = [m for m in g.get('enabled_mints', []) if m != mint]
    store.groups[str(chat.id)] = g
    store.flush()
    await update.message.reply_text("✅ Disabled")


def _trending_menu_text() -> str:
    return "📈 PumpTools Trending — Book a Slot\n\nSelect category:" 


async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # simple text menu + instructions (wizard via callback data)
    kb = [
        [
            {"text": "⬇️ Top 3 ⬇️", "cb": "trcat:top3"},
            {"text": "⬇️ Top 10 ⬇️", "cb": "trcat:top10"},
        ]
    ]
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(x['text'], callback_data=x['cb']) for x in kb[0]]])
    await update.message.reply_text(_trending_menu_text(), reply_markup=markup)


async def cmd_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("6H | 1 SOL", callback_data="ads:6h"), InlineKeyboardButton("12H | 1.5 SOL", callback_data="ads:12h")],
        [InlineKeyboardButton("24H | 3 SOL", callback_data="ads:24h")]
    ])
    await update.message.reply_text("📢 PumpTools Ads (shown under buy alerts)\n\nChoose duration:", reply_markup=markup)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store: Store = context.application.bot_data['store']
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # Trending category
    if data.startswith("trcat:"):
        cat = data.split(":",1)[1]
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        if cat == "top3":
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("2 hours | 0.14 SOL", callback_data="trpkg:top3:2h")],
                [InlineKeyboardButton("3 hours | 0.73 SOL", callback_data="trpkg:top3:3h")],
                [InlineKeyboardButton("6 hours | 1.47 SOL", callback_data="trpkg:top3:6h")],
                [InlineKeyboardButton("12 hours | 2.03 SOL", callback_data="trpkg:top3:12h")],
                [InlineKeyboardButton("24 hours | 2.92 SOL", callback_data="trpkg:top3:24h")],
            ])
        else:
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("3 hours | 0.46 SOL", callback_data="trpkg:top10:3h")],
                [InlineKeyboardButton("6 hours | 1.09 SOL", callback_data="trpkg:top10:6h")],
                [InlineKeyboardButton("12 hours | 1.37 SOL", callback_data="trpkg:top10:12h")],
                [InlineKeyboardButton("24 hours | 2.19 SOL", callback_data="trpkg:top10:24h")],
            ])
        await q.edit_message_text("Select duration:", reply_markup=markup)
        return

    # choose package -> ask for mint
    if data.startswith("trpkg:"):
        _, cat, pkg = data.split(":",2)
        context.user_data['pending'] = {"kind": "trending_top3" if cat=="top3" else "trending_top10", "package": pkg}
        await q.edit_message_text("Send token mint address:")
        return

    if data.startswith("ads:"):
        pkg = data.split(":",1)[1]
        context.user_data['pending'] = {"kind": "ads", "package": pkg}
        await q.edit_message_text("Send ad text (you can include a link):")
        return

    if data.startswith("paid:"):
        ref = data.split(":",1)[1]
        order = store.seen.get('orders', {}).get(ref)
        if not order:
            await q.edit_message_text("Order not found.")
            return
        # verify payment
        proof = verify_payment(reference=ref, expected_sol=float(order['price']))
        if not proof:
            await q.edit_message_text("Payment not found yet. Make sure you included the reference in memo then try again.")
            return
        # activate
        now = store.now()
        end = now + int(order['hours'])*3600
        if order['kind'].startswith('trending'):
            store.bookings.setdefault('trending', []).append({
                "mint": order['mint'], "start": now, "end": end,
                "kind": order['kind'], "package": order['package'],
                "user_id": order['user_id'], "ref": ref,
                "tx": proof['signature'],
            })
            store.flush()
            await q.edit_message_text(f"✅ Trending activated for ${store.tokens.get(order['mint'],{}).get('symbol','TOKEN')} ({order['hours']}H)")
        else:
            # ads
            store.ads.setdefault('paid', []).append({
                "text": order.get('ad_text') or 'Advertise here',
                "link": order.get('ad_link'),
                "start": now,
                "end": end,
                "ref": ref,
                "user_id": order['user_id'],
                "tx": proof['signature'],
            })
            store.flush()
            await q.edit_message_text("✅ Ads activated")
        return

    if data.startswith("cancel:"):
        await q.edit_message_text("Cancelled.")
        return


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store: Store = context.application.bot_data['store']
    pending = context.user_data.get('pending')
    if not pending:
        return

    kind = pending['kind']
    pkg = pending['package']

    # Trending: message should be mint
    if kind.startswith('trending'):
        mint = (update.message.text or "").strip()
        if not mint:
            await update.message.reply_text("Invalid mint. Send token mint address:")
            return
        # ensure token
        t = store.tokens.get(mint) or {"mint": mint, "symbol": "SYMBOL", "post_to_channel": True}
        store.tokens[mint] = t
        # create order
        order = make_order(kind, pkg, mint, update.effective_user.id)
        store.seen.setdefault('orders', {})[order['ref']] = order
        store.flush()
        context.user_data.pop('pending', None)

        await update.message.reply_text(
            f"✅ Order Summary\n\n"
            f"Token: ${t.get('symbol','SYMBOL')}\n"
            f"Slot: {'Top 3' if kind=='trending_top3' else 'Top 10'}\n"
            f"Duration: {order['hours']} hours\n"
            f"Price: {order['price']} SOL\n\n"
            f"Pay to (Solana):\n{BOOKING_WALLET}\n\n"
            f"Reference: {order['ref']}\n"
            f"(Include this reference in transfer note/memo)\n",
            reply_markup=paid_confirm_kb(order['ref'])
        )
        return

    # Ads: capture ad text
    if kind == 'ads':
        body = (update.message.text or "").strip()
        if not body:
            await update.message.reply_text("Send ad text (you can include a link):")
            return
        # naive link detection
        ad_text = body
        ad_link = None
        for token in body.split():
            if token.startswith('http://') or token.startswith('https://'):
                ad_link = token
                break

        # order uses mint placeholder
        order = make_order('ads', pkg, mint='-', user_id=update.effective_user.id)
        order['ad_text'] = ad_text
        order['ad_link'] = ad_link
        store.seen.setdefault('orders', {})[order['ref']] = order
        store.flush()
        context.user_data.pop('pending', None)

        await update.message.reply_text(
            f"✅ Order Summary\n\n"
            f"Type: Ads\n"
            f"Duration: {order['hours']} hours\n"
            f"Price: {order['price']} SOL\n\n"
            f"Pay to (Solana):\n{BOOKING_WALLET}\n\n"
            f"Reference: {order['ref']}\n"
            f"(Include this reference in transfer note/memo)\n",
            reply_markup=paid_confirm_kb(order['ref'])
        )
        return


async def leaderboard_loop(app_):
    store: Store = app_.bot_data['store']
    bot = app_.bot

    if not TRENDING_CHANNEL_CHAT_ID:
        log.warning("TRENDING_CHANNEL_CHAT_ID not set; leaderboard cannot post.")
        return

    # set message id from env or store
    if LEADERBOARD_MESSAGE_ID and not store.leaderboard.get('message_id'):
        try:
            store.leaderboard['message_id'] = int(LEADERBOARD_MESSAGE_ID)
            store.flush()
        except Exception:
            pass

    while True:
        try:
            rows = build_top10_combined(store)
            text = leaderboard_text(rows)
            mid = store.leaderboard.get('message_id')

            if not mid:
                m = await bot.send_message(chat_id=int(TRENDING_CHANNEL_CHAT_ID), text=text, reply_markup=leaderboard_kb())
                store.leaderboard['message_id'] = m.message_id
                store.flush()
            else:
                await bot.edit_message_text(chat_id=int(TRENDING_CHANNEL_CHAT_ID), message_id=int(mid), text=text, reply_markup=leaderboard_kb())

        except Exception as e:
            log.exception("leaderboard loop error: %s", e)

        await asyncio.sleep(LEADERBOARD_INTERVAL_SEC)


@app.route('/health', methods=['GET'])
def health():
    return 'ok', 200


@app.route('/webhook/solana', methods=['POST'])
def solana_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    ev = extract_event(payload)
    if not ev:
        return jsonify({"ok": False, "error": "unrecognized"}), 200

    # store event for async processing by bot
    global _WEBHOOK_QUEUE
    _WEBHOOK_QUEUE.append(ev)
    return jsonify({"ok": True}), 200


_WEBHOOK_QUEUE = []


async def webhook_loop(app_):
    store: Store = app_.bot_data['store']
    bot = app_.bot

    while True:
        try:
            if not _WEBHOOK_QUEUE:
                await asyncio.sleep(0.2)
                continue
            ev = _WEBHOOK_QUEUE.pop(0)
            mint = ev.get('mint')
            sol_amt = float(ev.get('sol') or 0)
            if not mint:
                continue

            # enrich symbol
            t = store.tokens.get(mint)
            if t and t.get('symbol'):
                ev['symbol'] = t['symbol']

            ev = format_links(ev)

            # update stats for organic
            update_stats(store, mint, sol_amt)
            store.flush()

            # post to groups where enabled
            for chat_id_str, gs in list(store.groups.items()):
                try:
                    chat_id = int(chat_id_str)
                except Exception:
                    continue
                if should_post_group(store, chat_id, mint, sol_amt):
                    txt = build_group_message(store, ev)
                    await bot.send_message(chat_id=chat_id, text=txt, reply_markup=make_group_buttons(ev))

            # post to trending channel
            if TRENDING_CHANNEL_CHAT_ID and should_post_channel(store, mint, sol_amt):
                txt = build_channel_message(store, ev)
                await bot.send_message(chat_id=int(TRENDING_CHANNEL_CHAT_ID), text=txt, reply_markup=make_channel_buttons())

        except Exception as e:
            log.exception("webhook loop error: %s", e)
        await asyncio.sleep(0.01)


def run_flask():
    port = int(os.getenv('PORT', '8080'))
    app.run(host='0.0.0.0', port=port)


async def main():
    store = Store(DATA_DIR)

    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
    tg_app.bot_data['store'] = store

    tg_app.add_handler(CommandHandler('start', cmd_start))
    tg_app.add_handler(CommandHandler('help', cmd_help))
    tg_app.add_handler(CommandHandler('trending', cmd_trending))
    tg_app.add_handler(CommandHandler('ads', cmd_ads))
    tg_app.add_handler(CommandHandler('setminbuy', cmd_setminbuy))
    tg_app.add_handler(CommandHandler('enable', cmd_enable))
    tg_app.add_handler(CommandHandler('disable', cmd_disable))

    # owner commands
    tg_app.add_handler(CommandHandler('admin', admin_help))
    tg_app.add_handler(CommandHandler('addtoken', addtoken))
    tg_app.add_handler(CommandHandler('deltoken', deltoken))
    tg_app.add_handler(CommandHandler('post_to_channel', post_to_channel))
    tg_app.add_handler(CommandHandler('addownerad', addownerad))
    tg_app.add_handler(CommandHandler('delownerad', delownerad))

    tg_app.add_handler(CallbackQueryHandler(on_callback))
    tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_message))

    # start telegram + polling
    # NOTE: Application.start() does NOT start receiving updates by itself.
    # We must start polling (or set a Telegram webhook). For Railway, polling is simplest.
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)

    # start background tasks
    tg_app.create_task(leaderboard_loop(tg_app))
    tg_app.create_task(webhook_loop(tg_app))

    # run flask in thread
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_flask)


if __name__ == '__main__':
    asyncio.run(main())
