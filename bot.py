import os
import time
import json
import re
import traceback
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

BINANCE_API = "https://data-api.binance.vision"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

POLY_GAMMA = "https://gamma-api.polymarket.com"
POLY_CLOB = "https://clob.polymarket.com"

RSI_PERIOD = 14
TOP_COINS = 100

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d",
}

STATE_FILE = "state.json"
USERS_FILE = "users.json"

REQUEST_TIMEOUT = 20


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-RSI-Polymarket-Bot/1.0"
})


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        value = str(value).strip()

        if not value:
            return default

        return float(value)

    except Exception:
        return default


def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"Could not load {filename}: {e}")
        return default


def save_json(filename, data):
    temp_file = filename + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, filename)


# ============================================================
# USERS
# ============================================================

def normalize_users(raw):
    """
    Supports both old and new users.json formats.

    Old example:
    {
        "123456789": 1
    }

    New example:
    {
        "123456789": {
            "active": true
        }
    }
    """

    users = {}

    if not isinstance(raw, dict):
        return users

    for chat_id, info in raw.items():

        chat_id = str(chat_id)

        # New format
        if isinstance(info, dict):

            users[chat_id] = {
                "active": bool(info.get("active", True)),
                "username": str(info.get("username", "")),
                "first_name": str(info.get("first_name", "")),
                "last_seen": info.get("last_seen", "")
            }

        # Old format
        elif isinstance(info, bool):

            users[chat_id] = {
                "active": info,
                "username": "",
                "first_name": "",
                "last_seen": ""
            }

        elif isinstance(info, (int, float)):

            users[chat_id] = {
                "active": bool(info),
                "username": "",
                "first_name": "",
                "last_seen": ""
            }

        elif isinstance(info, str):

            active = info.lower() in (
                "true",
                "1",
                "active",
                "yes"
            )

            users[chat_id] = {
                "active": active,
                "username": "",
                "first_name": "",
                "last_seen": ""
            }

    return users


def load_users():
    raw = load_json(USERS_FILE, {})
    users = normalize_users(raw)

    # Automatically convert old database to new format
    try:
        save_json(USERS_FILE, users)
    except Exception as e:
        print(f"Could not migrate users.json: {e}")

    return users


def get_active_users(users):
    """
    Safe against every users.json format.
    """

    active_users = []

    if not isinstance(users, dict):
        return active_users

    for chat_id, info in users.items():

        try:

            if isinstance(info, dict):

                if info.get("active") is True:
                    active_users.append(str(chat_id))

            elif isinstance(info, bool):

                if info:
                    active_users.append(str(chat_id))

            elif isinstance(info, (int, float)):

                if bool(info):
                    active_users.append(str(chat_id))

            elif isinstance(info, str):

                if info.lower() in (
                    "true",
                    "1",
                    "active",
                    "yes"
                ):
                    active_users.append(str(chat_id))

        except Exception as e:
            print(f"User parsing error for {chat_id}: {e}")

    return active_users


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, data=None):
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing.")
        return None

    url = f"{TELEGRAM_API}/{method}"

    try:

        response = session.post(
            url,
            data=data or {},
            timeout=REQUEST_TIMEOUT
        )

        if not response.ok:
            print(
                f"Telegram HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return None

        result = response.json()

        if not result.get("ok"):
            print(f"Telegram API error: {result}")

        return result

    except Exception as e:
        print(f"Telegram request error: {e}")
        return None


def send_message(chat_id, text):
    return telegram_request(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
    )


def get_updates():
    return telegram_request(
        "getUpdates",
        {
            "timeout": 1,
            "allowed_updates": json.dumps(["message"])
        }
    )


def process_telegram_users(users):
    """
    Register users who send /start.
    /stop disables them.
    /status shows their status.
    """

    result = get_updates()

    if not result or not result.get("ok"):
        return users

    updates = result.get("result", [])

    for update in updates:

        try:

            message = update.get("message")

            if not isinstance(message, dict):
                continue

            chat = message.get("chat", {})
            chat_id = chat.get("id")

            if chat_id is None:
                continue

            text = str(message.get("text", "")).strip()

            chat_id = str(chat_id)

            if text.startswith("/start"):

                users[chat_id] = {
                    "active": True,
                    "username": str(
                        message.get("from", {}).get("username", "")
                    ),
                    "first_name": str(
                        message.get("from", {}).get("first_name", "")
                    ),
                    "last_seen": datetime.now(
                        timezone.utc
                    ).isoformat()
                }

                send_message(
                    chat_id,
                    "✅ ربات فعال شد.\n\n"
                    "از این به بعد سیگنال‌های RSI برای شما ارسال می‌شود."
                )

            elif text.startswith("/stop"):

                if chat_id not in users:
                    users[chat_id] = {
                        "active": False,
                        "username": "",
                        "first_name": "",
                        "last_seen": ""
                    }
                else:
                    users[chat_id]["active"] = False

                send_message(
                    chat_id,
                    "⛔ دریافت سیگنال متوقف شد."
                )

            elif text.startswith("/status"):

                active = False

                if isinstance(users.get(chat_id), dict):
                    active = users[chat_id].get("active", False)

                send_message(
                    chat_id,
                    "🟢 فعال" if active else "🔴 غیرفعال"
                )

        except Exception as e:
            print(f"Telegram update error: {e}")

    return users


# ============================================================
# BINANCE
# ============================================================

def get_top_binance_coins():
    url = f"{BINANCE_API}/api/v3/ticker/24hr"

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        coins = []

        for item in data:

            try:

                if item.get("quoteAsset") != "USDT":
                    continue

                if item.get("status") not in (None, "TRADING"):
                    continue

                symbol = str(item.get("symbol", ""))

                if not symbol.endswith("USDT"):
                    continue

                # Exclude leveraged tokens
                base = symbol[:-4]

                if base.endswith(
                    ("UP", "DOWN", "BULL", "BEAR")
                ):
                    continue

                volume_quote = safe_float(
                    item.get("quoteVolume")
                )

                coins.append({
                    "symbol": symbol,
                    "volume": volume_quote
                })

            except Exception:
                continue

        coins.sort(
            key=lambda x: x["volume"],
            reverse=True
        )

        return coins[:TOP_COINS]

    except Exception as e:

        print(f"Binance
