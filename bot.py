import os
import json
import time
import requests
from datetime import datetime, timezone


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

BINANCE = "https://data-api.binance.vision"

POLY_GAMMA = "https://gamma-api.polymarket.com"
POLY_CLOB = "https://clob.polymarket.com"

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
    "1D": "1d"
}

TIMEFRAME_ORDER = {
    "5m": 0,
    "15m": 1,
    "1h": 2,
    "4h": 3,
    "1D": 4
}

TRADINGVIEW_INTERVAL = {
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1D": "D"
}

USERS_FILE = "users.json"
STATE_FILE = "state.json"

TELEGRAM_LIMIT = 3500
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 RSI-Polymarket-Bot/1.0"
}

session = requests.Session()
session.headers.update(HEADERS)


# =========================================================
# JSON
# =========================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"JSON LOAD ERROR {filename}: {e}"
        )

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            f"JSON SAVE ERROR {filename}: {e}"
        )


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(method, params=None):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        response = session.post(
            url,
            data=params or {},
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"TELEGRAM ERROR {method}: {e}"
        )

        return None


def send_message(chat_id, text):

    return telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_long_message(chat_id, text):

    while len(text) > TELEGRAM_LIMIT:

        cut = text.rfind(
            "\n",
            0,
            TELEGRAM_LIMIT
        )

        if cut <= 0:
            cut = TELEGRAM_LIMIT

        part = text[:cut]

        send_message(
            chat_id,
            part
        )

        text = text[cut:].lstrip()

    if text:

        send_message(
            chat_id,
            text
        )


# =========================================================
# TELEGRAM USERS
# =========================================================

def process_updates(users):

    try:

        result = telegram_api(
            "getUpdates"
        )

        if not result:
            return users

        if not result.get("ok"):
            return users

        updates = result.get(
            "result",
            []
        )

        for update in updates:

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

            if text.startswith("/start"):

                users[chat_id] = {
                    "active": True
                }

                send_message(
                    chat_id,
                    "✅ ربات فعال شد.\n\n"
                    "سیگنال‌های جدید RSI "
                    "برای شما ارسال می‌شوند."
                )

            elif text.startswith("/stop"):

                users[chat_id] = {
                    "active": False
                }

                send_message(
                    chat_id,
                    "⛔ دریافت سیگنال متوقف شد."
                )

            elif text.startswith("/status"):

                active = users.get(
                    chat_id,
                    {}
                ).get(
                    "active",
                    False
                )

                status = (
                    "فعال 🟢"
                    if active
                    else
                    "غیرفعال 🔴"
                )

                send_message(
                    chat_id,
                    f"وضعیت ربات: {status}"
                )

        return users

    except Exception as e:

        print(
            f"UPDATE ERROR: {e}"
        )

        return users


def get_active_users(users):

    return [
        chat_id
        for chat_id, info in users.items()
        if info.get("active") is True
    ]


# =========================================================
# BINANCE TOP 100
# =========================================================

def get_top_coins():

    url = (
        f"{BINANCE}/api/v3/ticker/24hr"
    )

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        coins = []

        for item in data:

            symbol = item.get(
                "symbol",
                ""
            )

            if not symbol.endswith(
                "USDT"
            ):
                continue

            # Remove leveraged tokens
            if any(
                x in symbol
                for x in [
                    "UPUSDT",
                    "DOWNUSDT",
                    "BULLUSDT",
                    "BEARUSDT"
                ]
            ):
                continue

            try:

                volume = float(
                    item.get(
                        "quoteVolume",
                        0
                    )
                )

            except Exception:

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

        top = [
            symbol
            for symbol, _ in
            coins[:TOP_COINS]
        ]

        print(
            f"Top Binance coins: {len(top)}"
        )

        return top

    except Exception as e:

        print(
            f"BINANCE TOP ERROR: {e}"
        )

        return []


# =========================================================
# RSI
# =========================================================

def calculate_rsi(
    prices,
    period=14
):

    if len(prices) < period + 1:
        return None

    changes = []

    for i in range(
        1,
        len(prices)
    ):

        changes.append(
            prices[i] - prices[i - 1]
        )

    gains = [
        max(x, 0)
        for x in changes
    ]

    losses = [
        max(-x, 0)
        for x in changes
    ]

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(changes)
    ):

        avg_gain = (
            (
                avg_gain * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    rs = avg_gain / avg_loss

    return (
        100
        -
        (
            100 / (1 + rs)
        )
    )


# =========================================================
# BINANCE CANDLE DATA
# =========================================================

def get_candle_data(
    symbol,
    timeframe
):

    interval = TIMEFRAMES[
        timeframe
    ]

    url = (
        f"{BINANCE}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": 100
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        if len(data) < 30:
            return None

        # Last candle = current/forming
        # RSI = closed candles
        closed = data[:-1]

        closes = [
            float(candle[4])
            for candle in closed
        ]

        rsi_value = calculate_rsi(
            closes,
            RSI_PERIOD
        )

        if rsi_value is None:
            return None

        current_volume = float(
            data[-1][5]
        )

        previous_volumes = [
            float(candle[5])
            for candle in
            closed[-VOLUME_LOOKBACK:]
        ]

        if not previous_volumes:
            return None

        average_volume = (
            sum(previous_volumes)
            / len(previous_volumes)
        )

        if average_volume <= 0:

            volume_ratio = 0

        else:

            volume_ratio = (
                current_volume
                / average_volume
            )

        # -------------------------
        # Trend
        # -------------------------

        recent = closes[-5:]
        previous = closes[-20:-5]

        if not previous:

            trend = "خنثی"

        else:

            recent_avg = (
                sum(recent)
                / len(recent)
            )

            previous_avg = (
                sum(previous)
                / len(previous)
            )

            if previous_avg == 0:

                trend = "خنثی"

            else:

                change = (
                    (
                        recent_avg
                        -
                        previous_avg
                    )
                    /
                    previous_avg
                ) * 100

                if change > 0.20:

                    trend = "صعودی"

                elif change < -0.20:

                    trend = "نزولی"

                else:

                    trend = "خنثی"

        return {
            "rsi": rsi_value,
            "volume_ratio": volume_ratio,
            "trend": trend
        }

    except Exception as e:

        print(
            f"CANDLE ERROR "
            f"{symbol} {timeframe}: {e}"
        )

        return None


# =========================================================
# VOLUME
# =========================================================

def get_volume_status(
    ratio
):

    if ratio >= HIGH_VOLUME_RATIO:
        return "🔥 زیاد"

    if ratio >= LOW_VOLUME_RATIO:
        return "🟡 متوسط"

    return "🟢 کم"


# =========================================================
# POLYMARKET HELPERS
# =========================================================

def parse_json_field(
    value,
    default=None
):

    if value is None:
        return default

    if isinstance(
        value,
        list
    ):
        return value

    if isinstance(
        value,
        dict
    ):
        return value

    if isinstance(
        value,
        str
    ):

        try:

            return json.loads(
                value
            )

        except Exception:

            return default

    return default


def parse_date(value):

    if not value:
        return None

    try:

        text = str(value)

        if text.endswith("Z"):

            text = (
                text[:-1]
                + "+00:00"
            )

        dt = datetime.fromisoformat(
            text
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:

        return None


# =========================================================
# DETECT CRYPTO + TIMEFRAME FROM MARKET
# =========================================================

def detect_asset(text):

    text = text.lower()

    # Important:
    # check longer names first

    assets = {
        "BTC": [
            "bitcoin",
            "btc"
        ],

        "ETH": [
            "ethereum",
            "eth"
        ],

        "SOL": [
            "solana",
            "sol"
        ],

        "XRP": [
            "xrp",
            "ripple"
        ],

        "DOGE": [
            "dogecoin",
            "doge"
        ],

        "BNB": [
            "bnb",
            "binance coin"
        ],

        "ADA": [
            "cardano",
            "ada"
        ],

        "AVAX": [
            "avalanche",
            "avax"
        ],

        "LINK": [
            "chainlink",
            "link"
        ],

        "SUI": [
            "sui"
        ],

        "DOT": [
            "polkadot",
            "dot"
        ],

        "TRX": [
            "tron",
            "trx"
        ],

        "LTC": [
            "litecoin",
            "ltc"
        ],

        "BCH": [
            "bitcoin cash",
            "bch"
        ],

        "SHIB": [
            "shiba",
            "shib"
        ],

        "UNI": [
            "uniswap",
            "uni"
        ]
    }

    # Prefer exact ticker boundaries
    for asset, names in assets.items():

        for name in names:

            if name in text:

                return asset

    return None


def detect_timeframe(text):

    text = text.lower()

    # 15m before 5m
    if (
        "15 minute" in text
        or "15 minutes" in text
        or "15-min" in text
        or "15m" in text
    ):

        return "15m"

    if (
        "5 minute" in text
        or "5 minutes" in text
        or "5-min" in text
        or "5m" in text
    ):

        return "5m"

    if (
        "4 hour" in text
        or "4 hours" in text
        or "4-hour" in text
        or "4h" in text
    ):

        return "4h"

    if (
        "hourly" in text
        or "1 hour" in text
        or "1-hour" in text
        or "1h" in text
    ):

        return "1h"

    if (
        "daily" in text
        or "1 day" in text
        or "1-day" in text
        or "1d" in text
    ):

        return "1D"

    return None


def is_up_down_market(
    text
):

    text = text.lower()

    patterns = [
        "up or down",
        "up/down",
        "updown"
    ]

    return any(
        p in text
        for p in patterns
    )


# =========================================================
# DOWNLOAD ACTIVE POLYMARKET MARKETS
# =========================================================

def get_active_polymarket_markets():

    all_markets = []

    offset = 0

    page_size = 100

    max_pages = 10

    for _ in range(max_pages):

        params = {
            "active": "true",
            "closed": "false",
            "limit": page_size,
            "offset": offset,
            "order": "volume24hr",
            "ascending": "false"
        }

        try:

            response = session.get(
                f"{POLY_GAMMA}/markets",
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(
                data,
                list
            ):

                break

            if not data:
                break

            all_markets.extend(
                data
            )

            print(
                f"Polymarket page "
                f"{_ + 1}: "
                f"{len(data)} markets"
            )

            if len(data) < page_size:
                break

            offset += page_size

        except Exception as e:

            print(
                f"POLYMARKET ERROR: {e}"
            )

            break

    print(
        f"Total Polymarket markets: "
        f"{len(all_markets)}"
    )

    return all_markets


# =========================================================
# BUILD POLYMARKET INDEX
# =========================================================

def build_polymarket_index(
    markets
):

    index = {}

    now = datetime.now(
        timezone.utc
    )

    for market in markets:

        if not isinstance(
            market,
            dict
        ):
            continue

        if market.get(
            "active"
        ) is False:
            continue

        if market.get(
            "closed"
        ) is True:
            continue

        question = str(
            market.get(
                "question",
                ""
            )
        )

        description = str(
            market.get(
                "description",
                ""
            )
        )

        slug = str(
            market.get(
                "slug",
                ""
            )
        )

        group_title = str(
            market.get(
                "groupItemTitle",
                ""
            )
        )

        combined = (
            question
            + " "
            + description
            + " "
            + slug
            + " "
            + group_title
        ).lower()

        # Only crypto Up/Down markets
        if not is_up_down_market(
            combined
        ):
            continue

        asset = detect_asset(
            combined
        )

        timeframe = detect_timeframe(
            combined
        )

        if not asset or not timeframe:
            continue

        end_date = parse_date(
            market.get(
                "endDate"
            )
        )

        if end_date:

            if end_date <= now:
                continue

        key = (
            asset,
            timeframe
        )

        index.setdefault(
            key,
            []
        ).append(
            market
        )

    # Sort each group:
    # currently running first,
    # then highest 24h volume.
    for key in index:

        index[key].sort(
            key=lambda m: (
                -(float(
                    m.get(
                        "volume24hr",
                        0
                    ) or 0
                )),
            )
        )

    print(
        f"Detected Polymarket "
        f"crypto groups: "
        f"{len(index)}"
    )

    for key in sorted(index):

        print(
            f"POLY: {key} -> "
            f"{len(index[key])}"
        )

    return index


# =========================================================
# CHOOSE CURRENT MARKET
# =========================================================

def choose_market(
    candidates
):

    now = datetime.now(
        timezone.utc
    )

    current = []

    future = []

    for market in candidates:

        start = parse_date(
            market.get(
                "startDate"
            )
        )

        end = parse_date(
            market.get(
                "endDate"
            )
        )

        if end and end <= now:
            continue

        if start and start <= now:

            current.append(
                market
            )

        elif start:

            seconds = (
                start - now
            ).total_seconds()

            # Future market only if
            # it starts within 5 minutes.
            if 0 <= seconds <= 300:

                future.append(
                    market
                )

    if current:

        current.sort(
            key=lambda m: float(
                m.get(
                    "volume24hr",
                    0
                ) or 0
            ),
            reverse=True
        )

        return current[0]

    if future:

        future.sort(
            key=lambda m: (
                parse_date(
                    m.get(
                        "startDate"
                    )
                )
                or now
            )
        )

        return future[0]

    return None


# =========================================================
# LIVE POLYMARKET PRICE
# =========================================================

def get_polymarket_price(
    market
):

    outcomes = parse_json_field(
        market.get(
            "outcomes"
        ),
        []
    )

    prices = parse_json_field(
        market.get(
            "outcomePrices"
        ),
        []
    )

    token_ids = parse_json_field(
        market.get(
            "clobTokenIds"
        ),
        []
    )

    if not outcomes:
        return None

    # Find UP / YES
    up_index = None

    for i, outcome in enumerate(
        outcomes
    ):

        value = str(
            outcome
        ).strip().lower()

        if value in [
            "up",
            "yes"
        ]:

            up_index = i
            break

    if up_index is None:

        # For genuine binary Up/Down
        # first outcome is usually UP.
        up_index = 0

    up_price = None

    # -------------------------
    # CLOB midpoint
    # -------------------------

    if (
        token_ids
        and
        up_index < len(token_ids)
    ):

        token_id = str(
            token_ids[up_index]
        )

        try:

            response = session.get(
                f"{POLY_CLOB}/midpoint",
                params={
                    "token_id": token_id
                },
                timeout=REQUEST_TIMEOUT
            )

            if response.ok:

                data = response.json()

                if data.get(
                    "mid_price"
                ) is not None:

                    up_price = float(
                        data[
                            "mid_price"
                        ]
                    )

        except Exception as e:

            print(
                f"CLOB PRICE ERROR: {e}"
            )

    # -------------------------
    # Gamma fallback
    # -------------------------

    if up_price is None:

        if (
            prices
            and
            up_index < len(prices)
        ):

            try:

                up_price = float(
                    prices[up_index]
                )

            except Exception:

                up_price = None

    if up_price is None:
        return None

    up_price = max(
        0,
        min(
            1,
            up_price
        )
    )

    down_price = (
        1 - up_price
    )

    return {
        "up": up_price,
        "down": down_price,
        "question": market.get(
            "question",
            ""
        ),
        "slug": market.get(
            "slug",
            ""
        ),
        "endDate": market.get(
            "endDate",
            ""
        )
    }


# =========================================================
# FIND MARKET FOR SYMBOL/TIMEFRAME
# =========================================================

def get_prediction_market(
    symbol,
    timeframe,
    polymarket_index
):

    if not symbol.endswith(
        "USDT"
    ):
        return None

    asset = symbol[
        :-4
    ]

    key = (
        asset,
        timeframe
    )

    candidates = polymarket_index.get(
        key,
        []
    )

    if not candidates:

        return None

    market = choose_market(
        candidates
    )

    if not market:
        return None

    return get_polymarket_price(
        market
    )


# =========================================================
# RSI ENTRY
# =========================================================

def check_rsi_entry(
    state,
    symbol,
    timeframe,
    current_rsi
):

    key = (
        f"{symbol}_"
        f"{timeframe}"
    )

    previous = state.get(
        key
    )

    # First scan:
    # save RSI, no alert.
    if previous is None:

        state[key] = current_rsi

        return False

    try:

        previous = float(
            previous
        )

    except Exception:

        state[key] = current_rsi

        return False

    entered_overbought = (
        previous < 70
        and
        current_rsi >= 70
    )

    entered_oversold = (
        previous > 30
        and
        current_rsi <= 30
    )

    state[key] = current_rsi

    return (
        entered_overbought
        or
        entered_oversold
    )


# =========================================================
# SIGNAL
# =========================================================

def calculate_signal(
    rsi,
    volume_ratio,
    trend,
    market
):

    bullish = 0
    bearish = 0

    # RSI
    if rsi <= 30:

        bullish += 2

    elif rsi >= 70:

        bearish += 2

    elif rsi < 45:

        bullish += 1

    elif rsi > 55:

        bearish += 1

    # Volume
    if volume_ratio >= 1.5:

        if rsi <= 45:
            bullish += 2

        elif rsi >= 55:
            bearish += 2

    elif volume_ratio >= 0.75:

        if rsi <= 45:
            bullish += 1

        elif rsi >= 55:
            bearish += 1

    # Trend
    if trend == "صعودی":

        bullish += 2

    elif trend == "نزولی":

        bearish += 2

    # Polymarket
    if market:

        up = market[
            "up"
        ]

        down = market[
            "down"
        ]

        if up >= 0.60:

            bullish += 3

        elif down >= 0.60:

            bearish += 3

    difference = (
        bullish
        - bearish
    )

    if difference >= 3:

        signal = (
            "🟢 تمایل صعودی قوی"
        )

    elif difference <= -3:

        signal = (
            "🔴 تمایل نزولی قوی"
        )

    elif difference > 0:

        signal = (
            "🟢 تمایل صعودی"
        )

    elif difference < 0:

        signal = (
            "🔴 تمایل نزولی"
        )

    else:

        signal = "⚪ خنثی"

    return signal


# =========================================================
# TRADINGVIEW
# =========================================================

def tradingview_link(
    symbol,
    timeframe
):

    interval = (
        TRADINGVIEW_INTERVAL[
            timeframe
        ]
    )

    return (
        "https://www.tradingview.com/chart/"
        f"?symbol=BINANCE:{symbol}"
        f"&interval={interval}"
    )


# =========================================================
# SCAN MARKET
# =========================================================

def scan_market(
    state,
    polymarket_index
):

    coins = get_top_coins()

    if not coins:
        return []

    signals = []

    for number, symbol in enumerate(
        coins,
        start=1
    ):

        print(
            f"[{number}/{len(coins)}] "
            f"{symbol}"
        )

        for timeframe in TIMEFRAMES:

            data = get_candle_data(
                symbol,
                timeframe
            )

            if not data:
                continue

            rsi = data[
                "rsi"
            ]

            # Only NEW entry
            entered = check_rsi_entry(
                state,
                symbol,
                timeframe,
                rsi
            )

            if not entered:
                continue

            print(
                f"NEW SIGNAL "
                f"{symbol} "
                f"{timeframe} "
                f"RSI={rsi:.2f}"
            )

            # Query local Polymarket index
            # only when RSI signal happens.
            market = get_prediction_market(
                symbol,
                timeframe,
                polymarket_index
            )

            signal = calculate_signal(
                rsi,
                data[
                    "volume_ratio"
                ],
                data[
                    "trend"
                ],
                market
            )

            signals.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "rsi": rsi,
                    "volume_ratio":
                        data[
                            "volume_ratio"
                        ],
                    "volume_status":
                        get_volume_status(
                            data[
                                "volume_ratio"
                            ]
                        ),
                    "trend":
                        data[
                            "trend"
                        ],
                    "market":
                        market,
                    "signal":
                        signal,
                    "tradingview":
                        tradingview_link(
                            symbol,
                            timeframe
                        )
                }
            )

    return signals


# =========================================================
# SORT
# =========================================================

def sort_signals(
    signals
):

    def key(item):

        rsi = item[
            "rsi"
        ]

        tf = TIMEFRAME_ORDER[
            item[
                "timeframe"
            ]
        ]

        # Overbought first
        if rsi >= 70:

            return (
                0,
                -rsi,
                tf
            )

        # Oversold second
        return (
            1,
            rsi,
            tf
        )

    return sorted(
        signals,
        key=key
    )


# =========================================================
# MESSAGE
# =========================================================

def make_signal_message(
    signal
):

    market = signal[
        "market"
    ]

    if market:

        up = (
            market["up"]
            * 100
        )

        down = (
            market["down"]
            * 100
        )

        market_line = (
            f"Market: UP "
            f"{up:.0f}% 🟢 | "
            f"DOWN "
            f"{down:.0f}% 🔴"
        )

    else:

        market_line = (
            "Market: N/A ⚪"
        )

    return (
        f"{signal['symbol']} | "
        f"{signal['timeframe']}\n\n"

        f"RSI: "
        f"{signal['rsi']:.1f}\n"

        f"Volume: "
        f"{signal['volume_ratio']:.2f}x "
        f"{signal['volume_status']}\n"

        f"Trend: "
        f"{signal['trend']}\n"

        f"{market_line}\n\n"

        f"📊 Signal:\n"
        f"{signal['signal']}\n\n"

        f"📈 TradingView:\n"
        f"{signal['tradingview']}"
    )


def make_message(
    signals
):

    if not signals:
        return None

    signals = sort_signals(
        signals
    )

    parts = [
        "🤖 Binance RSI + Polymarket",
        "",
        f"🚨 سیگنال جدید: "
        f"{len(signals)}",
        ""
    ]

    for signal in signals:

        parts.append(
            make_signal_message(
                signal
            )
        )

        parts.append(
            "\n"
            "━━━━━━━━━━━━━━━━"
            "\n"
        )

    return "\n".join(
        parts
    )


# =========================================================
# SEND
# =========================================================

def send_to_all_users(
    users,
    message
):

    if not message:
        return

    active_users = (
        get_active_users(
            users
        )
    )

    print(
        f"Sending to "
        f"{len(active_users)} users"
    )

    for chat_id in active_users:

        send_long_message(
            chat_id,
            message
        )

        time.sleep(
            0.15
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print("=" * 60)
    print(
        "BINANCE TOP 100 "
        "+ RSI "
        "+ POLYMARKET"
    )
    print("=" * 60)

    users = load_json(
        USERS_FILE,
        {}
    )

    state = load_json(
        STATE_FILE,
        {}
    )

    # Telegram commands
    users = process_updates(
        users
    )

    # -----------------------------------------------------
    # Download active Polymarket markets ONCE
    # -----------------------------------------------------

    print(
        "Loading active Polymarket markets..."
    )

    polymarket_markets = (
        get_active_polymarket_markets()
    )

    polymarket_index = (
        build_polymarket_index(
            polymarket_markets
        )
    )

    # -----------------------------------------------------
    # Binance scan
    # -----------------------------------------------------

    signals = scan_market(
        state,
        polymarket_index
    )

    # Save RSI state
    save_json(
        STATE_FILE,
        state
    )

    # Create Telegram message
    message = make_message(
        signals
    )

    if message:

        send_to_all_users(
            users,
            message
        )

    else:

        print(
            "No new RSI signals."
        )

    # Save users
    save_json(
        USERS_FILE,
        users
    )

    print("=" * 60)
    print(
        f"Users: {len(users)}"
    )

    print(
        f"New signals: "
        f"{len(signals)}"
    )

    print(
        "DONE"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
