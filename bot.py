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

LOW_VOLUME_RATIO = 0.75
HIGH_VOLUME_RATIO = 1.50

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d",
}

TIMEFRAME_ORDER = {
    "5m": 0,
    "15m": 1,
    "1h": 2,
    "4h": 3,
    "1D": 4,
}

TELEGRAM_LIMIT = 3500

USERS_FILE = "users.json"
STATE_FILE = "state.json"


# =====================================================
# SESSION
# =====================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Binance-RSI-Scanner/4.0"
})


# =====================================================
# JSON DATABASE
# =====================================================

def load_json(
    filename,
    default
):

    if not os.path.exists(filename):
        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as error:

        print(
            f"Could not read {filename}: {error}"
        )

        return default


def save_json(
    filename,
    data
):

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


def load_users():

    return load_json(
        USERS_FILE,
        {
            "users": {},
            "last_update_id": 0
        }
    )


def save_users(database):

    save_json(
        USERS_FILE,
        database
    )


def load_state():

    return load_json(
        STATE_FILE,
        {}
    )


def save_state(state):

    save_json(
        STATE_FILE,
        state
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
                "سیگنال فقط زمانی ارسال می‌شود "
                "که RSI وارد محدوده شود.\n\n"
                "🔴 ورود RSI به بالای 70\n"
                "🟢 ورود RSI به زیر 30\n\n"
                "📈 حجم فعلی نیز نمایش داده می‌شود.\n"
                "📊 لینک TradingView نیز ارسال می‌شود.\n\n"
                "برای توقف:\n"
                "/stop"
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
                    "سیگنال‌های ورود RSI برای شما "
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
# TOP 100
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
# CANDLE DATA
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

    minimum = (
        RSI_PERIOD
        + VOLUME_LOOKBACK
        + 2
    )

    if len(data) < minimum:
        return None

    # =============================================
    # RSI از کندل‌های بسته شده
    # =============================================

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

    # =============================================
    # حجم کندل در حال تشکیل
    # =============================================

    current_candle = data[-1]

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
# TRADINGVIEW
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

    return (
        "https://www.tradingview.com/"
        "chart/?symbol=BINANCE:"
        + symbol
        + "&interval="
        + tv_tf
    )


# =====================================================
# CHECK RSI ENTRY
# =====================================================

def check_rsi_entry(
    state,
    symbol,
    timeframe,
    current_rsi
):

    key = (
        symbol
        + "_"
        + timeframe
    )

    previous_rsi = state.get(
        key
    )

    # =============================================
    # اولین بار که این نماد بررسی می‌شود
    # فقط وضعیت را ذخیره می‌کنیم.
    # =============================================

    if previous_rsi is None:

        state[key] = current_rsi

        return None

    signal = None

    # =============================================
    # ورود به OVERBOUGHT
    # از زیر 70 به بالای 70
    # =============================================

    if (
        previous_rsi < 70
        and current_rsi >= 70
    ):

        signal = "🔴 OVERBOUGHT"

    # =============================================
    # ورود به OVERSOLD
    # از بالای 30 به زیر 30
    # =============================================

    elif (
        previous_rsi > 30
        and current_rsi <= 30
    ):

        signal = "🟢 OVERSOLD"

    # =============================================
    # Update state
    # =============================================

    state[key] = current_rsi

    return signal


# =====================================================
# SCAN MARKET
# =====================================================

def scan_market(
    state
):

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

                signal = check_rsi_entry(
                    state,
                    symbol,
                    timeframe,
                    rsi
                )

                # فقط ورود جدید به محدوده
                if signal is None:
                    continue

                volume_ratio = (
                    result["volume_ratio"]
                )

                signals.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "rsi": rsi,
                    "status": signal,
                    "volume_ratio": volume_ratio,
                    "volume_status":
                        get_volume_status(
                            volume_ratio
                        ),
                    "tradingview":
                        tradingview_link(
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
# SORT SIGNALS
# =====================================================

def sort_signals(
    signals
):

    overbought = [
        item
        for item in signals
        if item["status"]
        == "🔴 OVERBOUGHT"
    ]

    oversold = [
        item
        for item in signals
        if item["status"]
        == "🟢 OVERSOLD"
    ]

    # Overbought:
    # RSI بالاتر اول
    # سپس تایم‌فریم

    overbought.sort(
        key=lambda x: (
            -x["rsi"],
            TIMEFRAME_ORDER[
                x["timeframe"]
            ]
        )
    )

    # Oversold:
    # RSI پایین‌تر اول
    # سپس تایم‌فریم

    oversold.sort(
        key=lambda x: (
            x["rsi"],
            TIMEFRAME_ORDER[
                x["timeframe"]
            ]
        )
    )

    return (
        overbought
        + oversold
    )


# =====================================================
# CREATE MESSAGE
# =====================================================

def make_message(
    signals
):

    signals = sort_signals(
        signals
    )

    lines = [
        "🚨 BINANCE RSI SCANNER",
        "",
        f"🔎 TOP {TOP_COINS} USDT COINS",
        "📊 RSI(14)",
        "⚡ NEW RSI ENTRY SIGNALS",
        ""
    ]

    if not signals:

        lines.append(
            "ℹ️ در این اسکن RSI جدیدی "
            "وارد محدوده 70/30 نشد."
        )

        return "\n".join(lines)

    overbought = [
        x
        for x in signals
        if x["status"]
        == "🔴 OVERBOUGHT"
    ]

    oversold = [
        x
        for x in signals
        if x["status"]
        == "🟢 OVERSOLD"
    ]

    # =============================================
    # OVERBOUGHT
    # =============================================

    if overbought:

        lines.append(
            "🔴 OVERBOUGHT"
        )

        lines.append(
            "ورود RSI به بالای 70"
        )

        lines.append("")

        for item in overbought:

            lines.append(
                f"🔴 {item['symbol']}"
            )

            lines.append(
                f"⏱ {item['timeframe']}"
            )

            lines.append(
                f"📊 RSI: "
                f"{item['rsi']:.2f}"
            )

            lines.append(
                f"📈 حجم: "
                f"{item['volume_status']}"
            )

            lines.append(
                f"📊 حجم نسبت به میانگین: "
                f"{item['volume_ratio']:.2f}x"
            )

            lines.append(
                "📈 TradingView:"
            )

            lines.append(
                item["tradingview"]
            )

            lines.append(
                "────────────────"
            )

    # =============================================
    # OVERSOLD
    # =============================================

    if oversold:

        lines.append(
            "🟢 OVERSOLD"
        )

        lines.append(
            "ورود RSI به زیر 30"
        )

        lines.append("")

        for item in oversold:

            lines.append(
                f"🟢 {item['symbol']}"
            )

            lines.append(
                f"⏱ {item['timeframe']}"
            )

            lines.append(
                f"📊 RSI: "
                f"{item['rsi']:.2f}"
            )

            lines.append(
                f"📈 حجم: "
                f"{item['volume_status']}"
            )

            lines.append(
                f"📊 حجم نسبت به میانگین: "
                f"{item['volume_ratio']:.2f}x"
            )

            lines.append(
                "📈 TradingView:"
            )

            lines.append(
                item["tradingview"]
            )

            lines.append(
                "────────────────"
            )

    return "\n".join(lines)


# =====================================================
# SEND TO USERS
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
        "BINANCE MULTI USER RSI BOT"
    )

    print(
        "TOP 100"
    )

    print(
        "RSI ENTRY + VOLUME + TRADINGVIEW"
    )

    print(
        "======================================"
    )

    # ---------------------------------------------
    # Users
    # ---------------------------------------------

    database = load_users()

    database = process_updates(
        database
    )

    # ---------------------------------------------
    # RSI state
    # ---------------------------------------------

    state = load_state()

    # ---------------------------------------------
    # Scan
    # ---------------------------------------------

    signals = scan_market(
        state
    )

    print(
        f"NEW RSI ENTRY SIGNALS: "
        f"{len(signals)}"
    )

    # ---------------------------------------------
    # Save RSI state
    # ---------------------------------------------

    save_state(
        state
    )

    # ---------------------------------------------
    # Message
    # ---------------------------------------------

    message = make_message(
        signals
    )

    # ---------------------------------------------
    # Send
    # ---------------------------------------------

    send_to_all_users(
        database,
        message
    )

    # ---------------------------------------------
    # Save users
    # ---------------------------------------------

    save_users(
        database
    )

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
