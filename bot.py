import requests
import json
import os
import time
from datetime import datetime, timezone

# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID = os.getenv("CHAT_ID")

# Private link code:
# https://t.me/YOUR_BOT_USERNAME?start=PRIVATE_CODE
PRIVATE_CODE = os.getenv("PRIVATE_CODE", "RSI2026")

AUTHORIZED_FILE = "authorized_users.json"

BINANCE_URL = "https://data-api.binance.vision"

RSI_PERIOD = 14
TOP_COINS = 200

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d"
}

RSI_LOW = 30
RSI_HIGH = 70

CHECK_INTERVAL = 300
STATE_FILE = "state.json"

# =========================================================
# CHECK SETTINGS
# =========================================================

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN is not set")

if not OWNER_CHAT_ID:
    raise Exception("CHAT_ID is not set")


# =========================================================
# TELEGRAM
# =========================================================

def telegram_url(method):
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def send_message(chat_id, message):
    try:
        response = requests.post(
            telegram_url("sendMessage"),
            data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=15
        )
        return response.ok

    except Exception as e:
        print("Telegram error:", e)
        return False


# =========================================================
# AUTHORIZED USERS
# =========================================================

def load_authorized_users():
    if not os.path.exists(AUTHORIZED_FILE):
        return []

    try:
        with open(AUTHORIZED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return [str(x) for x in data]

    except Exception as e:
        print("Authorized users file error:", e)

    return []


def save_authorized_users(users):
    try:
        with open(AUTHORIZED_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print("Save users error:", e)


def is_authorized(chat_id):
    chat_id = str(chat_id)

    if chat_id == str(OWNER_CHAT_ID):
        return True

    users = load_authorized_users()
    return chat_id in users


def authorize_user(chat_id):
    chat_id = str(chat_id)
    users = load_authorized_users()

    if chat_id not in users:
        users.append(chat_id)
        save_authorized_users(users)

    return True


# =========================================================
# RSI
# =========================================================

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    current_rsi = 100 - (100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

        if avg_loss == 0:
            current_rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            current_rsi = 100 - (100 / (1 + rs))

    return current_rsi


# =========================================================
# BINANCE
# =========================================================

def get_top_symbols(limit=200):
    try:
        url = f"{BINANCE_URL}/api/v3/ticker/24hr"

        response = requests.get(url, timeout=20)
        response.raise_for_status()

        data = response.json()

        usdt_pairs = []

        blocked = [
            "USDCUSDT",
            "FDUSDUSDT",
            "TUSDUSDT",
            "USDPUSDT",
            "DAIUSDT"
        ]

        for item in data:
            symbol = item.get("symbol", "")

            if not symbol.endswith("USDT"):
                continue

            if symbol in blocked:
                continue

            try:
                volume = float(item.get("quoteVolume", 0))
            except Exception:
                volume = 0

            usdt_pairs.append((symbol, volume))

        usdt_pairs.sort(key=lambda x: x[1], reverse=True)

        return [symbol for symbol, volume in usdt_pairs[:limit]]

    except Exception as e:
        print("Binance symbols error:", e)
        return []


def get_closes(symbol, interval, limit=100):
    try:
        url = f"{BINANCE_URL}/api/v3/klines"

        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }

        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        return [float(candle[4]) for candle in data]

    except Exception as e:
        print(f"Kline error {symbol} {interval}:", e)
        return []


# =========================================================
# STATE
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("State save error:", e)


# =========================================================
# RSI SCANNER
# =========================================================

def scan_market():
    print(
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "Scanning..."
    )

    symbols = get_top_symbols(TOP_COINS)

    if not symbols:
        print("No symbols found.")
        return []

    alerts = []

    for symbol in symbols:
        for timeframe_name, interval in TIMEFRAMES.items():

            closes = get_closes(symbol, interval, 100)

            if len(closes) < RSI_PERIOD + 1:
                continue

            value = rsi(closes, RSI_PERIOD)

            if value is None:
                continue

            if value <= RSI_LOW:
                alerts.append({
                    "symbol": symbol,
                    "timeframe": timeframe_name,
                    "rsi": value,
                    "type": "OVERSOLD"
                })

            elif value >= RSI_HIGH:
                alerts.append({
                    "symbol": symbol,
                    "timeframe": timeframe_name,
                    "rsi": value,
                    "type": "OVERBOUGHT"
                })

    return alerts


# =========================================================
# FORMAT ALERT
# =========================================================

def format_alerts(alerts):
    if not alerts:
        return None

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    message = (
        "📊 <b>RSI ALERT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}\n\n"
    )

    oversold = [x for x in alerts if x["type"] == "OVERSOLD"]
    overbought = [x for x in alerts if x["type"] == "OVERBOUGHT"]

    if oversold:
        message += "🟢 <b>RSI ≤ 30</b>\n\n"

        for item in oversold:
            message += (
                f"🟢 <b>{item['symbol']}</b> "
                f"| {item['timeframe']} "
                f"| RSI: <b>{item['rsi']:.2f}</b>\n"
            )

        message += "\n"

    if overbought:
        message += "🔴 <b>RSI ≥ 70</b>\n\n"

        for item in overbought:
            message += (
                f"🔴 <b>{item['symbol']}</b> "
                f"| {item['timeframe']} "
                f"| RSI: <b>{item['rsi']:.2f}</b>\n"
            )

    return message


# =========================================================
# DUPLICATE ALERT FILTER
# =========================================================

def filter_new_alerts(alerts):
    state = load_state()
    new_alerts = []

    for item in alerts:
        key = (
            f"{item['symbol']}_"
            f"{item['timeframe']}_"
            f"{item['type']}"
        )

        current_rsi = item["rsi"]
        previous_rsi = state.get(key)

        if previous_rsi is None:
            new_alerts.append(item)
        else:
            previous_rsi = float(previous_rsi)

            if item["type"] == "OVERSOLD":
                if previous_rsi > RSI_LOW:
                    new_alerts.append(item)

            elif item["type"] == "OVERBOUGHT":
                if previous_rsi < RSI_HIGH:
                    new_alerts.append(item)

        state[key] = current_rsi

    save_state(state)

    return new_alerts


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_start(chat_id, text):
    parts = text.split()

    if len(parts) < 2:
        send_message(
            chat_id,
            "🔒 <b>دسترسی خصوصی</b>\n\n"
            "این بات خصوصی است.\n"
            "برای استفاده باید از لینک دعوت خصوصی وارد شوید."
        )
        return

    code = parts[1]

    if code != PRIVATE_CODE:
        send_message(
            chat_id,
            "❌ لینک دعوت معتبر نیست."
        )
        return

    authorize_user(chat_id)

    send_message(
        chat_id,
        "✅ <b>دسترسی فعال شد!</b>\n\n"
        "شما اکنون به ربات RSI دسترسی دارید.\n\n"
        "📊 بررسی ارزها:\n"
        "• Top 200 Binance\n"
        "• 5m\n"
        "• 15m\n"
        "• 1h\n"
        "• 4h\n"
        "• 1D\n\n"
        "🟢 RSI ≤ 30\n"
        "🔴 RSI ≥ 70"
    )


def process_updates(offset=None):
    try:
        response = requests.get(
            telegram_url("getUpdates"),
            params={
                "offset": offset,
                "timeout": 30
            },
            timeout=40
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            return offset

        updates = data.get("result", [])

        for update in updates:
            update_id = update["update_id"]
            offset = update_id + 1

            message = update.get("message")

            if not message:
                continue

            chat = message.get("chat")

            if not chat:
                continue

            chat_id = chat.get("id")

            text = message.get("text", "").strip()

            if not text:
                continue

            if text.startswith("/start"):
                handle_start(chat_id, text)
                continue

            if not is_authorized(chat_id):
                send_message(
                    chat_id,
                    "🔒 <b>دسترسی ندارید.</b>\n\n"
                    "برای استفاده از بات باید "
                    "از لینک دعوت خصوصی وارد شوید."
                )
                continue

            if text == "/status":
                send_message(
                    chat_id,
                    "🟢 <b>Bot is running</b>\n\n"
                    "RSI Period: 14\n"
                    "Top Coins: 200\n"
                    "Timeframes: 5m / 15m / 1h / 4h / 1D\n"
                    "Oversold: RSI ≤ 30\n"
                    "Overbought: RSI ≥ 70"
                )

            elif text == "/id":
                send_message(
                    chat_id,
                    f"🆔 Chat ID:\n<code>{chat_id}</code>"
                )

            elif text == "/help":
                send_message(
                    chat_id,
                    "📖 <b>Commands</b>\n\n"
                    "/status - وضعیت ربات\n"
                    "/id - نمایش Chat ID\n"
                    "/help - راهنما"
                )

        return offset

    except Exception as e:
        print("Update error:", e)
        return offset


# =========================================================
# MAIN
# =========================================================

def main():
    print("===================================")
    print("      RSI TELEGRAM BOT")
    print("      PRIVATE LINK ENABLED")
    print("===================================")

    offset = None
    last_scan = 0

    while True:
        offset = process_updates(offset)

        current_time = time.time()

        if current_time - last_scan >= CHECK_INTERVAL:
            try:
                alerts = scan_market()

                print("Total alerts:", len(alerts))

                new_alerts = filter_new_alerts(alerts)

                print("New alerts:", len(new_alerts))

                if new_alerts:
                    message = format_alerts(new_alerts)
                    users = load_authorized_users()

                    for user_id in users:
                        send_message(user_id, message)

                    if str(OWNER_CHAT_ID) not in users:
                        send_message(OWNER_CHAT_ID, message)

                last_scan = current_time

            except Exception as e:
                print("Scanner error:", e)

        time.sleep(2)


if __name__ == "__main__":
    main()
