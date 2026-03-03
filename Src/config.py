import os
import re

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Brand / links
TRENDING_CHANNEL_USERNAME = os.getenv("TRENDING_CHANNEL_USERNAME", "@PumpToolsTrending").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "@Pump_ToolsBot").strip()
BOOKING_WALLET = os.getenv("BOOKING_WALLET", "3nuVyrqMLvxLx8finrRLH3zWufJk7AKAWZroH7upwDdQ").strip()

# Telegram IDs (recommended for stable posting)
TRENDING_CHANNEL_CHAT_ID = os.getenv("TRENDING_CHANNEL_CHAT_ID", "").strip()  # -100...
LEADERBOARD_MESSAGE_ID = os.getenv("LEADERBOARD_MESSAGE_ID", "").strip()     # existing msg id if you already have one

# Behavior
LEADERBOARD_INTERVAL_SEC = max(10, int(float(os.getenv("LEADERBOARD_INTERVAL_SEC", "30"))))
DEFAULT_MIN_BUY_SOL = float(os.getenv("DEFAULT_MIN_BUY_SOL", "0.05"))

# Solana / verification
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()
PAY_VERIFY_LOOKBACK = max(10, int(os.getenv("PAY_VERIFY_LOOKBACK", "50")))  # signatures to scan

# Storage
DATA_DIR = os.getenv("DATA_DIR", "./data").strip()

# Owners
OWNER_IDS = [int(x) for x in re.split(r"[ ,;]+", os.getenv("OWNER_IDS", "").strip()) if x.isdigit()]

# Ads defaults
DEFAULT_AD_TEXT = os.getenv("DEFAULT_AD_TEXT", "Advertise here").strip()
DEFAULT_AD_LINK = os.getenv("DEFAULT_AD_LINK", f"https://t.me/{BOT_USERNAME.lstrip('@')}").strip()

# Prices (already reduced by 30% for trending)
PRICES = {
  "trending_top3": {
    "2h": 0.14,
    "3h": 0.73,
    "6h": 1.47,
    "12h": 2.03,
    "24h": 2.92,
  },
  "trending_top10": {
    "3h": 0.46,
    "6h": 1.09,
    "12h": 1.37,
    "24h": 2.19,
  },
  "ads": {
    "6h": 1.0,
    "12h": 1.5,
    "24h": 3.0,
  }
}

# Deep links
BOT_DEEP_TRENDING = os.getenv("BOT_DEEP_TRENDING", f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=trending").strip()
BOT_DEEP_ADS = os.getenv("BOT_DEEP_ADS", f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=ads").strip()

if not BOT_TOKEN:
  raise RuntimeError("BOT_TOKEN is required")
