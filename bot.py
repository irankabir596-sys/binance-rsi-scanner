import os
import time
import html
import requests

# =========================
# SETTINGS
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BINANCE = "https://data-api.binance.vision"

RSI_PERIOD = 14
TOP_COINS = 200

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d"
}

TELEGRAM_LIMIT = 3500


# =========================
# TELEGRAM
# =========================

def send_telegram(message):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        },
        timeout=20
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise Exception("Telegram API error")

    print("Telegram message sent successfully!")


def send_long_message(message):

    if len(message) <= TELEGRAM_LIMIT:
        send_telegram(message)
        return

    chunks = []
    current = ""

    for line in message.split("\n"):

        if len(current) + len(line) + 1 > TELEGRAM_LIMIT:

            if current:
                chunks.append(current)

            current = line

        else:

            if current:
                current += "\n"

            current += line

    if current:
        chunks.append(current)

    total = len(chunks)

    for i, chunk in enumerate(chunks, 1):

        header = (
            f"📄 <b>قسمت {i}/{total}</b>\n\n"
        )

        send_telegram(
            header + chunk
        )

        time.sleep(0.5)


# =========================
# RSI
# =========================

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):

        change = prices[i] - prices[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =========================
# TOP COINS
# =========================

def get_top_coins():

    url = BINANCE + "/api/v3/ticker/24hr"

    response = requests.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    coins = []

    for item in data:

        symbol = item.get("symbol", "")

        # فقط جفت‌های USDT
        if not symbol.endswith("USDT"):
            continue

        # حذف توکن‌های اهرمی
        if any(x in symbol for x in [
            "UPUSDT",
            "DOWNUSDT",
            "BULLUSDT",
            "BEARUSDT"
        ]):
            continue

        try:

            volume = float(
                item["quoteVolume"]
            )

            coins.append(
                (symbol, volume)
            )

        except (ValueError, KeyError):
            continue

    coins.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        symbol
        for symbol, volume in coins[:TOP_COINS]
    ]


# =========================
# GET RSI
# =========================

def get_rsi(symbol, interval):

    url = BINANCE + "/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": 100
    }

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not data or len(data) < RSI_PERIOD + 2:
        return None

    # حذف کندل در حال تشکیل
    data = data[:-1]

    prices = [
        float(candle[4])
        for candle in data
    ]

    return calculate_rsi(
        prices,
        RSI_PERIOD
    )


# =========================
# SCANNER
# =========================

def scan():

    print(
        "Starting Binance RSI Scanner..."
    )

    symbols = get_top_coins()

    print(
        f"Scanning {len(symbols)} coins..."
    )

    signals = []

    for index, symbol in enumerate(
        symbols,
        1
    ):

        print(
            f"[{index}/{len(symbols)}] {symbol}"
        )

        for timeframe, interval in TIMEFRAMES.items():

            try:

                value = get_rsi(
                    symbol,
                    interval
                )

                if value is None:
                    continue

                # RSI بالای 70
                if value >= 70:

                    signals.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "rsi": value,
                        "status": "OVERBOUGHT"
                    })

                # RSI پایین 30
                elif value <= 30:

                    signals.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "rsi": value,
                        "status": "OVERSOLD"
                    })

            except Exception as error:

                print(
                    f"Error {symbol} "
                    f"{timeframe}: {error}"
                )

            time.sleep(0.05)

    return signals


# =========================
# TELEGRAM MESSAGE
# =========================

def create_message(signals):

    if not signals:

        return (
            "📊 <b>BINANCE RSI SCANNER</b>\n\n"
            f"🔎 بررسی {TOP_COINS} ارز برتر\n"
            "📈 RSI(14)\
