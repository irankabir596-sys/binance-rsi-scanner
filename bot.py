import os
import json
import re
import time
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PRIVATE_ACCESS_KEY = os.getenv("PRIVATE_ACCESS_KEY", "").strip()

BINANCE_BASES = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]

TELEGRAM_BASE = "https://api.telegram.org"
POLYMARKET_URL = "https://gamma-api.polymarket.com/markets"

TOP_COINS = 100
RSI_PERIOD = 14

USERS_FILE = "users.json"
STATE_FILE = "state.json"

TIMEFRAMES = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1D": "1d",
}

TV_INTERVALS = {
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1D": "D",
}

REQUEST_TIMEOUT = 15
RETRIES = 3
MAX_WORKERS = 8

# Common Polymarket aliases.
ALIASES = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
    "BNB": ["binance coin", "bnb"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"],
    "DOT": ["polkadot", "dot"],
    "TRX": ["tron", "trx"],
    "SUI": ["sui"],
    "TON": ["toncoin", "ton"],
    "SHIB": ["shiba inu", "shib"],
    "LTC": ["litecoin", "ltc"],
    "BCH": ["bitcoin cash", "bch"],
    "UNI": ["uniswap", "uni"],
    "AAVE": ["aave"],
    "NEAR": ["near protocol", "near"],
    "ATOM": ["cosmos", "atom"],
    "ETC": ["ethereum classic", "etc"],
    "FIL": ["filecoin", "fil"],
    "APT": ["aptos", "apt"],
    "ARB": ["arbitrum", "arb"],
    "OP": ["optimism", "op"],
    "INJ": ["injective", "inj"],
    "PEPE": ["pepe", "pepe coin"],
    "WIF": ["dogwifhat", "wif"],
    "RENDER": ["render", "render token"],
    "FET": ["fetch.ai", "fetch ai", "fet"],
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 RSI-Telegram-Bot/1.0"
})


# ============================================================
# FILE HELPERS
# ============================================================

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"WARNING: Could not read {path}: {e}")
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_users(raw):
    """
    Supports old users.json formats:
    - {"123": {"active": true}}
    - {"123": true}
    - [123, 456]
    """
    users = {}

    if isinstance(raw, dict):
        for chat_id, value in raw.items():
            key = str(chat_id)
            if isinstance(value, dict):
                users[key] = {
                    "active": bool(value.get("active", False)),
                    "authorized": bool(value.get("authorized", True)),
                    "updated_at": value.get("updated_at"),
                }
            elif isinstance(value, bool):
                users[key] = {
                    "active": value,
                    "authorized": value,
                    "updated_at": None,
                }
            else:
                users[key] = {
                    "active": False,
                    "authorized": True,
                    "updated_at": None,
                }

    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "chat_id" in item:
                key = str(item["chat_id"])
                users[key] = {
                    "active": bool(item.get("active", False)),
                    "authorized": bool(item.get("authorized", True)),
                    "updated_at": item.get("updated_at"),
                }
            else:
                key = str(item)
                users[key] = {
                    "active": True,
                    "authorized": True,
                    "updated_at": None,
                }

    return users


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return f"{TELEGRAM_BASE}/bot{BOT_TOKEN}/{method}"


def telegram_call(method, payload=None):
    try:
        response = session.post(
            telegram_url(method),
            data=payload or {},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(f"Telegram HTTP {response.status_code}: {response.text[:500]}")
            return None

        data = response.json()

        if not data.get("ok"):
            print(f"Telegram API error: {data}")
            return None

        return data.get("result")
    except Exception as e:
        print(f"Telegram request error: {e}")
        return None


def send_message(chat_id, text):
    return telegram_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def process_commands(users, state):
    """
    Private access:
    /start PRIVATE_ACCESS_KEY
    """
    offset = int(state.get("telegram_offset", 0) or 0)

    updates = telegram_call(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 1,
            "allowed_updates": json.dumps(["message"]),
        },
    )

    if not updates:
        return

    for update in updates:
        update_id = update.get("update_id")
        if update_id is not None:
            state["telegram_offset"] = int(update_id) + 1

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if chat_id is None or not text.startswith("/"):
            continue

        parts = text.split(maxsplit=1)
        command = parts[0].split("@")[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        key = str(chat_id)

        if command == "/start":
            if not PRIVATE_ACCESS_KEY:
                send_message(chat_id, "❌ PRIVATE_ACCESS_KEY تنظیم نشده است.")
                continue

            if argument != PRIVATE_ACCESS_KEY:
                send_message(chat_id, "❌ لینک یا کلید دسترسی نامعتبر است.")
                continue

            users[key] = {
                "active": True,
                "authorized": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            send_message(
                chat_id,
                "✅ دسترسی تأیید شد.\n\n"
                "ربات RSI برای شما فعال شد.\n"
                "از این پس سیگنال‌های جدید برای شما ارسال می‌شود."
            )

        elif command == "/stop":
            user = users.get(key)

            if not isinstance(user, dict) or not user.get("authorized"):
                send_message(chat_id, "❌ شما دسترسی فعال ندارید.")
                continue

            user["active"] = False
            user["updated_at"] = datetime.now(timezone.utc).isoformat()

            send_message(chat_id, "⏹ ارسال سیگنال برای شما متوقف شد.")

        elif command == "/status":
            user = users.get(key)

            if not isinstance(user, dict) or not user.get("authorized"):
                send_message(chat_id, "❌ شما دسترسی ندارید.")
                continue

            status = "فعال 🟢" if user.get("active") else "متوقف 🔴"
            send_message(chat_id, f"وضعیت ربات: {status}")

        elif command == "/help":
            send_message(
                chat_id,
                "دستورات:\n"
                "/status — وضعیت ربات\n"
                "/stop — توقف سیگنال‌ها\n"
                "/start KEY — فعال‌سازی با کلید خصوصی"
            )

    save_json(USERS_FILE, users)
    save_json(STATE_FILE, state)


# ============================================================
# BINANCE HTTP
# ============================================================

def binance_get(path, params=None):
    last_error = None

    for attempt in range(1, RETRIES + 1):
        for base in BINANCE_BASES:
            url = base + path

            try:
                response = session.get(
                    url,
                    params=params or {},
                    timeout=REQUEST_TIMEOUT,
                )

                if response.status_code == 200:
                    data = response.json()
                    return data

                body = response.text[:300].replace("\n", " ")
                last_error = (
                    f"{base}{path} -> HTTP {response.status_code}: {body}"
                )

                print(
                    f"Binance request failed "
                    f"(attempt {attempt}/{RETRIES}): {last_error}"
                )

            except Exception as e:
                last_error = f"{url} -> {type(e).__name__}: {e}"
                print(
                    f"Binance request error "
                    f"(attempt {attempt}/{RETRIES}): {last_error}"
                )

        if attempt < RETRIES:
            time.sleep(1.5 * attempt)

    raise RuntimeError(last_error or "Unknown Binance API error")


def get_top_coins():
    """
    Gets the highest-volume USDT spot pairs.
    Uses /api/v3/ticker/24hr, which Binance documents as
    public market data on data-api.binance.vision.
    """
    print("Loading Binance Top 100...")

    try:
        data = binance_get("/api/v3/ticker/24hr")

        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected ticker response type: {type(data).__name__}"
            )

        coins = []

        for item in data:
            if not isinstance(item, dict):
                continue

            symbol = str(item.get("symbol", "")).upper()

            if not symbol.endswith("USDT"):
                continue

            # Exclude common leveraged-token suffixes.
            if symbol.endswith((
                "UPUSDT",
                "DOWNUSDT",
                "BULLUSDT",
                "BEARUSDT",
            )):
                continue

            try:
                quote_volume = float(item.get("quoteVolume", 0))
            except (TypeError, ValueError):
                quote_volume = 0.0

            if quote_volume <= 0:
                continue

            coins.append({
                "symbol": symbol,
                "volume": quote_volume,
            })

        if not coins:
            raise RuntimeError("Binance returned zero usable USDT pairs.")

        coins.sort(
            key=lambda x: x["volume"],
            reverse=True
        )

        result = coins[:TOP_COINS]

        print(f"Loaded {len(result)} coins.")

        return result

    except Exception as e:
        print(f"ERROR: Could not load Binance coins: {e}")
        return []


def get_klines(symbol, interval, limit=80):
    try:
        data = binance_get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )

        if not isinstance(data, list):
            return None

        if len(data) < RSI_PERIOD + 2:
            return None

        return data

    except Exception as e:
        print(f"Kline error {symbol} {interval}: {e}")
        return None


# ============================================================
# RSI
# ============================================================

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None

    changes = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def get_rsi_from_klines(data):
    """
    RSI is calculated on closed candles.
    The currently open candle is excluded.
    """
    closed = data[:-1]

    try:
        closes = [float(row[4]) for row in closed]
    except (TypeError, ValueError, IndexError):
        return None

    return calculate_rsi(closes, RSI_PERIOD)


# ============================================================
# VOLUME / TREND
# ============================================================

def volume_info(data):
    """
    Current candle volume is compared with the previous 20 candles.
    """
    try:
        current_volume = float(data[-1][5])
        previous = [float(row[5]) for row in data[-21:-1]]

        if not previous:
            return 1.0, "🟡"

        average = sum(previous) / len(previous)

        if average <= 0:
            ratio = 1.0
        else:
            ratio = current_volume / average

        if ratio >= 1.5:
            label = "🟢🟢🟢"
        elif ratio >= 0.75:
            label = "🟡🟡"
        else:
            label = "🔴"

        return ratio, label

    except Exception:
        return 1.0, "🟡"


def trend_from_klines(data):
    try:
        closes = [float(row[4]) for row in data[-21:]]
        if len(closes) < 2:
            return "خنثی", "⚪"

        first = closes[0]
        last = closes[-1]

        if last > first:
            return "bullish","🦬"
        if last < first:
            return "bearish","🐻"

        return "neutral","🐫"

    except Exception:
        return "neutral","🐫"


# ============================================================
# POLYMARKET
# ============================================================

def normalize_text(value):
    value = str(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_base_symbol(symbol):
    return symbol.upper().replace("USDT", "")


def market_mentions_asset(market, base):
    text_parts = []

    for key in (
        "question",
        "title",
        "description",
        "slug",
        "ticker",
        "eventSlug",
    ):
        value = market.get(key)
        if value:
            text_parts.append(str(value))

    text = normalize_text(" ".join(text_parts))

    candidates = [base.lower()]
    candidates.extend(ALIASES.get(base.upper(), []))

    for candidate in candidates:
        candidate_norm = normalize_text(candidate)
        if candidate_norm and candidate_norm in text:
            return True

    return False


def market_timeframe(market):
    text = normalize_text(
        " ".join(
            str(market.get(k, ""))
            for k in (
                "question",
                "title",
                "description",
                "slug",
                "ticker",
            )
        )
    )

    if "5 minute" in text or "5 min" in text or "5m" in text:
        return "5m"

    if "15 minute" in text or "15 min" in text or "15m" in text:
        return "15m"

    if "1 hour" in text or "1 hr" in text or "1h" in text:
        return "1h"

    if "4 hour" in text or "4 hr" in text or "4h" in text:
        return "4h"

    if "daily" in text or "1 day" in text or "24 hour" in text:
        return "1D"

    return None


def parse_outcome_probability(market):
    """
    Attempts to extract UP probability from common Polymarket
    outcome/outcomePrices fields.
    """
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")

    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = None

    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = None

    if not isinstance(outcomes, list) or not isinstance(prices, list):
        return None

    if len(outcomes) != len(prices):
        return None

    for outcome, price in zip(outcomes, prices):
        try:
            p = float(price)
        except (TypeError, ValueError):
            continue

        name = normalize_text(outcome)

        if name in ("up", "yes"):
            return p * 100.0

    return None


def discover_polymarket_probability(symbol, timeframe):
    base = extract_base_symbol(symbol)

    try:
        response = session.get(
            POLYMARKET_URL,
            params={
                "active": "true",
                "closed": "false",
                "limit": 500,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"Polymarket HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
            return None

        data = response.json()

        if isinstance(data, dict):
            markets = data.get("data") or data.get("markets") or []
        elif isinstance(data, list):
            markets = data
        else:
            return None

        candidates = []

        for market in markets:
            if not isinstance(market, dict):
                continue

            if not market_mentions_asset(market, base):
                continue

            tf = market_timeframe(market)

            if tf != timeframe:
                continue

            probability = parse_outcome_probability(market)

            if probability is not None:
                candidates.append(probability)

        if not candidates:
            return None

        # If several matches exist, use the first usable one.
        return max(0.0, min(100.0, candidates[0]))

    except Exception as e:
        print(f"Polymarket error {symbol} {timeframe}: {e}")
        return None


# ============================================================
# SIGNAL LOGIC
# ============================================================

def signal_direction(rsi):
    if rsi >= 70:
        return "bearish"
    if rsi <= 30:
        return "bullish"
    if rsi > 55:
        return "bearish"
    if rsi < 45:
        return "bullish"
    return "neutral"


def score_signal(rsi, volume_ratio, trend, market_probability):
    score = 0

    if rsi <= 30:
        score += 2
    elif rsi >= 70:
        score -= 2
    elif rsi < 45:
        score += 1
    elif rsi > 55:
        score -= 1

    if volume_ratio >= 1.5:
        if rsi <= 30:
            score += 2
        elif rsi >= 70:
            score -= 2

    elif volume_ratio >= 0.75:
        if rsi <= 30:
            score += 1
        elif rsi >= 70:
            score -= 1

    if trend == "صعودی":
        score += 2
    elif trend == "نزولی":
        score -= 2

    if market_probability is not None:
        if market_probability >= 60:
            score += 3
        elif market_probability <= 40:
            score -= 3

    return score


def score_label(score):
    if score >= 3:
        return "strong bullish 🦬"

    if score > 0:
        return "bullish 🦬"

    if score <= -3:
        return "strong bearish 🐻"

    if score < 0:
        return "bearish 🐻"

    return "neutral 🐫"


def tv_link(symbol, timeframe):
    interval = TV_INTERVALS.get(timeframe, "5")
    return (
        "https://www.tradingview.com/chart/"
        f"?symbol=BINANCE%3A{symbol}&interval={interval}"
    )


def format_market_probability(probability):
    if probability is None:
        return "N/A"

    if probability >= 60:
        emoji = "🟢"
    elif probability <= 40:
        emoji = "🔴"
    else:
        emoji = "🟡"

    return f"UP {probability:.0f}% {emoji}"


def build_message(
    symbol,
    timeframe,
    rsi,
    volume_ratio,
    volume_emoji,
    trend,
    trend_emoji,
    market_probability,
):
    score = score_signal(
        rsi,
        volume_ratio,
        trend,
        market_probability,
    )

    label = score_label(score)

    market_text = format_market_probability(market_probability)

    return (
        f"<b>{symbol} | {timeframe}</b>\n\n"
        f"RSI: {rsi:.1f}       "
        f"{'🔴' if rsi >= 70 else '🟢' if rsi <= 30 else '🟡'}\n"
        f"Volume: {volume_ratio:.1f}x    {volume_emoji}\n"
        f"Trend: {trend}    {trend_emoji}\n"
        f"Market: {market_text}\n\n"
        f"📊 <b>Signal:</b>\n"
        f"{label}\n\n"
        f"📈 <a href=\"{tv_link(symbol, timeframe)}\">TradingView</a>"
    )


# ============================================================
# COIN SCANNING
# ============================================================

def scan_coin(coin):
    symbol = coin["symbol"]
    signals = []

    for timeframe, interval in TIMEFRAMES.items():
        data = get_klines(symbol, interval, limit=80)

        if not data:
            continue

        # RSI on closed candles.
        rsi = get_rsi_from_klines(data)

        if rsi is None:
            continue

        volume_ratio, volume_emoji = volume_info(data)
        trend, trend_emoji = trend_from_klines(data)

        signals.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "volume_emoji": volume_emoji,
            "trend": trend,
            "trend_emoji": trend_emoji,
        })

    return signals


def is_new_cross(previous_rsi, current_rsi):
    if previous_rsi is None:
        return False

    try:
        previous_rsi = float(previous_rsi)
        current_rsi = float(current_rsi)
    except (TypeError, ValueError):
        return False

    entered_overbought = previous_rsi < 70 <= current_rsi
    entered_oversold = previous_rsi > 30 >= current_rsi

    return entered_overbought or entered_oversold


def signal_key(symbol, timeframe):
    return f"{symbol}:{timeframe}"


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")

    if not PRIVATE_ACCESS_KEY:
        raise RuntimeError("PRIVATE_ACCESS_KEY is missing.")

    users = normalize_users(load_json(USERS_FILE, {}))
    state = load_json(STATE_FILE, {})

    if not isinstance(state, dict):
        state = {}

    # Process private Telegram commands first.
    process_commands(users, state)

    active_users = [
        chat_id
        for chat_id, user in users.items()
        if isinstance(user, dict)
        and user.get("authorized")
        and user.get("active")
    ]

    print(f"Active authorized users: {len(active_users)}")

    if not active_users:
        print("No active users. Nothing to send.")

    coins = get_top_coins()

    if not coins:
        raise RuntimeError(
            "Could not load Binance Top 100 after all retries."
        )

    all_signals = []

    print(f"Scanning {len(coins)} coins...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(scan_coin, coin): coin
            for coin in coins
        }

        for future in as_completed(futures):
            coin = futures[future]

            try:
                result = future.result()
                all_signals.extend(result)
            except Exception as e:
                print(
                    f"Scan error {coin.get('symbol')}: {e}"
                )

    previous = state.setdefault("rsi", {})
    pending = []

    for signal in all_signals:
        key = signal_key(
            signal["symbol"],
            signal["timeframe"],
        )

        current_rsi = signal["rsi"]
        previous_rsi = previous.get(key)

        if is_new_cross(previous_rsi, current_rsi):
            pending.append(signal)

        previous[key] = current_rsi

    # Save RSI state even when there are no new signals.
    save_json(STATE_FILE, state)

    print(
        f"Scan complete. "
        f"Signals detected: {len(pending)}"
    )

    if not pending:
        return

    # Highest overbought first, lowest oversold first.
    def sort_key(signal):
        rsi = signal["rsi"]
        if rsi >= 70:
            group = 0
            value = -rsi
        else:
            group = 1
            value = rsi

        tf_order = {
            "5m": 0,
            "15m": 1,
            "1h": 2,
            "4h": 3,
            "1D": 4,
        }

        return (
            group,
            value,
            tf_order.get(signal["timeframe"], 99),
        )

    pending.sort(key=sort_key)

    for signal in pending:
        probability = discover_polymarket_probability(
            signal["symbol"],
            signal["timeframe"],
        )

        message = build_message(
            symbol=signal["symbol"],
            timeframe=signal["timeframe"],
            rsi=signal["rsi"],
            volume_ratio=signal["volume_ratio"],
            volume_emoji=signal["volume_emoji"],
            trend=signal["trend"],
            trend_emoji=signal["trend_emoji"],
            market_probability=probability,
        )

        for chat_id in active_users:
            try:
                send_message(chat_id, message)
            except Exception as e:
                print(
                    f"Send error for {chat_id}: {e}"
                )

    save_json(USERS_FILE, users)
    save_json(STATE_FILE, state)

    print("Finished successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FATAL ERROR:")
        traceback.print_exc()
        raise
