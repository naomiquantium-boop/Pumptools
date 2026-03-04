# PumpTools BuyBot v1 (BuyBot + Trending + Ads)

This is the **PumpTools** Telegram bot for **Solana** tokens.
It can:
- Post buy alerts in **groups**
- Mirror buy alerts into your **trending channel**
- Provide **/trending** (Top 3 / Top 10) and **/ads** booking in the same bot (paid in SOL)
- Maintain a **leaderboard** message in your trending channel

## How buys are detected (important)
This bot uses **Helius Enhanced Transactions** by address:
`GET /v0/addresses/{address}/transactions?api-key=...` (Helius docs)

So for EACH token you track, you must set a `watch_address`:
- Pump.fun tokens: set it to the **bonding curve address** (best)
- Other Solana tokens: set it to the **pool address** you want to watch (Raydium/Orca/etc)

Then the bot looks for SWAP events where SOL is input and the token mint is output.

## Environment variables (Railway)
Required:
- `BOT_TOKEN` = Telegram bot token
- `HELIUS_API_KEY` = Helius API key (mainnet)
- `PAY_WALLET` = Solana wallet address to receive booking payments (defaults to PumpTools wallet)

Recommended:
- `TRENDING_POST_CHAT_ID` = numeric id of your trending channel (example: `-1001234567890`)
- `MIRROR_TO_TRENDING` = `1` to mirror all buys into the trending channel
- `OWNER_IDS` = comma separated Telegram user ids (admins) e.g. `123,456`
- `DATA_DIR` = `/data` (Railway volume mount recommended)

Pricing (defaults match your screenshots):
- `TOP3_PRICES` default: `2h=0.14,3h=0.73,6h=1.47,12h=2.03,24h=2.92`
- `TOP10_PRICES` default: `3h=0.46,6h=1.09,12h=1.37,24h=2.19`
- `ADS_PACKAGES` default: `6h=1,12h=1.5,24h=3`

Leaderboard:
- `LEADERBOARD_ON` = `1`
- `LEADERBOARD_INTERVAL` = `60`

## Commands
Public:
- `/start`
- `/tokens`
- `/trending <mint> <duration>`  (shows wallet + memo invoice)
- `/ads <mint> <duration>`
- `/confirm <invoice_id> <tx_signature>` (fallback if user can't add memo)

Owner-only:
- `/addtoken <mint> <SYMBOL> <Name...>`
- `/setwatch <mint> <pool_or_bonding_curve_address>`
- `/deltoken <mint>`
- `/adset * <text> | <link>` (global rotation ads)
- `/adset <mint> <text> | <link>` (token-specific ads)

## Files
All state is JSON (easy to edit):
- `tokens_public.json`
- `groups_public.json`
- `ads_public.json`
- `bookings_public.json`
- `invoices_public.json`
- `seen_public.json`

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN=...
export HELIUS_API_KEY=...
export PAY_WALLET=...
python main.py
```
