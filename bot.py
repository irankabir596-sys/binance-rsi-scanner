import os
import time
import json
import requests


# =====================================================
# CONFIG
# =====================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

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

USERS_FILE = "users.json"


# =====================================================
# SESSION
# =====================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-RSI-Scanner/2.0"
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

    except Exception:

        print("users.json is invalid. Creating new database.")

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

def telegram_api(method, data=None):

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
# DISABLE WEBHOOK
# =====================================================

def prepare_telegram():

    try:

        telegram_api(
            "deleteWebhook",
            {
                "drop_pending_updates": False
            }
        )

        print("Telegram webhook: disabled")

    except Exception as error:

        print(
            f"Webhook preparation warning: {error}"
        )


# =====================================================
# RECEIVE TELEGRAM UPDATES
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
        f"Telegram updates received: {len(updates)}"
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

        text = (
            message.get(
                "text",
                ""
            )
            .strip()
            .lower()
        )

        if not chat_id:
            continue

        # =========================================
        # /start
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
                "برای توقف دریافت پیام:\n"
                "/stop"
            )

            print(
                f"User STARTED: {chat_id}"
            )

        # =========================================
        # /stop
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
        # /status
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
# SEND MESSAGE
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


# =====================================================
# SEND LONG MESSAGE
# =====================================================

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
# TOP 200
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
# GET RSI
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

    # آخرین کندل کامل نیست
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
# SCAN MARKET
# =====================================================

def scan_market():

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

        for timeframe, interval in (
            TIMEFRAMES.items()
        ):

            try:

                rsi = get_rsi(
                    symbol,
                    interval
                )

                if rsi is None:
                    continue

                if rsi >= 70:

                    signals.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "rsi": rsi,
                        "status": "OVERBOUGHT"
                    })

                elif rsi <= 30:

                    signals.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "rsi": rsi,
                        "status": "OVERSOLD"
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

def make_message(signals):

    lines = [
        "🚨 BINANCE RSI SCANNER",
        "",
        f"🔎 Top {TOP_COINS} USDT coins",
        "📊 RSI(14)",
        ""
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
        for chat_id, user in users.items()
        if user.get(
            "active",
            False
        )
    ]

    print(
        f"Active users: {len(active_users)}"
    )

    if not active_users:
        print(
            "No active users."
        )
        return

    for chat_id in active_users:

        print(
            f"Sending signal to {chat_id}"
        )

        success = send_long_message(
            chat_id,
            message
        )

        if not success:

            print(
                f"Could not send to {chat_id}"
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
        "======================================"
    )

    # Load users
    database = load_users()

    # Telegram preparation
    prepare_telegram()

    # دریافت /start /stop
    database = process_updates(
        database
    )

    # Scan Binance
    signals = scan_market()

    print(
        f"Signals found: {len(signals)}"
    )

    # Create message
    message = make_message(
        signals
    )

    # Send to active users
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
