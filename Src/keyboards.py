from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .config import BOT_DEEP_TRENDING


def leaderboard_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📈 Book Trending", url=BOT_DEEP_TRENDING)]])


def paid_confirm_kb(ref: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Paid", callback_data=f"paid:{ref}"), InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{ref}")]
    ])


def back_home_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home")]])
