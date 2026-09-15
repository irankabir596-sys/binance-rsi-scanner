import os
import time
import requests

# ==================================================
# SETTINGS
# ==================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BINANCE_URL = "https://data-api.binance.vision"

TOP_COINS = 200
RSI_PERIOD = 14

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d",
}

TELEGRAM_MESSAGE_LIMIT = 3500


# ==================================================
# HTTP SESSION
# ==================================================

session = requests.Session()


# ==================================================
# TELEGRAM
# ==================================================

def send_telegram(message):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = session.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if result.get("ok") is not True:
        raise Exception(
            f"Telegram API error: {result}"
        )

    print("Telegram message sent.")


def send_long_telegram(message):

    # اگر پیام کوتاه باشد
    if len(message) <= TELEGRAM_MESSAGE_LIMIT:

        send_telegram(message)
        return

    # تقسیم پیام به چند قسمت
    lines = message.split("\n")

    parts = []
    current = ""

    for line in lines:

        if len(current) + len(line) + 1 > TELEGRAM_MESSAGE_LIMIT:

            if current:
                parts.append(current)

            current = line

        else:

            if current:
                current += "\n"

            current += line

    if current:
        parts.append(current)

    total = len(parts)

    for number, part in enumerate(parts, 1):

        header = f"📄 بخش {number}/{total}\n\n"

        send_telegram(
            header + part
        )

        time.sleep(1)


# ==================================================
# RSI
# ==================================================

def calculate_rsi(prices, period=14):

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

    average_gain = sum(
        gains[:period]
    ) / period

    average_loss = sum(
        losses[:period]
    ) / period

    for i in range(period, len(gains)):

        average_gain = (
            (
                average_gain * (period - 1)
            ) + gains[i]
        ) / period

        average_loss = (
            (
                average_loss * (period - 1)
            ) + losses[i]
        ) / period

    if average_loss == 0:

        return 100.0

    if average_gain == 0:

        return 0.0

    rs = average_gain / average_loss

    return 100 - (
        100 / (1 + rs)
    )


# ==================================================
# GET TOP COINS
# ==================================================

def get_top_coins():

    url = (
        BINANCE_URL
        + "/api/v3/ticker/24hr"
    )

    response = session.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    coins = []

    for item in data:

        symbol = item.get(
            "symbol",
            ""
        )

        # فقط USDT
        if not symbol.endswith("USDT"):
            continue

        # حذف توکن‌های اهرمی
        if (
            symbol.startswith("UP")
            or symbol.startswith("DOWN")
            or symbol.startswith("BULL")
            or symbol.startswith("BEAR")
        ):
            continue

        try:

            volume = float(
                item["quoteVolume"]
            )

            coins.append(
                (symbol, volume)
            )

        except (
            ValueError,
            KeyError,
            TypeError
        ):

            continue

    # مرتب‌سازی بر اساس حجم
    coins.sort(
        key=lambda x: x[1],
        reverse=True
    )

    top_symbols = [
        symbol
        for symbol, volume
        in coins[:TOP_COINS]
    ]

    return top_symbols


# ==================================================
# GET KLINES
# ==================================================

def get_rsi(symbol, interval):

    url = (
        BINANCE_URL
        + "/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": 100
    }

    response = session.get(
        url,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        return None

    if len(data) < RSI_PERIOD + 2:
        return None

    # آخرین کندل هنوز در حال تشکیل است
    # بنابراین حذف می‌شود.
    closed_candles = data[:-1]

    closes = []

    for candle in closed_candles:

        try:

            closes.append(
                float(candle[4])
            )

        except (
           
