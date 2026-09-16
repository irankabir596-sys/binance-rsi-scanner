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

        print(f"Binance ticker error: {e}")

        return []


def get_candle_data(symbol, interval):
    url = f"{BINANCE_API}/api/v3/klines"

    try:
        response = session.get(
            url,
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": 100
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            return []

        return data

    except Exception as e:
        print(f"Candle error {symbol} {interval}: {e}")
        return []


# ============================================================
# RSI
# ============================================================

def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    if len(gains) < period:
        return None

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        if avg_gain == 0:
            return 50.0
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ============================================================
# VOLUME
# ============================================================

def calculate_volume_info(candles):
    if len(candles) < 22:
        return None

    current_volume = safe_float(candles[-1][5])
    previous_volumes = [safe_float(candle[5]) for candle in candles[-21:-1]]

    if not previous_volumes:
        return None

    average_volume = sum(previous_volumes) / len(previous_volumes)

    if average_volume <= 0:
        return None

    ratio = current_volume / average_volume

    if ratio < 0.75:
        label = "کم"
        emoji = "🟢"
    elif ratio < 1.5:
        label = "متوسط"
        emoji = "🟡"
    else:
        label = "زیاد"
        emoji = "🔥"

    return {
        "ratio": ratio,
        "label": label,
        "emoji": emoji
    }


# ============================================================
# TREND
# ============================================================

def calculate_trend(closes):
    if len(closes) < 20:
        return {"label": "خنثی", "emoji": "🟡", "score": 0}

    recent = closes[-5:]
    previous = closes[-20:-5]

    if not previous:
        return {"label": "خنثی", "emoji": "🟡", "score": 0}

    recent_avg = sum(recent) / len(recent)
    previous_avg = sum(previous) / len(previous)

    if previous_avg <= 0:
        return {"label": "خنثی", "emoji": "🟡", "score": 0}

    change_percent = ((recent_avg - previous_avg) / previous_avg) * 100

    if change_percent > 0.2:
        return {
            "label": "صعودی",
            "emoji": "🟢",
            "score": 2,
            "change": change_percent
        }

    if change_percent < -0.2:
        return {
            "label": "نزولی",
            "emoji": "🔴",
            "score": -2,
            "change": change_percent
        }

    return {
        "label": "خنثی",
        "emoji": "🟡",
        "score": 0,
        "change": change_percent
    }


# ============================================================
# RSI ENTRY
# ============================================================

def check_rsi_entry(previous_rsi, current_rsi):
    if previous_rsi is None:
        return False

    if previous_rsi > 30 and current_rsi <= 30:
        return True

    if previous_rsi < 70 and current_rsi >= 70:
        return True

    return False


# ============================================================
# POLYMARKET
# ============================================================

def get_active_polymarket_markets():
    markets = []
    limit = 100

    for page in range(10):
        offset = page * limit

        try:
            response = session.get(
                f"{POLY_GAMMA}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset
                },
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()
            data = response.json()

            if isinstance(data, list):
                batch = data
            elif isinstance(data, dict):
                batch = data.get("data") or data.get("markets") or []
            else:
                batch = []

            if not batch:
                break

            markets.extend(batch)

            print(f"Polymarket page {page + 1}: {len(batch)} markets")

            if len(batch) < limit:
                break

        except Exception as e:
            print(f"Polymarket page error {page + 1}: {e}")
            break

    print(f"Total Polymarket markets: {len(markets)}")
    return markets


# ============================================================
# POLYMARKET PARSING
# ============================================================

ASSET_ALIASES = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
    "BNB": ["bnb", "binance coin"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"],
    "SUI": ["sui"],
    "DOT": ["polkadot", "dot"],
    "TRX": ["tron", "trx"],
    "LTC": ["litecoin", "ltc"],
    "BCH": ["bitcoin cash", "bch"],
    "SHIB": ["shiba inu", "shib"],
    "UNI": ["uniswap", "uni"],
    "AAVE": ["aave"],
    "ATOM": ["cosmos", "atom"],
    "NEAR": ["near protocol", "near"],
    "APT": ["aptos", "apt"],
    "ARB": ["arbitrum", "arb"],
    "OP": ["optimism", "op"],
    "INJ": ["injective", "inj"],
    "FIL": ["filecoin", "fil"],
    "ICP": ["internet computer", "icp"],
    "HBAR": ["hedera", "hbar"],
    "PEPE": ["pepe"],
    "RENDER": ["render", "render token"],
    "TIA": ["celestia", "tia"],
    "TON": ["toncoin", "ton"],
    "WLD": ["worldcoin", "wld"],
    "CRV": ["curve", "crv"],
    "CAKE": ["pancakeswap", "cake"],
    "LDO": ["lido", "ldo"],
    "PENDLE": ["pendle"],
    "ENA": ["ethena", "ena"],
    "POL": ["polygon", "pol"],
    "MKR": ["maker", "mkr"],
    "MATIC": ["matic", "polygon"],
    "RAY": ["raydium", "ray"],
    "JUP": ["jupiter", "jup"],
    "SEI": ["sei"],
    "STX": ["stacks", "stx"],
    "XLM": ["stellar", "xlm"],
    "ALGO": ["algorand", "algo"],
    "VET": ["vechain", "vet"],
    "SAND": ["sandbox", "sand"],
    "MANA": ["decentraland", "mana"],
    "AXS": ["axie infinity", "axs"],
    "GALA": ["gala"],
    "APE": ["apecoin", "ape"],
    "FLOKI": ["floki"],
    "BONK": ["bonk"],
    "PENGU": ["pudgy penguins", "pengu"]
}


def normalize_text(text):
    text = str(text or "").lower()
    text = text.replace("-", " ")
    text = text.replace("_", " ")
    return text


def contains_term(text, term):
    text = normalize_text(text)
    term = normalize_text(term)

    if not term:
        return False

    if len(term) <= 4:
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None

    return term in text


def detect_asset(text, available_assets=None):
    text = normalize_text(text)
    assets = available_assets if available_assets else ASSET_ALIASES.keys()
    candidates = []

    for asset in assets:
        aliases = ASSET_ALIASES.get(asset, [asset.lower()])
        for alias in aliases:
            if contains_term(text, alias):
                candidates.append((len(alias), asset))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def detect_timeframe(text):
    text = normalize_text(text)

    patterns = [
        ("15m", ["15 minute", "15m"]),
        ("5m", ["5 minute", "5m"]),
        ("4h", ["4 hour", "4h"]),
        ("1h", ["1 hour", "hourly", "1h"]),
        ("1D", ["1 day", "daily", "24 hour"])
    ]

    for timeframe, aliases in patterns:
        for alias in aliases:
            if contains_term(text, alias):
                return timeframe

    return None


def is_up_down_market(text):
    text = normalize_text(text)
    patterns = ["up or down", "up/down", "up down", "updown"]
    return any(pattern in text for pattern in patterns)


def parse_datetime(value):
    if not value:
        return None

    try:
        value = str(value)
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except Exception:
        return None


def market_is_current(market):
    if market.get("closed") is True:
        return False

    if market.get("active") is False:
        return False

    now = datetime.now(timezone.utc)
    end_date = parse_datetime(market.get("endDate") or market.get("end_date"))

    if end_date and end_date <= now:
        return False

    return True


def market_text(market):
    fields = [
        market.get("question"),
        market.get("description"),
        market.get("slug"),
        market.get("groupItemTitle"),
        market.get("title")
    ]

    return " ".join(str(x or "") for x in fields)


def build_polymarket_index(markets, binance_coins):
    index = {}
    available_assets = set()

    for coin in binance_coins:
        symbol = coin["symbol"]
        if symbol.endswith("USDT"):
            available_assets.add(symbol[:-4])

    detected_count = 0

    for market in markets:
        try:
            if not isinstance(market, dict):
                continue

            if not market_is_current(market):
                continue

            text = market_text(market)

            if not is_up_down_market(text):
                continue

            asset = detect_asset(text, available_assets)
            if not asset:
                continue

            timeframe = detect_timeframe(text)
            if not timeframe:
                continue

            key = (asset, timeframe)
            index.setdefault(key, []).append(market)
            detected_count += 1

        except Exception as e:
            print(f"Polymarket index error: {e}")

    print("Detected Polymarket crypto groups:", len(index))

    for key, values in sorted(index.items()):
        print("POLY:", key, "->", len(values))

    return index


# ============================================================
# POLYMARKET MARKET SELECTION
# ============================================================

def choose_market(candidates):
    if not candidates:
        return None

    now = datetime.now(timezone.utc)
    current = []
    future = []

    for market in candidates:
        if not isinstance(market, dict):
            continue

        if not market_is_current(market):
            continue

        start_date = parse_datetime(market.get("startDate") or market.get("start_date"))
        end_date = parse_datetime(market.get("endDate") or market.get("end_date"))

        if start_date is None or start_date <= now:
            if end_date is None or end_date > now:
                current.append(market)
        elif (start_date - now).total_seconds() <= 300:
            future.append(market)

    pool = current or future

    if not pool:
        return None

    pool.sort(
        key=lambda m: safe_float(
            m.get("volume24hr") or m.get("volume24h") or m.get("volume")
        ),
        reverse=True
    )

    return pool[0]


# ============================================================
# POLYMARKET OUTCOME PARSING
# ============================================================

def parse_json_list(value):
    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    return []


def get_polymarket_price(market):
    outcomes = parse_json_list(market.get("outcomes"))
    prices = parse_json_list(market.get("outcomePrices"))
    token_ids = parse_json_list(market.get("clobTokenIds"))

    if not outcomes:
        outcomes = ["Up", "Down"]

    up_index = None
    down_index = None

    for i, outcome in enumerate(outcomes):
        label = str(outcome).strip().lower()

        if label in ("up", "yes", "true"):
            up_index = i
        elif label in ("down", "no", "false"):
            down_index = i

    if up_index is None:
        up_index = 0

    if down_index is None and len(outcomes) > 1:
        down_index = 1

    if token_ids and up_index < len(token_ids):
        try:
            up_token = token_ids[up_index]

            response = session.get(
                f"{POLY_CLOB}/midpoint",
                params={"token_id": up_token},
                timeout=REQUEST_TIMEOUT
            )

            if response.ok:
                data = response.json()
                mid = data.get("mid") or data.get("mid_price") or data.get("price")

                if mid is not None:
                    up_price = safe_float(mid, -1)

                    if 0 <= up_price <= 1:
                        return {
                            "up": up_price,
                            "down": 1.0 - up_price
                        }

        except Exception as e:
            print(f"CLOB midpoint error: {e}")

    if prices:
        up_price = None
        down_price = None

        if up_index < len(prices):
            up_price = safe_float(prices[up_index], -1)

        if down_index is not None and down_index < len(prices):
            down_price = safe_float(prices[down_index], -1)

        if up_price is not None and 0 <= up_price <= 1:
            if not (down_price is not None and 0 <= down_price <= 1):
                down_price = 1.0 - up_price

            return {
                "up": up_price,
                "down": down_price
            }

    return None


def get_prediction_market(symbol, timeframe, polymarket_index):
    if not symbol.endswith("USDT"):
        return None

    asset = symbol[:-4]
    key = (asset, timeframe)
    candidates = polymarket_index.get(key, [])
    market = choose_market(candidates)

    if not market:
        return None

    price = get_polymarket_price(market)

    if not price:
        return None

    return {
        "up": price["up"],
        "down": price["down"],
        "question": market.get("question", ""),
        "market": market
    }


# ============================================================
# SIGNAL SCORE
# ============================================================

def calculate_signal(rsi_value, volume_info, trend_info, market_info):
    score = 0

    if rsi_value <= 30:
        score += 2
    elif rsi_value >= 70:
        score -= 2
    elif rsi_value < 45:
        score += 1
    elif rsi_value > 55:
        score -= 1

    if volume_info:
        ratio = volume_info["ratio"]

        if ratio >= 1.5:
            if rsi_value <= 50:
                score += 2
            else:
                score -= 2
        elif ratio >= 0.75:
            if rsi_value <= 50:
                score += 1
            else:
                score -= 1

    if trend_info:
        score += trend_info.get("score", 0)

    if market_info:
        up = market_info["up"]
        down = market_info["down"]

        if up >= 0.60:
            score += 3
        elif down >= 0.60:
            score -= 3

    if score >= 3:
        return {"label": "تمایل صعودی قوی", "emoji": "🟢", "score": score}

    if score > 0:
        return {"label": "تمایل صعودی", "emoji": "🟢", "score": score}

    if score <= -3:
        return {"label": "تمایل نزولی قوی", "emoji": "🔴", "score": score}

    if score < 0:
        return {"label": "تمایل نزولی", "emoji": "🔴", "score": score}

    return {"label": "خنثی", "emoji": "🟡", "score": 0}


# ============================================================
# TRADINGVIEW
# ============================================================

def tradingview_url(symbol, timeframe):
    interval = {
        "5m": "5",
        "15m": "15",
        "1h": "60",
        "4h": "240",
        "1D": "D"
    }.get(timeframe, "5")

    return (
        "https://www.tradingview.com/chart/"
        f"?symbol=BINANCE:{symbol}"
        f"&interval={interval}"
    )


# ============================================================
# MESSAGE
# ============================================================

def format_signal(
    symbol,
    timeframe,
    rsi_value,
    volume_info,
    trend_info,
    market_info,
    signal
):
    rsi_emoji = (
        "🔴" if rsi_value >= 70
        else "🟢" if rsi_value <= 30
        else "🟡"
    )

    if volume_info:
        volume_text = f"{volume_info['ratio']:.1f}x {volume_info['emoji']}"
    else:
        volume_text = "N/A"

    trend_text = f"{trend_info['label']} {trend_info['emoji']}"

    if market_info:
        up_percent = market_info["up"] * 100
        down_percent = market_info["down"] * 100

        if up_percent >= down_percent:
            market_text = f"UP {up_percent:.0f}% 🟢"
        else:
            market_text = f"DOWN {down_percent:.0f}% 🔴"
    else:
        market_text = "N/A"

    tv = tradingview_url(symbol, timeframe)

    return (
        f"<b>{symbol} | {timeframe}</b>\n\n"
        f"RSI: {rsi_value:.1f} {rsi_emoji}\n"
        f"Volume: {volume_text}\n"
        f"Trend: {trend_text}\n"
        f"Market: {market_text}\n\n"
        f"📊 <b>Signal:</b>\n"
        f"{signal['emoji']} <b>{signal['label']}</b>\n\n"
        f'<a href="{tv}">📈 TradingView</a>'
    )


# ============================================================
# SEND TO USERS
# ============================================================

def send_to_all_users(users, message):
    active_users = get_active_users(users)

    if not active_users:
        print("No active Telegram users.")
        return

    print(f"Sending signal to {len(active_users)} users...")

    for chat_id in active_users:
        try:
            result = send_message(chat_id, message)

            if result and result.get("ok"):
                print(f"Signal sent to {chat_id}")
            else:
                print(f"Failed sending to {chat_id}")

        except Exception as e:
            print(f"Send error {chat_id}: {e}")

        time.sleep(0.1)


# ============================================================
# STATE
# ============================================================

def load_state():
    state = load_json(STATE_FILE, {})
    return state if isinstance(state, dict) else {}


def save_state(state):
    try:
        save_json(STATE_FILE, state)
    except Exception as e:
        print(f"State save error: {e}")


def state_key(symbol, timeframe):
    return f"{symbol}|{timeframe}"


# ============================================================
# SCAN ONE COIN
# ============================================================

def scan_coin(symbol, state, polymarket_index):
    signals = []

    for timeframe, interval in TIMEFRAMES.items():
        try:
            candles = get_candle_data(symbol, interval)

            if len(candles) < RSI_PERIOD + 25:
                continue

            # RSI is calculated using closed candles.
            closed_candles = candles[:-1]

            closes = [safe_float(candle[4]) for candle in closed_candles]
            rsi_value = calculate_rsi(closes, RSI_PERIOD)

            if rsi_value is None:
                continue

            previous_closes = closes[:-1]
            previous_rsi = calculate_rsi(previous_closes, RSI_PERIOD)

            key = state_key(symbol, timeframe)

            # First observation: save RSI but do not alert.
            if previous_rsi is None:
                state[key] = {
                    "rsi": rsi_value,
                    "updated": datetime.now(timezone.utc).isoformat()
                }
                continue

            old_state = state.get(key)
            old_rsi = None

            if isinstance(old_state, dict):
                old_rsi = safe_float(old_state.get("rsi"), None)

            comparison_rsi = old_rsi if old_rsi is not None else previous_rsi
            entered = check_rsi_entry(comparison_rsi, rsi_value)

            state[key] = {
                "rsi": rsi_value,
                "updated": datetime.now(timezone.utc).isoformat()
            }

            if not entered:
                continue

            print(f"NEW SIGNAL {symbol} {timeframe} RSI={rsi_value:.2f}")

            volume_info = calculate_volume_info(candles)
            trend_info = calculate_trend(closes)
            market_info = get_prediction_market(
                symbol,
                timeframe,
                polymarket_index
            )

            signal = calculate_signal(
                rsi_value,
                volume_info,
                trend_info,
                market_info
            )

            signals.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "rsi": rsi_value,
                "volume": volume_info,
                "trend": trend_info,
                "market": market_info,
                "signal": signal
            })

        except Exception as e:
            print(f"Scan error {symbol} {timeframe}: {e}")
            traceback.print_exc()

    return signals


# ============================================================
# SORT SIGNALS
# ============================================================

def sort_signals(signals):
    timeframe_order = {
        "5m": 0,
        "15m": 1,
        "1h": 2,
        "4h": 3,
        "1D": 4
    }

    def sort_key(signal):
        rsi = signal["rsi"]

        if rsi >= 70:
            return (
                0,
                -rsi,
                timeframe_order.get(signal["timeframe"], 99)
            )

        return (
            1,
            rsi,
            timeframe_order.get(signal["timeframe"], 99)
        )

    return sorted(signals, key=sort_key)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("BINANCE TOP 100 + RSI + POLYMARKET")
    print("=" * 60)

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not configured.")
        raise SystemExit(1)

    users = load_users()
    print(f"Users in database: {len(users)}")

    users = process_telegram_users(users)
    save_json(USERS_FILE, users)

    print("Loading Binance Top 100...")
    coins = get_top_binance_coins()

    if not coins:
        print("ERROR: Could not load Binance coins.")
        raise SystemExit(1)

    print(f"Top Binance coins: {len(coins)}")

    print("Loading active Polymarket markets...")
    polymarket_markets = get_active_polymarket_markets()

    polymarket_index = build_polymarket_index(
        polymarket_markets,
        coins
    )

    state = load_state()
    all_signals = []

    for number, coin in enumerate(coins, start=1):
        symbol = coin["symbol"]
        print(f"[{number}/{len(coins)}] {symbol}")

        signals = scan_coin(
            symbol,
            state,
            polymarket_index
        )

        all_signals.extend(signals)

    all_signals = sort_signals(all_signals)

    print(f"Total new signals: {len(all_signals)}")

    for signal in all_signals:
        message = format_signal(
            symbol=signal["symbol"],
            timeframe=signal["timeframe"],
            rsi_value=signal["rsi"],
            volume_info=signal["volume"],
            trend_info=signal["trend"],
            market_info=signal["market"],
            signal=signal["signal"]
        )

        send_to_all_users(users, message)

    save_state(state)
    save_json(USERS_FILE, users)

    print("=" * 60)
    print("BOT FINISHED SUCCESSFULLY")
    print("=" * 60)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("=" * 60)
        print("FATAL ERROR")
        print("=" * 60)
        print(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        raise
