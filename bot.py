import os
import time
import json
import requests


# =====================================================
# CONFIG
# =====================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

BINANCE = "https://data-api.binance.vision"

TOP_COINS = 100
RSI_PERIOD = 14

VOLUME_LOOKBACK = 20

# حجم:
# کمتر از 0.75 برابر میانگین = کم
# 0.75 تا 1.5 برابر = متوسط
# بیشتر از 1.5 برابر = زیاد

LOW_VOLUME_RATIO = 0.75
HIGH_VOLUME_RATIO = 1.50

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d",
}

TELEGRAM_LIMIT = 3500

USERS_FILE = "users.json"


# =====================================================
# SESSION
# =====================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-RSI-Scanner/3.0"
})


# =====================================================
# USERS DATABASE
# =====================================================

def load_users():

    if not os.path.exists(USERS_FILE):

        return {
            "users": {},
            "last_update_id": 0
        }

    try:

        with open(
            USERS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if "users" not in data:
            data["users"] = {}

        if "last_update_id" not in data:
            data["last_update_id"] = 0

        return data

    except Exception as error:

        print(
            f"Could not read users.json: {error}"
        )

        return {
            "users": {},
            "last_update_id": 0
        }


def save_users(data):

    with open(
        USERS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


# =====================================================
# TELEGRAM API
# =====================================================

def telegram_api(
    method,
    data=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    response = session.post(
        url,
        data=data or {},
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError(
            f"Telegram API error: {result}"
        )

    return result


# =====================================================
# TELEGRAM MESSAGE
# =====================================================

def send_message(
    chat_id,
    message
):

    try:

        telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": message
            }
        )

        return True

    except Exception as error:

        print(
            f"Telegram send error "
            f"for {chat_id}: {error}"
        )

        return False


def send_long_message(
    chat_id,
    message
):

    if len(message) <= TELEGRAM_LIMIT:

        return send_message(
            chat_id,
            message
        )

    parts = []
    current = ""

    for line in message.splitlines():

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

    success = True

    total = len(parts)

    for number, part in enumerate(
        parts,
        start=1
    ):

        result = send_message(
            chat_id,
            f"📄 Part {number}/{total}\n\n"
            + part
        )

        if not result:
            success = False

        time.sleep(0.3)

    return success


# =====================================================
# TELEGRAM USERS
# =====================================================

def process_updates(database):

    offset = (
        database.get(
            "last_update_id",
            0
        )
        + 1
    )

    try:

        result = telegram_api(
            "getUpdates",
            {
                "offset": offset,
                "limit": 100,
                "timeout": 1,
                "allowed_updates": json.dumps(
                    ["message"]
                )
            }
        )

    except Exception as error:

        print(
            f"Telegram update error: {error}"
        )

        return database

    updates = result.get(
        "result",
        []
    )

    print(
        f"Telegram updates: {len(updates)}"
    )

    for update in updates:

        update_id = update.get(
            "update_id"
        )

        if update_id is not None:

            database["last_update_id"] = max(
                database.get(
                    "last_update_id",
                    0
                ),
                update_id
            )

        message = update.get(
            "message"
        )

        if not message:
            continue

        chat = message.get(
            "chat"
        )

        if not chat:
            continue

        chat_id = str(
            chat.get("id")
        )

        text = message.get(
            "text",
            ""
        ).strip().lower()

        if not chat_id:
            continue

        # =========================================
        # START
        # =========================================

        if text.startswith("/start"):

            database["users"][chat_id] = {
                "active": True,
                "username": chat.get(
                    "username",
                    ""
                ),
                "first_name": chat.get(
                    "first_name",
                    ""
                )
            }

            send_message(
                chat_id,
                "🤖 ربات RSI فعال شد.\n\n"
                "از این به بعد سیگنال‌های RSI "
                "برای شما ارسال می‌شود.\n\n"
                "🔴 RSI >= 70 → OVERBOUGHT\n"
                "🟢 RSI <= 30 → OVERSOLD\n\n"
                "📊 حجم معامله نیز نمایش داده می‌شود.\n"
                "📈 لینک TradingView نیز ارسال می‌شود.\n\n"
                "برای توقف:\n"
                "/stop\n\n"
                "برای مشاهده وضعیت:\n"
                "/status"
            )

            print(
                f"User STARTED: {chat_id}"
            )

        # =========================================
        # STOP
        # =========================================

        elif text.startswith("/stop"):

            if chat_id in database["users"]:

                database["users"][
                    chat_id
                ]["active"] = False

            send_message(
                chat_id,
                "⛔ دریافت سیگنال‌ها متوقف شد.\n\n"
                "برای فعال‌سازی دوباره:\n"
                "/start"
            )

            print(
                f"User STOPPED: {chat_id}"
            )

        # =========================================
        # STATUS
        # =========================================

        elif text.startswith("/status"):

            user = database["users"].get(
                chat_id
            )

            if user and user.get(
                "active",
                False
            ):

                send_message(
                    chat_id,
                    "🟢 وضعیت: فعال\n\n"
                    "سیگنال‌های RSI برای شما "
                    "ارسال می‌شود."
                )

            else:

                send_message(
                    chat_id,
                    "🔴 وضعیت: غیرفعال\n\n"
                    "برای فعال‌سازی:\n"
                    "/start"
                )

    save_users(database)

    return database


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

    rs = (
        average_gain
        / average_loss
    )

    return (
        100
        - (
            100
            / (1 + rs)
        )
    )


# =====================================================
# TOP 100 COINS
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

    excluded = (
        "UPUSDT",
        "DOWNUSDT",
        "BULLUSDT",
        "BEARUSDT",
    )

    for item in data:

        symbol = item.get(
            "symbol",
            ""
        )

        if not symbol.endswith(
            "USDT"
        ):
            continue

        if symbol.endswith(
            excluded
        ):
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

def get_candle_data(
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

    if len(data) < (
        RSI_PERIOD
        + VOLUME_LOOKBACK
        + 2
    ):
        return None

    # آخرین کندل برای وضعیت لحظه‌ای
    current_candle = data[-1]

    # کندل‌های بسته‌شده برای RSI
    closed_data = data[:-1]

    closes = []

    for candle in closed_data:

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

    rsi = calculate_rsi(
        closes,
        RSI_PERIOD
    )

    if rsi is None:
        return None

    try:

        current_volume = float(
            current_candle[5]
        )

        previous_volumes = [
            float(candle[5])
            for candle
            in data[
                -VOLUME_LOOKBACK - 1:
                -1
            ]
        ]

    except (
        IndexError,
        ValueError,
        TypeError
    ):

        return None

    if not previous_volumes:
        return None

    average_volume = (
        sum(previous_volumes)
        / len(previous_volumes)
    )

    if average_volume <= 0:
        return None

    volume_ratio = (
        current_volume
        / average_volume
    )

    return {
        "rsi": rsi,
        "current_volume": current_volume,
        "average_volume": average_volume,
        "volume_ratio": volume_ratio
    }


# =====================================================
# VOLUME STATUS
# =====================================================

def get_volume_status(
    ratio
):

    if ratio < LOW_VOLUME_RATIO:

        return "🟢 کم"

    if ratio < HIGH_VOLUME_RATIO:

        return "🟡 متوسط"

    return "🔥 زیاد"


# =====================================================
# TRADINGVIEW LINK
# =====================================================

def tradingview_link(
    symbol,
    timeframe
):

    tv_timeframes = {
        "5m": "5",
        "15m": "15",
        "1h": "60",
        "4h": "240",
        "1D": "D"
    }

    tv_tf = tv_timeframes.get(
        timeframe,
        "5"
    )

    tv_symbol = (
        "BINANCE:"
        + symbol
    )

    return (
        "https://www.tradingview.com/"
        "chart/?symbol="
        + tv_symbol
        + "&interval="
        + tv_tf
    )


# =====================================================
# SCAN MARKET
# =====================================================

def scan_market():

    symbols = get_top_coins()

    print(
        f"Scanning TOP {len(symbols)} coins..."
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

        for timeframe, interval in (
            TIMEFRAMES.items()
        ):

            try:

                result = get_candle_data(
                    symbol,
                    interval
                )

                if result is None:
                    continue

                rsi = result["rsi"]

                # =================================
                # RSI CONDITION
                # =================================

                if rsi >= 70:

                    status = "🔴 OVERBOUGHT"

                elif rsi <= 30:

                    status = "🟢 OVERSOLD"

                else:

                    continue

                volume_ratio = (
                    result["volume_ratio"]
                )

                volume_status = (
                    get_volume_status(
                        volume_ratio
                    )
                )

                signals.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "rsi": rsi,
                    "status": status,
                    "volume_ratio": volume_ratio,
                    "volume_status": volume_status,
                    "tradingview": tradingview_link(
                        symbol,
                        timeframe
                    )
                })

            except Exception as error:

                print(
                    f"Error {symbol} "
                    f"{timeframe}: {error}"
                )

            time.sleep(0.05)

    return signals


# =====================================================
# CREATE MESSAGE
# =====================================================

def make_message(
    signals
):

    lines = [
        "🚨 BINANCE RSI SCANNER",
        "",
        f"🔎 TOP {TOP_COINS} USDT COINS",
        "📊 RSI(14)",
        "📈 Current Candle Volume",
        ""
    ]

    if not signals:

        lines.append(
            "✅ هیچ ارزی با RSI >= 70 "
            "یا RSI <= 30 پیدا نشد."
        )

        return "\n".join(lines)

    # ===============================================
    # OVERBOUGHT
    # ===============================================

    overbought = [
        x
        for x in signals
        if x["status"]
        == "🔴 OVERBOUGHT"
    ]

    if overbought:

        lines.append(
            "🔴 OVERBOUGHT — RSI >= 70"
        )

        lines.append("")

        for item in overbought:

            lines.append(
                f"🔴 {item['symbol']}"
            )

            lines.append(
                f"⏱ تایم‌فریم: "
                f"{item['timeframe']}"
            )

            lines.append(
                f"📊 RSI: "
                f"{item['rsi']:.2f}"
            )

            lines.append(
                f"📈 حجم فعلی: "
                f"{item['volume_status']}"
            )

            lines.append(
                f"📊 نسبت حجم: "
                f"{item['volume_ratio']:.2f}x "
                f"میانگین 20 کندل"
            )

            lines.append(
                f"📈 TradingView:\n"
                f"{item['tradingview']}"
            )

            lines.append(
                "────────────────"
            )

    # ===============================================
    # OVERSOLD
    # ===============================================

    oversold = [
        x
        for x in signals
        if x["status"]
        == "🟢 OVERSOLD"
    ]

    if oversold:

        lines.append(
            "🟢 OVERSOLD — RSI <= 30"
        )

        lines.append("")

        for item in oversold:

            lines.append(
                f"🟢 {item['symbol']}"
            )

            lines.append(
                f"⏱ تایم‌فریم: "
                f"{item['timeframe']}"
            )

            lines.append(
                f"📊 RSI: "
                f"{item['rsi']:.2f}"
            )

            lines.append(
                f"📈 حجم فعلی: "
                f"{item['volume_status']}"
            )

            lines.append(
                f"📊 نسبت حجم: "
                f"{item['volume_ratio']:.2f}x "
                f"میانگین 20 کندل"
            )

            lines.append(
                f"📈 TradingView:\n"
                f"{item['tradingview']}"
            )

            lines.append(
                "────────────────"
            )

    return "\n".join(lines)


# =====================================================
# SEND TO ALL USERS
# =====================================================

def send_to_all_users(
    database,
    message
):

    users = database.get(
        "users",
        {}
    )

    active_users = [
        chat_id
        for chat_id, user
        in users.items()
        if user.get(
            "active",
            False
        )
    ]

    print(
        f"Active users: "
        f"{len(active_users)}"
    )

    if not active_users:

        print(
            "No active users."
        )

        return

    for chat_id in active_users:

        print(
            f"Sending to {chat_id}"
        )

        success = send_long_message(
            chat_id,
            message
        )

        if not success:

            print(
                f"Failed to send "
                f"to {chat_id}"
            )

        time.sleep(0.2)


# =====================================================
# MAIN
# =====================================================

def main():

    print(
        "======================================"
    )

    print(
        "BINANCE MULTI-USER RSI BOT"
    )

    print(
        "TOP 100 + VOLUME + TRADINGVIEW"
    )

    print(
        "======================================"
    )

    # Load users
    database = load_users()

    # دریافت /start /stop
    database = process_updates(
        database
    )

    # Scan Binance
    signals = scan_market()

    print(
        f"Signals found: "
        f"{len(signals)}"
    )

    # Create message
    message = make_message(
        signals
    )

    # Send to all active users
    send_to_all_users(
        database,
        message
    )

    # Save database
    save_users(database)

    print(
        "======================================"
    )

    print(
        "SCAN COMPLETED"
    )

    print(
        "======================================"
    )


if __name__ == "__main__":

    main()
