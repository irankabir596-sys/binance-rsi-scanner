import html
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote

import requests

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PRIVATE_ACCESS_KEY = os.getenv("PRIVATE_ACCESS_KEY", "").strip()

BINANCE_BASE = "https://data-api.binance.vision"
TELEGRAM_BASE = "https://api.telegram.org"
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

TOP_COINS = 100
RSI_PERIOD = 14
UPPER_RSI = 80.0
LOWER_RSI = 20.0

USERS_FILE = "users.json"
STATE_FILE = "state.json"

TIMEFRAMES = {
    "1D": {"binance": "1d", "tv": "D"},
    "4h": {"binance": "4h", "tv": "240"},
    "1h": {"binance": "1h", "tv": "60"},
    "15m": {"binance": "15m", "tv": "15"},
}

REQUEST_TIMEOUT = 15
BINANCE_RETRIES = 3
MAX_WORKERS = 8

# Polymarket crypto market aliases.
POLY_ALIASES = {
    "BTC": ["btc", "bitcoin"],
    "ETH": ["eth", "ethereum"],
    "SOL": ["sol", "solana"],
    "BNB": ["bnb", "binance coin"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["doge", "dogecoin"],
    "ADA": ["ada", "cardano"],
    "AVAX": ["avax", "avalanche"],
    "LINK": ["link", "chainlink"],
    "DOT": ["dot", "polkadot"],
    "TRX": ["trx", "tron"],
    "SUI": ["sui"],
    "TON": ["ton", "toncoin"],
    "SHIB": ["shib", "shiba inu"],
    "LTC": ["ltc", "litecoin"],
    "BCH": ["bch", "bitcoin cash"],
    "UNI": ["uni", "uniswap"],
    "AAVE": ["aave"],
    "NEAR": ["near", "near protocol"],
    "ATOM": ["atom", "cosmos"],
    "ETC": ["etc", "ethereum classic"],
    "FIL": ["fil", "filecoin"],
    "APT": ["apt", "aptos"],
    "ARB": ["arb", "arbitrum"],
    "OP": ["op", "optimism"],
    "INJ": ["inj", "injective"],
    "PEPE": ["pepe"],
    "WIF": ["wif", "dogwifhat"],
    "RENDER": ["render"],
    "FET": ["fet", "fetch.ai", "fetch ai"],
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 RSI-Polymarket-Telegram-Bot/2.0"
})


# ============================================================
# GENERIC HELPERS
# ============================================================
def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp(value, low, high):
    return max(low, min(high, value))


def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"WARNING: failed to read {path}: {exc}")
        return default


def save_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_users(raw):
    """Keep compatibility with earlier users.json formats."""
    users = {}

    if isinstance(raw, dict):
        items = raw.items()
        for chat_id, value in items:
            cid = str(chat_id)
            if isinstance(value, dict):
                users[cid] = {
                    "active": bool(value.get("active", False)),
                    "authorized": bool(value.get("authorized", False)),
                    "username": str(value.get("username", "")),
                    "first_name": str(value.get("first_name", "")),
                    "last_seen": value.get("last_seen", ""),
                }
            elif isinstance(value, bool):
                users[cid] = {
                    "active": value,
                    "authorized": value,
                    "username": "",
                    "first_name": "",
                    "last_seen": "",
                }
    elif isinstance(raw, list):
        for item in raw:
            cid = str(item.get("chat_id")) if isinstance(item, dict) else str(item)
            users[cid] = {
                "active": True,
                "authorized": True,
                "username": item.get("username", "") if isinstance(item, dict) else "",
                "first_name": item.get("first_name", "") if isinstance(item, dict) else "",
                "last_seen": item.get("last_seen", "") if isinstance(item, dict) else "",
            }

    return users


# ============================================================
# TELEGRAM
# ============================================================
def telegram_call(method, payload=None):
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing")
        return None

    try:
        response = session.post(
            f"{TELEGRAM_BASE}/bot{BOT_TOKEN}/{method}",
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
    except Exception as exc:
        print(f"Telegram request error: {exc}")
        return None


def send_message(chat_id, text):
    return telegram_call(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def process_commands(users, state):
    """Authorize only with /start PRIVATE_ACCESS_KEY."""
    offset = int(state.get("telegram_offset", 0) or 0)
    result = telegram_call(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 1,
            "allowed_updates": json.dumps(["message"]),
        },
    )

    if not isinstance(result, list):
        return users, state

    for update in result:
        try:
            update_id = int(update.get("update_id", 0))
            state["telegram_offset"] = update_id + 1

            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue

            cid = str(chat_id)
            text = str(message.get("text", "")).strip()
            sender = message.get("from") or {}

            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                supplied_key = parts[1].strip() if len(parts) == 2 else ""

                if not PRIVATE_ACCESS_KEY:
                    send_message(cid, "Bot access key is not configured.")
                    continue

                if supplied_key != PRIVATE_ACCESS_KEY:
                    send_message(cid, "❌ Invalid access link.")
                    continue

                users[cid] = {
                    "active": True,
                    "authorized": True,
                    "username": str(sender.get("username", "")),
                    "first_name": str(sender.get("first_name", "")),
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }
                send_message(cid, "✅ Bot activated.")

            elif text.split()[0].lower() == "/stop" if text else False:
                if cid in users and users[cid].get("authorized"):
                    users[cid]["active"] = False
                    users[cid]["last_seen"] = datetime.now(timezone.utc).isoformat()
                    send_message(cid, "⛔ Signals stopped.")
                else:
                    send_message(cid, "❌ You are not authorized.")

            elif text.split()[0].lower() == "/status" if text else False:
                active = bool(users.get(cid, {}).get("authorized") and users.get(cid, {}).get("active"))
                send_message(cid, "🟢 Active" if active else "🔴 Inactive")

        except Exception as exc:
            print(f"Telegram update error: {exc}")

    return users, state


# ============================================================
# BINANCE
# ============================================================
def binance_get(path, params=None):
    last_error = None
    for attempt in range(BINANCE_RETRIES):
        try:
            response = session.get(
                f"{BINANCE_BASE}{path}",
                params=params or {},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(0.7 * (attempt + 1))
    print(f"Binance request failed {path}: {last_error}")
    return None


def get_top_binance_coins():
    data = binance_get("/api/v3/ticker/24hr")
    if not isinstance(data, list):
        return []

    coins = []
    for item in data:
        symbol = str(item.get("symbol", ""))
        if not symbol.endswith("USDT"):
            continue

        base = symbol[:-4]
        if not base or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue

        quote_volume = safe_float(item.get("quoteVolume"))
        if quote_volume is None:
            continue

        coins.append({"symbol": symbol, "volume": quote_volume})

    coins.sort(key=lambda x: x["volume"], reverse=True)
    return coins[:TOP_COINS]


def get_closed_klines(symbol, interval, limit=260):
    data = binance_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    if not isinstance(data, list) or len(data) < 60:
        return None

    candles = []
    for row in data:
        try:
            candles.append({
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        except (TypeError, ValueError, IndexError):
            continue

    # Binance's last kline is normally the currently forming candle.
    if len(candles) >= 2:
        candles = candles[:-1]

    return candles


# ============================================================
# INDICATORS
# ============================================================
def ema(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [None] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    prev = seed
    for value in values[period:]:
        prev = (value - prev) * alpha + prev
        result.append(prev)
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(values):
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    if not ema12 or not ema26:
        return None, None

    macd_line = [None] * len(values)
    for i in range(len(values)):
        if ema12[i] is not None and ema26[i] is not None:
            macd_line[i] = ema12[i] - ema26[i]

    usable = [x for x in macd_line if x is not None]
    if len(usable) < 9:
        return None, None

    signal_usable = ema(usable, 9)
    if not signal_usable:
        return None, None

    return usable[-1], signal_usable[-1]


def bollinger(values, period=20, stdevs=2.0):
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    sd = math.sqrt(max(variance, 0.0))
    return mean, mean + stdevs * sd, mean - stdevs * sd


def stochastic(candles, period=14, smooth=3):
    if len(candles) < period + smooth:
        return None
    ks = []
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1:i + 1]
        highest = max(c["high"] for c in window)
        lowest = min(c["low"] for c in window)
        if highest == lowest:
            k = 50.0
        else:
            k = 100.0 * (candles[i]["close"] - lowest) / (highest - lowest)
        ks.append(k)
    return sum(ks[-smooth:]) / smooth


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
    return sum(trs[-period:]) / period


def adx(candles, period=14):
    if len(candles) < period * 2 + 1:
        return None

    trs = []
    plus_dm = []
    minus_dm = []
    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]
        up_move = cur["high"] - prev["high"]
        down_move = prev["low"] - cur["low"]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))

    atr_v = sum(trs[:period]) / period
    plus = sum(plus_dm[:period]) / period
    minus = sum(minus_dm[:period]) / period
    dx_values = []

    for i in range(period, len(trs)):
        atr_v = ((atr_v * (period - 1)) + trs[i]) / period
        plus = ((plus * (period - 1)) + plus_dm[i]) / period
        minus = ((minus * (period - 1)) + minus_dm[i]) / period
        if atr_v == 0:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * plus / atr_v
        minus_di = 100.0 * minus / atr_v
        denom = plus_di + minus_di
        dx_values.append(0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom)

    if len(dx_values) < period:
        return None
    return sum(dx_values[-period:]) / period


def indicator_probability(candles):
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    last = candles[-1]

    value_rsi = rsi(closes, RSI_PERIOD)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    m_line, m_signal = macd(closes)
    bb = bollinger(closes)
    stoch = stochastic(candles)
    adx_v = adx(candles)
    atr_v = atr(candles)

    if value_rsi is None or not e20 or not e50 or m_line is None or m_signal is None:
        return None

    e20_now = e20[-1]
    e20_prev = e20[-4] if len(e20) >= 4 and e20[-4] is not None else e20_now
    e50_now = e50[-1]

    score = 0.0

    # RSI momentum: 0.30 weight-ish.
    score += clamp((value_rsi - 50.0) / 25.0, -1.5, 1.5) * 1.10

    # Trend.
    score += 0.9 if last["close"] > e20_now else -0.9
    score += 0.7 if e20_now > e50_now else -0.7
    score += clamp((e20_now - e20_prev) / max(e20_now * 0.003, 1e-12), -1.0, 1.0) * 0.5

    # MACD.
    macd_hist = m_line - m_signal
    score += clamp(macd_hist / max(abs(m_signal), abs(m_line), 1e-12) * 4.0, -1.2, 1.2) * 0.8

    # Stochastic.
    if stoch is not None:
        score += clamp((stoch - 50.0) / 25.0, -1.2, 1.2) * 0.45

    # Bollinger position.
    if bb:
        mid, upper, lower = bb
        band = max(upper - lower, 1e-12)
        pos = (last["close"] - mid) / band
        score += clamp(pos * 3.0, -1.0, 1.0) * 0.45

    # Volume confirmation.
    vol_base = sum(volumes[-21:-1]) / max(len(volumes[-21:-1]), 1)
    vol_ratio = volumes[-1] / vol_base if vol_base else 1.0
    candle_direction = 1.0 if last["close"] >= last["open"] else -1.0
    score += clamp((vol_ratio - 1.0) * 1.2, -1.0, 1.0) * candle_direction * 0.45

    # ADX strengthens the sign already present.
    if adx_v is not None:
        strength = clamp((adx_v - 15.0) / 25.0, 0.0, 1.0)
        directional = 1.0 if e20_now > e50_now else -1.0
        score += strength * directional * 0.55

    # ATR-normalized candle body provides a short-term push.
    if atr_v and atr_v > 0:
        body = last["close"] - last["open"]
        score += clamp(body / atr_v, -1.0, 1.0) * 0.35

    probability = 50.0 + 40.0 * math.tanh(score / 3.4)
    probability = clamp(probability, 5.0, 95.0)
    return probability



# ============================================================
# 10-SITE EXTERNAL DIRECTION CONSENSUS
# ============================================================
# The sites below publish technical ratings, trend scores or forecast
# direction. They do NOT all publish a calibrated "next candle probability".
# We normalize each available directional reading to 0..100:
# Strong Bull=100, Bull=75, Neutral=50, Bear=25, Strong Bear=0.
# Missing/blocked sources are excluded. No values are fabricated.
#
# IMPORTANT: external sources are queried ONLY after a new RSI-entry signal,
# not for all 100 x 4 Binance pairs.

SITE_TIMEOUT = 8
JINA_READER = "https://r.jina.ai/http://"

COIN_SLUGS = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binance-coin",
    "SOL": "solana", "XRP": "xrp", "DOGE": "dogecoin",
    "ADA": "cardano", "AVAX": "avalanche", "LINK": "chainlink",
    "DOT": "polkadot", "TRX": "tron", "SUI": "sui", "TON": "toncoin",
    "SHIB": "shiba-inu", "LTC": "litecoin", "BCH": "bitcoin-cash",
    "UNI": "uniswap", "AAVE": "aave", "NEAR": "near-protocol",
    "ATOM": "cosmos", "ETC": "ethereum-classic", "FIL": "filecoin",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "INJ": "injective", "PEPE": "pepe", "WIF": "dogwifhat",
    "RENDER": "render-token", "FET": "fetch-ai", "ALGO": "algorand",
    "HBAR": "hedera", "XLM": "stellar", "VET": "vechain",
    "ICP": "internet-computer", "MKR": "maker", "GRT": "the-graph",
    "THETA": "theta", "EGLD": "elrond-egld", "SAND": "the-sandbox",
    "MANA": "decentraland", "AXS": "axie-infinity", "EOS": "eos",
    "XTZ": "tezos", "FLOW": "flow", "QNT": "quant",
    "CRV": "curve-dao-token", "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
}

RATING_VALUES = {
    "strong buy": 100.0, "strong bullish": 100.0, "strong bull": 100.0,
    "bullish": 75.0, "bull": 75.0, "buy": 75.0, "up": 75.0,
    "positive": 70.0, "neutral": 50.0, "sideways": 50.0,
    "range": 50.0, "hold": 50.0, "sell": 25.0, "bearish": 25.0,
    "bear": 25.0, "down": 25.0, "negative": 30.0,
    "strong sell": 0.0, "strong bearish": 0.0, "strong bear": 0.0,
}

def coin_slug(symbol):
    return COIN_SLUGS.get(symbol[:-4].upper(), symbol[:-4].lower())

def strip_html(raw):
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def fetch_page_text(url):
    try:
        r = session.get(
            url,
            timeout=SITE_TIMEOUT,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if r.ok and r.text:
            return strip_html(r.text)[:1_200_000]
    except Exception as exc:
        print(f"Page fetch failed {url}: {exc}")

    try:
        jina_url = JINA_READER + url.replace("https://", "").replace("http://", "")
        r = session.get(
            jina_url,
            timeout=SITE_TIMEOUT,
            headers={"User-Agent": "RSI-Telegram-Bot/10sites"},
        )
        if r.ok and r.text:
            return r.text[:1_200_000]
    except Exception as exc:
        print(f"Reader fetch failed {url}: {exc}")
    return None

def extract_rating(fragment):
    if not fragment:
        return None
    lowered = fragment.lower()
    for phrase in (
        "strong buy", "strong bullish", "strong bull",
        "strong sell", "strong bearish", "strong bear",
        "bullish", "bearish", "buy", "sell", "neutral",
    ):
        if phrase in lowered:
            return RATING_VALUES[phrase]
    return None

def extract_rating_near(text, pattern):
    m = re.search(pattern, text or "", re.I | re.S)
    return extract_rating(m.group(1)) if m else None

def parse_first_number(text, pattern):
    m = re.search(pattern, text or "", re.I | re.S)
    if not m:
        return None
    return safe_float(m.group(1).replace(",", ""))

# 1) TradingView — exact timeframe technical recommendation
def site_tradingview(symbol, timeframe):
    interval = TIMEFRAMES[timeframe]["tv"]
    column = "Recommend.All" if interval == "D" else f"Recommend.All|{interval}"
    try:
        r = session.post(
            "https://scanner.tradingview.com/crypto/scan",
            json={
                "symbols": {
                    "tickers": [f"BINANCE:{symbol}"],
                    "query": {"types": []},
                },
                "columns": [column],
            },
            timeout=SITE_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
            },
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
        values = rows[0].get("d") if rows else None
        raw = safe_float(values[0]) if values else None
        if raw is None:
            return None
        return clamp((raw + 1.0) * 50.0, 0.0, 100.0)
    except Exception as exc:
        print(f"TradingView source error {symbol} {timeframe}: {exc}")
        return None

# 2) Investing.com — 15m / hourly / daily
def site_investing(symbol, timeframe):
    text = fetch_page_text(
        f"https://www.investing.com/crypto/{coin_slug(symbol)}/technical"
    )
    if not text:
        return None

    if timeframe == "15m":
        patterns = [
            r"15\s*min(?:utes?)?.{0,160}?(strong buy|buy|neutral|sell|strong sell)",
        ]
    elif timeframe == "1h":
        patterns = [
            r"hourly.{0,160}?(strong buy|buy|neutral|sell|strong sell)",
            r"1\s*hour.{0,160}?(strong buy|buy|neutral|sell|strong sell)",
        ]
    elif timeframe == "1D":
        patterns = [
            r"daily.{0,160}?(strong buy|buy|neutral|sell|strong sell)",
        ]
    else:
        return None

    for pattern in patterns:
        value = extract_rating_near(text, pattern)
        if value is not None:
            return value
    return None

# 3) Bitget — public technical pages currently expose exact 1D and 4h
def site_bitget(symbol, timeframe):
    slug = coin_slug(symbol)
    base = symbol[:-4].upper()

    if timeframe == "1D":
        text = fetch_page_text(f"https://www.bitget.com/price/{slug}/technical")
        if not text:
            return None
        value = extract_rating_near(
            text,
            rf"According to the {re.escape(base)}\s*1D technical ratings.*?trading signal is\s+(strong buy|buy|neutral|sell|strong sell)",
        )
        if value is not None:
            return value
        return extract_rating_near(
            text,
            r"Technical rating:\s*(strong buy|buy|neutral|sell|strong sell)",
        )

    if timeframe == "4h":
        text = fetch_page_text(f"https://www.bitget.com/price/{slug}")
        if not text:
            return None
        for pattern in (
            rf"{re.escape(base)}\s*4[- ]?hour technical analysis.*?trading signal is\s+(strong buy|buy|neutral|sell|strong sell)",
            rf"{re.escape(base)}\s*4h technical analysis.*?trading signal is\s+(strong buy|buy|neutral|sell|strong sell)",
        ):
            value = extract_rating_near(text, pattern)
            if value is not None:
                return value
    return None

# 4) CoinBeacon — exact timeframe public trend score
def site_coinbeacon(symbol, timeframe):
    tf = {"1D": "1d", "4h": "4h", "1h": "1h", "15m": "15m"}[timeframe]
    url = f"https://api.coinbeacon.io/public/trend-scanner/binance/{quote(symbol)}"
    try:
        r = session.get(url, timeout=SITE_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok:
            return None
        data = r.json()
        item = data.get("item") if isinstance(data, dict) else None
        row = (item or {}).get("scores", {}).get(tf)
        score = safe_float((row or {}).get("score"))
        if score is None:
            return None
        return clamp((score + 100.0) / 2.0, 0.0, 100.0)
    except Exception as exc:
        print(f"CoinBeacon source error {symbol} {timeframe}: {exc}")
        return None

# 5) DYOR — free public preview (top-20 scanner only)
def site_dyor(symbol, timeframe):
    text = fetch_page_text("https://dyor.net/")
    if not text:
        return None
    base = symbol[:-4].upper()
    tf = {"1D": "1d", "4h": "4h", "1h": "1h", "15m": "15m"}[timeframe]
    pos = re.search(rf"\b{re.escape(base)}\b", text, re.I)
    if not pos:
        return None
    fragment = text[max(0, pos.start() - 350):pos.start() + 1800]
    m = re.search(rf"\b{re.escape(tf)}\b\s*([+-]?\d+(?:\.\d+)?)", fragment, re.I)
    if not m:
        return None
    score = safe_float(m.group(1))
    return None if score is None or not -100 <= score <= 100 else (score + 100.0) / 2.0

# 6) CoinCheckup — daily technical trend
def site_coincheckup(symbol, timeframe):
    if timeframe != "1D":
        return None
    text = fetch_page_text(
        f"https://coincheckup.com/coins/{coin_slug(symbol)}/analysis"
    )
    if not text:
        return None
    m = re.search(r"\bTrend\b.{0,180}?\b(Bullish|Bearish|Neutral)\b", text, re.I | re.S)
    if m:
        return RATING_VALUES[m.group(1).lower()]
    m = re.search(r"\bCurrent regime\b.{0,120}?\b(Bull|Bear|Neutral)", text, re.I | re.S)
    if m:
        regime = m.group(1).lower()
        return 75.0 if regime == "bull" else 25.0 if regime == "bear" else 50.0
    return None

# 7) CoinLore — daily technical direction
def site_coinlore(symbol, timeframe):
    if timeframe != "1D":
        return None
    text = fetch_page_text(
        f"https://www.coinlore.com/coin/{coin_slug(symbol)}/forecast/price-prediction"
    )
    if not text:
        return None
    m = re.search(
        r"Of\s+(\d+)\s+technical indicators.*?(\d+)\s+(?:signal )?Buy.*?(\d+)\s+(?:signal )?Sell.*?(\d+)\s+neutral",
        text, re.I | re.S
    )
    if m:
        total = safe_float(m.group(1))
        buys = safe_float(m.group(2))
        neutral = safe_float(m.group(4))
        if total and buys is not None and neutral is not None:
            return clamp((buys + 0.5 * neutral) / total * 100.0, 0.0, 100.0)

    pos = text.lower().find("short-term reading")
    if pos >= 0:
        value = extract_rating(text[pos:pos + 300])
        if value is not None:
            return value
    return None

# 8) CoinCodex — daily current model sentiment
def site_coincodex(symbol, timeframe):
    if timeframe != "1D":
        return None
    text = fetch_page_text(
        f"https://coincodex.com/crypto/{coin_slug(symbol)}/price-prediction/"
    )
    if not text:
        return None
    m = re.search(
        r"\bBullish\b\s*(\d+(?:\.\d+)?)%\s*\bBearish\b\s*(\d+(?:\.\d+)?)%",
        text, re.I
    )
    if m:
        bull = safe_float(m.group(1))
        bear = safe_float(m.group(2))
        if bull is not None and bear is not None and bull + bear > 0:
            return clamp(bull / (bull + bear) * 100.0, 0.0, 100.0)
    m = re.search(
        r"\bSentiment\b\s*[:|]?\s*(Strong bullish|Bullish|Neutral|Bearish|Strong bearish)",
        text, re.I
    )
    return extract_rating(m.group(1)) if m else None

# 9) CryptoPredictions — daily forecast direction
def site_cryptopredictions(symbol, timeframe):
    if timeframe != "1D":
        return None
    text = fetch_page_text(
        f"https://cryptopredictions.com/{coin_slug(symbol)}/"
    )
    if not text:
        return None
    current = parse_first_number(
        text, r"\bPrice\b.{0,100}?\$([0-9][0-9.,]*)\s+24H"
    )
    if current is None:
        current = parse_first_number(
            text, r"Live Price.{0,100}?\$([0-9][0-9.,]*)"
        )
    tomorrow = parse_first_number(
        text, r"end\s+today\s+at\s+\$([0-9][0-9.,]*)"
    )
    if tomorrow is None:
        tomorrow = parse_first_number(
            text, r"end\s+the\s+day\s+at\s+\$([0-9][0-9.,]*)"
        )
    if current is None or tomorrow is None:
        return None
    if tomorrow > current:
        return 100.0
    if tomorrow < current:
        return 0.0
    return 50.0

# 10) PricePredictions — explicit next-hour direction
def site_pricepredictions(symbol, timeframe):
    if timeframe != "1h":
        return None
    text = fetch_page_text(
        f"https://pricepredictions.com/forecast/{coin_slug(symbol)}"
    )
    if not text:
        return None
    m = re.search(r"calling\s+(higher|lower).*?next\s+hour", text, re.I | re.S)
    if not m:
        return None
    return 100.0 if m.group(1).lower() == "higher" else 0.0

EXTERNAL_SOURCES = [
    ("TradingView", site_tradingview),
    ("Investing", site_investing),
    ("Bitget", site_bitget),
    ("CoinBeacon", site_coinbeacon),
    ("DYOR", site_dyor),
    ("CoinCheckup", site_coincheckup),
    ("CoinLore", site_coinlore),
    ("CoinCodex", site_coincodex),
    ("CryptoPredictions", site_cryptopredictions),
    ("PricePredictions", site_pricepredictions),
]

def ten_site_probability(symbol, timeframe):
    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(fn, symbol, timeframe): name
            for name, fn in EXTERNAL_SOURCES
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                value = future.result()
                if value is not None:
                    results.append((name, clamp(float(value), 0.0, 100.0)))
            except Exception as exc:
                print(f"External source error {name} {symbol} {timeframe}: {exc}")

    if not results:
        print(f"10-site consensus: 0/10 for {symbol} {timeframe}")
        return None, 0

    results.sort()
    bull = round(sum(v for _, v in results) / len(results))
    print(
        f"10-site consensus: {len(results)}/10 "
        f"{symbol} {timeframe} -> {bull}% bull"
    )
    print("Sources used:", ", ".join(name for name, _ in results))
    return bull, len(results)

# ============================================================
# POLYMARKET
# ============================================================
def parse_json_field(value, default):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else default
        except Exception:
            return default
    return default


def market_text(market):
    fields = [
        str(market.get("question", "")),
        str(market.get("title", "")),
        str(market.get("slug", "")),
        str(market.get("groupItemTitle", "")),
    ]
    return " ".join(fields).lower()


def market_matches(symbol, timeframe, market):
    base = symbol[:-4].upper()
    aliases = POLY_ALIASES.get(base, [base.lower()])
    text = market_text(market)
    alias_ok = False
    for alias in aliases:
        if re.search(
            r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])",
            text,
        ):
            alias_ok = True
            break
    if not alias_ok:
        return False

    tf_markers = {
        "15m": ["15m", "15 min", "15-minute", "15 minute"],
        "1h": ["1h", "1 hour", "1-hour", "hour"],
        "4h": ["4h", "4 hour", "4-hour"],
        "1D": ["1d", "1 day", "daily", "day"],
    }
    if not any(marker in text for marker in tf_markers[timeframe]):
        return False

    # Prefer explicit Up/Down binary markets.
    if "up or down" not in text and not (" up " in f" {text} " and " down " in f" {text} "):
        return False
    return True


def get_polymarket_market(symbol, timeframe):
    # Scan several recent pages ordered by 24h volume.
    candidates = []
    for offset in (0, 100, 200, 300, 400):
        try:
            response = session.get(
                f"{GAMMA_BASE}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                    "offset": offset,
                    "order": "volume24hr",
                    "ascending": "false",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                break
            candidates.extend(data)
            if len(data) < 100:
                break
        except Exception as exc:
            print(f"Polymarket discovery failed offset={offset}: {exc}")
            break

    matches = [m for m in candidates if market_matches(symbol, timeframe, m)]
    if not matches:
        return None

    matches.sort(
        key=lambda m: safe_float(m.get("volume24hr")) or safe_float(m.get("volumeNum")) or 0.0,
        reverse=True,
    )
    return matches[0]


def polymarket_probability(symbol, timeframe):
    market = get_polymarket_market(symbol, timeframe)
    if not market:
        return None, None

    outcomes = parse_json_field(market.get("outcomes"), [])
    token_ids = parse_json_field(market.get("clobTokenIds"), [])
    prices = parse_json_field(market.get("outcomePrices"), [])

    yes_index = None
    for i, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() in ("up", "yes"):
            yes_index = i
            break

    # Exact token midpoint is preferred when available.
    probability = None
    if yes_index is not None and yes_index < len(token_ids):
        token_id = str(token_ids[yes_index])
        try:
            response = session.get(
                f"{CLOB_BASE}/midpoint",
                params={"token_id": token_id},
                timeout=REQUEST_TIMEOUT,
            )
            if response.ok:
                mid = safe_float((response.json() or {}).get("mid_price"))
                if mid is not None:
                    probability = clamp(mid * 100.0, 0.0, 100.0)
        except Exception:
            pass

    if probability is None and yes_index is not None and yes_index < len(prices):
        probability = safe_float(prices[yes_index])
        if probability is not None:
            probability = clamp(probability * 100.0, 0.0, 100.0)

    slug = market.get("slug")
    if probability is None or not slug:
        return None, None

    return probability, f"https://polymarket.com/event/{slug}"


# ============================================================
# LINKS + MESSAGE
# ============================================================
def tradingview_link(symbol, timeframe):
    tf = TIMEFRAMES[timeframe]["tv"]
    return f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{quote(symbol)}&interval={tf}"


def safe_href(url):
    return html.escape(url, quote=True)


def format_prediction(prob):
    if prob is None:
        return "N/A", "N/A"
    prob = clamp(prob, 0.0, 100.0)
    bull = round(prob)
    bear = 100 - bull
    return f"{bull}%", f"{bear}%"


def build_message(signal):
    bull_a, bear_a = format_prediction(signal.get("indicator_probability"))
    bull_s, bear_s = format_prediction(signal.get("site_probability"))
    site_count = int(signal.get("site_count") or 0)

    site_bull = (
        f"🟢 Bullish: {bull_s}"
        if site_count >= 10
        else f"🟢 Bullish: {bull_s} ({site_count}/10)"
    )

    poly = signal.get("polymarket_url")
    poly_prob = signal.get("polymarket_probability")
    if poly and poly_prob is not None:
        poly_block = (
            f'<a href="{safe_href(poly)}">Bet</a> • '
            f'Up {poly_prob:.0f}% • Down {100.0 - poly_prob:.0f}%'
        )
    elif poly:
        poly_block = f'<a href="{safe_href(poly)}">Bet</a>'
    else:
        poly_block = "N/A"

    tv = f'<a href="{safe_href(signal["tradingview_url"])}">TradingView</a>'

    return (
        f"🚨 <b>{html.escape(signal['symbol'])} • {html.escape(signal['timeframe'])}</b>\n\n"
        f"RSI: {signal['rsi']:.2f} {'🔴' if signal['rsi'] > UPPER_RSI else '🟢'}\n\n"
        f"🎯 Indicators\n"
        f"🟢 Bullish: {bull_a}\n"
        f"🔴 Bearish: {bear_a}\n\n"
        f"🌐 10 Sites\n"
        f"{site_bull}\n"
        f"🔴 Bearish: {bear_s}\n\n"
        f"🎲 Polymarket\n"
        f"{poly_block}\n\n"
        f"📊 TradingView\n"
        f"{tv}"
    )

def crossed_into_zone(previous_rsi, current_rsi):
    if current_rsi is None:
        return False
    if previous_rsi is None:
        return False
    entered_overbought = previous_rsi <= UPPER_RSI and current_rsi > UPPER_RSI
    entered_oversold = previous_rsi >= LOWER_RSI and current_rsi < LOWER_RSI
    return entered_overbought or entered_oversold


def scan_one(symbol, timeframe):
    candles = get_closed_klines(
        symbol,
        TIMEFRAMES[timeframe]["binance"],
        limit=260,
    )
    if not candles:
        return None

    closes = [c["close"] for c in candles]
    current_rsi = rsi(closes, RSI_PERIOD)
    if current_rsi is None:
        return None

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "rsi": current_rsi,
    }


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is missing")
    if not PRIVATE_ACCESS_KEY:
        raise SystemExit("PRIVATE_ACCESS_KEY is missing")

    users = normalize_users(load_json(USERS_FILE, {}))
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    users, state = process_commands(users, state)
    save_json(USERS_FILE, users)
    save_json(STATE_FILE, state)

    active_users = [
        cid for cid, info in users.items()
        if isinstance(info, dict)
        and info.get("authorized")
        and info.get("active")
    ]

    if not active_users:
        print("No active users. Scan skipped.")
        return

    coins = get_top_binance_coins()
    print(f"Top coins loaded: {len(coins)}")
    if not coins:
        print("No Binance coins found. Exiting.")
        return

    # Stage 1: Binance-only RSI scan.
    jobs = [
        (coin["symbol"], timeframe)
        for coin in coins
        for timeframe in TIMEFRAMES
    ]

    candidates = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(scan_one, symbol, timeframe): (symbol, timeframe)
            for symbol, timeframe in jobs
        }
        for future in as_completed(futures):
            symbol, timeframe = futures[future]
            try:
                result = future.result()
                if result:
                    candidates.append(result)
            except Exception as exc:
                print(f"RSI scan error {symbol} {timeframe}: {exc}")

    previous = state.setdefault("rsi", {})
    pending = []

    for result in candidates:
        key = f"{result['symbol']}|{result['timeframe']}"
        old = safe_float(previous.get(key))
        current = result["rsi"]

        if crossed_into_zone(old, current):
            pending.append(result)

        previous[key] = current

    save_json(STATE_FILE, state)

    pending.sort(
        key=lambda x: (
            0, -x["rsi"]
        ) if x["rsi"] > UPPER_RSI else (
            1, x["rsi"]
        )
    )

    print(f"New RSI-entry signals: {len(pending)}")

    # Stage 2: expensive external sites + Polymarket ONLY for new RSI entries.
    for signal in pending:
        symbol = signal["symbol"]
        timeframe = signal["timeframe"]

        indicator_prob = None
        try:
            candles = get_closed_klines(
                symbol,
                TIMEFRAMES[timeframe]["binance"],
                limit=260,
            )
            if candles:
                indicator_prob = indicator_probability(candles)
        except Exception as exc:
            print(f"Indicator model error {symbol} {timeframe}: {exc}")

        site_prob, site_count = ten_site_probability(symbol, timeframe)

        poly_prob = None
        poly_url = None
        try:
            poly_prob, poly_url = polymarket_probability(symbol, timeframe)
        except Exception as exc:
            print(f"Polymarket error {symbol} {timeframe}: {exc}")

        signal.update({
            "indicator_probability": indicator_prob,
            "site_probability": site_prob,
            "site_count": site_count,
            "polymarket_probability": poly_prob,
            "polymarket_url": poly_url,
            "tradingview_url": tradingview_link(symbol, timeframe),
        })

        message = build_message(signal)
        for cid in active_users:
            send_message(cid, message)
            time.sleep(0.15)

    save_json(USERS_FILE, users)
    save_json(STATE_FILE, state)

if __name__ == "__main__":
    main()
