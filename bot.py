import os
import time
import requests


# =====================================================
# CONFIG
# =====================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BINANCE = "https://data-api.binance.vision"

TOP_COINS = 200
RSI_PERIOD = 14

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d",
}

TELEGRAM_LIMIT = 3500


# =====================================================
# HTTP SESSION
# =====================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-RSI-Scanner/1.0"
})


# =====================================================
# TELEGRAM
# =====================================================

def telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    response = session.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("ok") is not True:
        raise RuntimeError(
            "Telegram API returned an error"
        )

    print("Telegram: OK")


def telegram_long(message):

    if len(message) <= TELEGRAM_LIMIT:

        telegram(message)
        return

    parts = []
    current = ""

    for line in message.splitlines():

        # اگر یک خط خودش خیلی بلند باشد
        if len(line) > TELEGRAM_LIMIT:

            if current:
                parts.append(current)
                current = ""

            for i in range(
                0,
                len(line),
                TELEGRAM_LIMIT
            ):
                parts.append(
                    line[
                        i:i + TELEGRAM_LIMIT
                    ]
                )

            continue

        if (
            len(current)
            + len(line)
            + 1
            > TELEGRAM_LIMIT
        ):

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

    for number, part in enumerate(
        parts,
        start=1
    ):

        telegram(
            f"📄 Part {number}/{total}\n\n"
            + part
        )

        time.sleep(0.5)


# =====================================================
# RSI
# =====================================================

def calculate_rsi(
    prices,
    period=14
):

    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(prices)
    ):

        change = (
            prices[i]
            - prices[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0.0)

        else:

            gains.append(0.0)
            losses.append(-change)

    average_gain = (
        sum(gains[:period])
        / period
    )

    average_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        average_gain = (
            (
                average_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        average_loss = (
            (
                average_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0

    relative_strength = (
        average_gain
        / average_loss
    )

    return (
        100
        - (
            100
            / (
                1
                + relative_strength
            )
        )
    )


# =====================================================
# TOP 200 COINS
# =====================================================

def get_top_coins():

    url = (
        BINANCE
        + "/api/v3/ticker/24hr"
    )

    response = session.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            "Invalid Binance ticker response"
        )

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
        excluded = (
            "UPUSDT",
            "DOWNUSDT",
            "BULLUSDT",
            "BEARUSDT",
        )

        if symbol.endswith(excluded):
            continue

        try:

            volume = float(
                item["quoteVolume"]
            )

        except (
            KeyError,
            ValueError,
            TypeError
        ):

            continue

        coins.append(
            (
                symbol,
                volume
            )
        )

    coins.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        symbol
        for symbol, volume
        in coins[:TOP_COINS]
    ]


# =====================================================
# GET CANDLES
# =====================================================

def get_rsi(
    symbol,
    interval
):

    url = (
        BINANCE
        + "/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": 100,
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

    # آخرین کندل ممکن است هنوز در حال تشکیل باشد.
    data = data[:-1]

    closes = []

    for candle in data:

        try:

            closes.append(
                float(candle[4])
            )

        except (
            IndexError,
            ValueError,
            TypeError
        ):

            continue

    return calculate_rsi(
        closes,
        RSI_PERIOD
    )


# =====================================================
# SCAN
# =====================================================

def scan():

    symbols = get_top_coins()

    print(
        f"Scanning {len(symbols)} coins..."
    )

    signals = []

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"{number}/{len(symbols)} "
            f"{symbol}"
        )

        for name, interval in (
            TIMEFRAMES.items()
        ):

            try:

                value = get_rsi(
                    symbol,
                    interval
                )

                if value is None:
                    continue

                if value >= 70:

                    signals.append({
                        "symbol": symbol,
                        "timeframe": name,
                        "rsi": value,
                        "status": "OVERBOUGHT",
                    })

                elif value <= 30:

                    signals.append({
                        "symbol": symbol,
                        "timeframe": name,
                        "rsi": value,
                        "status": "OVERSOLD",
                    })

            except Exception as error:

                print(
                    f"Error: "
                    f"{symbol} "
                    f"{name} "
                    f"{error}"
                )

            time.sleep(0.05)

    return signals


# =====================================================
# MESSAGE
# =====================================================

def make_message(signals):

    lines = [
        "🚨 BINANCE RSI SCANNER",
        "",
        f"🔎 Top {TOP_COINS} USDT coins",
        "📊 RSI(14)",
        "",
    ]

    overbought = [
        x
        for x in signals
        if x["status"] == "OVERBOUGHT"
    ]

    oversold = [
        x
        for x in signals
        if x["status"] == "OVERSOLD"
    ]

    if overbought:

        lines.append(
            "🔴 OVERBOUGHT — RSI >= 70"
        )

        lines.append("")

        for item in overbought:

            lines.append(
                f"🔴 {item['symbol']} | "
                f"{item['timeframe']} | "
                f"RSI: {item['rsi']:.2f}"
            )

        lines.append("")

    if oversold:

        lines.append(
            "🟢 OVERSOLD — RSI <= 30"
        )

        lines.append("")

        for item in oversold:

            lines.append(
                f"🟢 {item['symbol']} | "
                f"{item['timeframe']} | "
                f"RSI: {item['rsi']:.2f}"
            )

    if not signals:

        lines.append(
            "✅ No RSI >= 70 "
            "or RSI <= 30 found."
        )

    return "\n".join(lines)


# =====================================================
# MAIN
# =====================================================

def main():

    print(
        "===================================="
    )

    print(
        "BINANCE RSI SCANNER STARTED"
    )

    print(
        "===================================="
    )

    signals = scan()

    print(
        f"Signals found: {len(signals)}"
    )

    message = make_message(
        signals
    )

    telegram_long(message)

    print(
        "SCAN COMPLETED"
    )


if __name__ == "__main__":

    main()
