import json
import os
import time
from typing import Any, Dict


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def load_json(path: str, default: Any):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any):
    tmp = f"{path}.tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Store:
    """Simple JSON store (good enough for v1)."""

    def __init__(self, data_dir: str):
        _ensure_dir(data_dir)
        self.data_dir = data_dir
        self.paths = {
            "groups": os.path.join(data_dir, "groups.json"),
            "tokens": os.path.join(data_dir, "tokens.json"),
            "ads": os.path.join(data_dir, "ads.json"),
            "bookings": os.path.join(data_dir, "bookings.json"),
            "leaderboard": os.path.join(data_dir, "leaderboard.json"),
            "seen": os.path.join(data_dir, "seen.json"),
        }

        self.groups: Dict[str, Any] = load_json(self.paths["groups"], {})
        self.tokens: Dict[str, Any] = load_json(self.paths["tokens"], {})
        self.ads: Dict[str, Any] = load_json(self.paths["ads"], {"paid": [], "owner": []})
        self.bookings: Dict[str, Any] = load_json(self.paths["bookings"], {"trending": [], "ads": []})
        self.leaderboard: Dict[str, Any] = load_json(self.paths["leaderboard"], {"message_id": None, "last_post": 0})
        self.seen: Dict[str, Any] = load_json(self.paths["seen"], {})

    def flush(self):
        save_json(self.paths["groups"], self.groups)
        save_json(self.paths["tokens"], self.tokens)
        save_json(self.paths["ads"], self.ads)
        save_json(self.paths["bookings"], self.bookings)
        save_json(self.paths["leaderboard"], self.leaderboard)
        save_json(self.paths["seen"], self.seen)

    def now(self) -> int:
        return int(time.time())
