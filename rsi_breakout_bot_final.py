import os
import json
import math
import re
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PRIVATE_ACCESS_KEY = os.getenv("PRIVATE_ACCESS_KEY", "").strip()

BINANCE_BASE = os.getenv("BINANCE_BASE", "https://data-api.binance.vision").rstrip("/")
POLYMARKET_BASE = "https://gamma-api.polymarket.com"
TRADINGVIEW_BASE = "https://www.tradingview.com/chart/"

TOP_COINS = 100
RSI_PERIOD = 14
KLINE_LIMIT = 220
TIMEFRAMES = {
    "1D": "1d",
    "4H": "4h",
    "1H": "1h",
    "15M": "15m",
}
STATE_FILE = "state.json"
USERS_FILE = "users.json"
REQUEST_TIMEOUT = 15
MAX_WORKERS = 12

# Assets that are not useful as directional "coins" for this scanner.
STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "USDE", "DAI", "USDP", "BUSD",
    "USD1", "EUR", "TRY", "BRL", "GBP", "AUD", "RUB", "UAH", "PLN",
}
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "RSI-Breakout-Bot/1.0"})


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_json(url, params=None, timeout=REQUEST_TIMEOUT, retries=2):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = SESSION.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url} | {last_error}")


def telegram_request(method, data=None, timeout=REQUEST_TIMEOUT):
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = SESSION.post(url, data=data or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def send_message(chat_id, text):
    try:
        telegram_request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
        return True
    except Exception as exc:
        print(f"Telegram send failed for {chat_id}: {exc}")
        return False


def poll_commands(state, users):
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing.")
        return state, users

    offset = int(state.get("telegram_update_offset", 0))
    try:
        data = telegram_request(
            "getUpdates",
            {"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
            timeout=10,
        )
        updates = data.get("result", []) if isinstance(data, dict) else []
    except Exception as exc:
        print(f"getUpdates failed: {exc}")
        return state, users

    for update in updates:
        update_id = int(update.get("update_id", 0))
        state["telegram_update_offset"] = max(offset, update_id + 1)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = str(chat.get("id", "")).strip()
        text = str(message.get("text", "")).strip()
        if not chat_id or not text:
            continue

        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) == 2 else ""

        if command == "/start":
            if not PRIVATE_ACCESS_KEY or argument != PRIVATE_ACCESS_KEY:
                send_message(chat_id, "❌ Invalid access link.")
                continue
            users[chat_id] = {
                "active": True,
                "username": user.get("username", ""),
                "first_name": user.get("first_name", ""),
                "authorized_at": datetime.now(timezone.utc).isoformat(),
            }
            send_message(chat_id, "✅ Access granted. RSI alerts are active.")

        elif command == "/stop":
            if chat_id in users:
                users[chat_id]["active"] = False
                send_message(chat_id, "✅ Alerts stopped.")
            else:
                send_message(chat_id, "❌ You are not authorized.")

        elif command == "/status":
            active = bool(users.get(chat_id, {}).get("active", False))
            if active:
                send_message(chat_id, "✅ Alerts are active.")
            else:
                send_message(chat_id, "⛔ Alerts are inactive.")

    return state, users


def get_active_users(users):
    return [cid for cid, info in users.items() if info.get("active")]


def get_top_usdt_symbols():
    tickers = get_json(f"{BINANCE_BASE}/api/v3/ticker/24hr")
    candidates = []
    for row in tickers:
        symbol = str(row.get("symbol", ""))
        if not symbol.endswith("USDT"):
            continue
        if symbol.endswith(LEVERAGED_SUFFIXES):
            continue
        base = symbol[:-4]
        if base in STABLE_BASES:
            continue
        try:
            quote_volume = float(row.get("quoteVolume", 0.0))
        except (TypeError, ValueError):
            continue
        if quote_volume <= 0:
            continue
        candidates.append((symbol, quote_volume))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [symbol for symbol, _ in candidates[:TOP_COINS]]


def get_closed_klines(symbol, interval, limit=KLINE_LIMIT):
    rows = get_json(
        f"{BINANCE_BASE}/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    now_ms = int(time.time() * 1000)
    candles = []
    for row in rows:
        if len(row) < 11:
            continue
        close_time = int(row[6])
        if close_time >= now_ms:
            continue
        candles.append(
            {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": close_time,
                "trades": int(row[8]),
                "taker_buy_volume": float(row[9]),
            }
        )
    return candles


def rsi_series(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return []
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    values = [None] * period

    def calc(g, l):
        if l == 0:
            return 100.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))

    values.append(calc(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        values.append(calc(avg_gain, avg_loss))
    return values


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    prev_close = candles[0]["close"]
    for candle in candles[1:]:
        tr = max(
            candle["high"] - candle["low"],
            abs(candle["high"] - prev_close),
            abs(candle["low"] - prev_close),
        )
        trs.append(tr)
        prev_close = candle["close"]
    if len(trs) < period:
        return None
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = ((value * (period - 1)) + tr) / period
    return value


def clamp(value, low, high):
    return max(low, min(high, value))


def sigmoid(x):
    x = clamp(x, -12.0, 12.0)
    return 1.0 / (1.0 + math.exp(-x))


def pivot_levels(candles, left=3, right=3, lookback=120):
    subset = candles[-lookback:]
    highs = []
    lows = []
    n = len(subset)
    for i in range(left, n - right):
        h = subset[i]["high"]
        l = subset[i]["low"]
        if h >= max(subset[j]["high"] for j in range(i - left, i + right + 1)):
            highs.append(h)
        if l <= min(subset[j]["low"] for j in range(i - left, i + right + 1)):
            lows.append(l)
    return highs, lows


def cluster_levels(levels, tolerance):
    if not levels:
        return []
    ordered = sorted(levels)
    clusters = [[ordered[0]]]
    for value in ordered[1:]:
        current = clusters[-1]
        center = sum(current) / len(current)
        if abs(value - center) <= tolerance:
            current.append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def nearest_level(candles, side, price, atr_value):
    highs, lows = pivot_levels(candles)
    recent = candles[-60:]
    fallback_high = max(c["high"] for c in recent) if recent else price
    fallback_low = min(c["low"] for c in recent) if recent else price
    tolerance = max((atr_value or price * 0.005) * 0.35, price * 0.001)

    if side == "resistance":
        candidates = [x for x in highs if x > price * 1.0001]
        if not candidates:
            candidates = [fallback_high] if fallback_high > price else []
        clustered = cluster_levels(candidates, tolerance)
        above = [x for x in clustered if x > price]
        return min(above, key=lambda x: x - price) if above else None

    candidates = [x for x in lows if x < price * 0.9999]
    if not candidates:
        candidates = [fallback_low] if fallback_low < price else []
    clustered = cluster_levels(candidates, tolerance)
    below = [x for x in clustered if x < price]
    return min(below, key=lambda x: price - x) if below else None


def count_level_touches(candles, level, atr_value):
    if not level:
        return 0
    tol = max((atr_value or level * 0.005) * 0.25, level * 0.0008)
    count = 0
    last_touch_index = -999
    for i, candle in enumerate(candles[-100:]):
        touched = candle["low"] <= level + tol and candle["high"] >= level - tol
        if touched and i - last_touch_index >= 4:
            count += 1
            last_touch_index = i
    return count


def breakout_probability(candles, side, level, rsi_value):
    """Model score for the next closed candle crossing the nearby level.

    This is a model-derived probability, not a guarantee or calibrated market odds.
    """
    if not level or len(candles) < 60:
        return None

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    last = candles[-1]
    price = last["close"]
    atr_value = atr(candles, 14) or max(price * 0.005, 1e-12)
    fast = ema(closes[-60:], 20)
    slow = ema(closes[-60:], 50)
    if fast is None or slow is None:
        return None

    distance_atr = abs(level - price) / atr_value
    body = last["close"] - last["open"]
    rng = max(last["high"] - last["low"], 1e-12)
    body_frac = abs(body) / rng
    close_location = (last["close"] - last["low"]) / rng
    avg_volume = sum(volumes[-21:-1]) / max(len(volumes[-21:-1]), 1)
    volume_ratio = last["volume"] / max(avg_volume, 1e-12)

    momentum = (closes[-1] - closes[-6]) / max(atr_value * 2.0, 1e-12)
    trend = (fast - slow) / max(atr_value, 1e-12)
    touches = count_level_touches(candles, level, atr_value)
    touch_factor = clamp((touches - 1) / 5.0, 0.0, 1.0)
    proximity = 1.0 / (1.0 + distance_atr)
    volume_boost = clamp((volume_ratio - 1.0) / 2.0, -1.0, 1.0)

    # Normalize candle pressure in the direction of the expected break.
    if side == "resistance":
        candle_pressure = (1.0 if body > 0 else -1.0) * body_frac
        close_pressure = (close_location - 0.5) * 2.0
        direction_momentum = momentum
        direction_trend = trend
        rsi_pressure = (rsi_value - 50.0) / 50.0
    else:
        candle_pressure = (1.0 if body < 0 else -1.0) * body_frac
        close_pressure = (0.5 - close_location) * 2.0
        direction_momentum = -momentum
        direction_trend = -trend
        rsi_pressure = (50.0 - rsi_value) / 50.0

    # Touches are mildly breakout-positive (tests can weaken a level), but not dominant.
    raw = (
        1.30 * candle_pressure
        + 0.95 * close_pressure
        + 0.75 * clamp(direction_momentum, -1.5, 1.5)
        + 0.60 * clamp(direction_trend, -1.5, 1.5)
        + 0.55 * rsi_pressure
        + 0.45 * volume_boost
        + 0.40 * proximity
        + 0.30 * touch_factor
    )

    probability = sigmoid(raw)
    # Keep probabilities in a useful, non-absolute range.
    return int(round(clamp(probability * 100.0, 5.0, 95.0)))


def format_price(value):
    if value is None:
        return "N/A"
    if value >= 1000:
        return f"{value:.0f}"
    if value >= 1:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if value >= 0.01:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def timeframe_match(text, tf):
    text = text.lower()
    patterns = {
        "15M": ["15m", "15 min", "15-min", "15 minute", "15-minute", "15 minutes"],
        "1H": ["1h", "1 hr", "1-hour", "1 hour", "hourly", "60 min", "60-minute"],
        "4H": ["4h", "4 hr", "4-hour", "4 hour", "240 min", "240-minute"],
        "1D": ["1d", "1 day", "1-day", "1 day", "daily", "day up or down"],
    }
    return any(p in text for p in patterns[tf])


def parse_json_string(value):
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return []


def polymarket_market_url(market):
    event_slug = market.get("eventSlug") or market.get("event_slug")
    events = market.get("events")
    if isinstance(events, list) and events:
        event_slug = event_slug or events[0].get("slug")
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    slug = market.get("slug")
    if slug:
        return f"https://polymarket.com/market/{slug}"
    return None


def find_polymarket_market(symbol, tf):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    queries = [base]
    name_map = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
        "XRP": "XRP",
        "BNB": "BNB",
        "DOGE": "Dogecoin",
        "ADA": "Cardano",
        "AVAX": "Avalanche",
        "LINK": "Chainlink",
        "SUI": "Sui",
    }
    if base in name_map:
        queries.insert(0, name_map[base])

    candidates = []
    for query in queries:
        try:
            data = get_json(
                f"{POLYMARKET_BASE}/public-search",
                {"q": query, "limit": 100},
                timeout=10,
                retries=1,
            )
        except Exception:
            continue
        if isinstance(data, dict):
            raw = []
            for key in ("markets", "events", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    raw.extend(value)
            # Events can contain nested markets.
            for item in list(raw):
                if isinstance(item, dict) and isinstance(item.get("markets"), list):
                    raw.extend(item.get("markets", []))
        elif isinstance(data, list):
            raw = data
        else:
            raw = []

        for market in raw:
            if not isinstance(market, dict):
                continue
            question = str(market.get("question") or market.get("title") or "")
            blob = f"{question} {market.get('description', '')} {market.get('slug', '')}".lower()
            if not timeframe_match(blob, tf):
                continue
            if "up or down" not in blob and not ("up" in blob and "down" in blob):
                continue
            outcomes = [str(x).lower() for x in parse_json_string(market.get("outcomes"))]
            if outcomes and not ("up" in outcomes and "down" in outcomes):
                continue
            if market.get("closed") is True:
                continue
            if market.get("active") is False:
                continue

            score = 0
            if base.lower() in blob:
                score += 8
            if name_map.get(base, "").lower() in blob:
                score += 8
            if timeframe_match(question, tf):
                score += 8
            if "up or down" in question.lower():
                score += 6
            try:
                score += min(5, math.log10(max(float(market.get("volume24hr", 0)), 1.0)))
            except (TypeError, ValueError):
                pass

            end_date = market.get("endDate") or market.get("end_date")
            if end_date:
                try:
                    dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
                    seconds = abs((dt - datetime.now(timezone.utc)).total_seconds())
                    score += max(0.0, 6.0 - min(seconds / 3600.0, 6.0))
                except Exception:
                    pass

            candidates.append((score, market))

    if not candidates:
        # Second pass using /markets with q, useful if public-search response shape changes.
        for query in queries:
            try:
                data = get_json(
                    f"{POLYMARKET_BASE}/markets",
                    {"active": "true", "closed": "false", "limit": 100, "q": query},
                    timeout=10,
                    retries=1,
                )
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for market in data:
                if not isinstance(market, dict):
                    continue
                question = str(market.get("question") or "")
                blob = f"{question} {market.get('description', '')} {market.get('slug', '')}".lower()
                if not timeframe_match(blob, tf):
                    continue
                if "up or down" not in blob and not ("up" in blob and "down" in blob):
                    continue
                candidates.append((1.0, market))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return polymarket_market_url(candidates[0][1])


def tradingview_url(symbol, tf):
    interval = {"15M": "15", "1H": "60", "4H": "240", "1D": "D"}[tf]
    return (
        f"{TRADINGVIEW_BASE}?symbol=BINANCE%3A{symbol}"
        f"&interval={interval}"
    )


def build_alert(symbol, tf, rsi_value, candles):
    closes = [c["close"] for c in candles]
    price = closes[-1]
    atr_value = atr(candles, 14) or max(price * 0.005, 1e-12)

    # Extreme RSI tells us which side of the market is under pressure.
    side = "resistance" if rsi_value > 80 else "support"
    level = nearest_level(candles, side, price, atr_value)
    probability = breakout_probability(candles, side, level, rsi_value) if level else None

    if level is None or probability is None:
        # This branch should be rare; still keep the requested message compact.
        if side == "resistance":
            direction_a = "Breakout"
            direction_b = "Rejection"
        else:
            direction_a = "Breakdown"
            direction_b = "Bounce"
        prob_a = 50
    else:
        direction_a = "Breakout" if side == "resistance" else "Breakdown"
        direction_b = "Rejection" if side == "resistance" else "Bounce"
        prob_a = probability
    prob_b = 100 - prob_a

    level_label = "Resistance" if side == "resistance" else "Support"
    rsi_emoji = "🔴" if rsi_value > 80 else "🟢"
    poly_url = find_polymarket_market(symbol, tf)
    tv_url = tradingview_url(symbol, tf)

    lines = [
        f"🚨 <b>{symbol} • {tf}</b>",
        "",
        f"RSI: {rsi_value:.2f} {rsi_emoji}",
        "",
        f"🎯 {level_label}: {format_price(level)}",
        f"🟢 {direction_a}: {prob_a}%",
        f"🔴 {direction_b}: {prob_b}%",
        "",
    ]

    if poly_url:
        lines.append(f'🎲 <a href="{poly_url}">Bet</a>')
    else:
        lines.append("🎲 Bet: N/A")

    lines.append(f'<a href="{tv_url}">📊</a>')
    return "\n".join(lines)


def process_symbol_timeframe(symbol, tf_label, interval):
    candles = get_closed_klines(symbol, interval)
    if len(candles) < RSI_PERIOD + 10:
        return None
    closes = [c["close"] for c in candles]
    rsis = rsi_series(closes, RSI_PERIOD)
    if not rsis or rsis[-1] is None:
        return None
    current_rsi = float(rsis[-1])
    previous_rsi = float(rsis[-2]) if len(rsis) >= 2 and rsis[-2] is not None else None
    current_zone = "high" if current_rsi > 80 else "low" if current_rsi < 20 else "neutral"
    previous_zone = "high" if previous_rsi is not None and previous_rsi > 80 else "low" if previous_rsi is not None and previous_rsi < 20 else "neutral"
    return {
        "symbol": symbol,
        "tf": tf_label,
        "candles": candles,
        "rsi": current_rsi,
        "zone": current_zone,
        "previous_zone": previous_zone,
        "candle_time": candles[-1]["open_time"],
    }


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is missing.")
    if not PRIVATE_ACCESS_KEY:
        raise SystemExit("PRIVATE_ACCESS_KEY is missing.")

    state = load_json(STATE_FILE, {
        "telegram_update_offset": 0,
        "zones": {},
    })
    users = load_json(USERS_FILE, {})
    state, users = poll_commands(state, users)

    active_users = get_active_users(users)
    if not active_users:
        save_json(STATE_FILE, state)
        save_json(USERS_FILE, users)
        print("No active users.")
        return

    symbols = get_top_usdt_symbols()
    print(f"Scanning {len(symbols)} symbols across {len(TIMEFRAMES)} timeframes...")

    tasks = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for symbol in symbols:
            for tf_label, interval in TIMEFRAMES.items():
                tasks.append(
                    executor.submit(process_symbol_timeframe, symbol, tf_label, interval)
                )

        results = []
        for future in as_completed(tasks):
            try:
                item = future.result()
                if item:
                    results.append(item)
            except Exception as exc:
                print(f"Scan worker failed: {exc}")

    zones = state.setdefault("zones", {})
    alerts_sent = 0

    for item in results:
        key = f"{item['symbol']}|{item['tf']}"
        current_zone = item["zone"]
        old_zone = zones.get(key, {}).get("zone")
        candle_time = item["candle_time"]
        old_candle = zones.get(key, {}).get("candle_time")

        # Send only when RSI enters an extreme zone on a new closed candle.
        entered_extreme = current_zone in ("high", "low") and (
            old_zone != current_zone or old_candle != candle_time
        )
        # If we are already in the same extreme zone on a new candle, do not repeat.
        if old_zone == current_zone and current_zone in ("high", "low"):
            entered_extreme = False

        zones[key] = {
            "zone": current_zone,
            "candle_time": candle_time,
            "rsi": item["rsi"],
        }

        if not entered_extreme:
            continue

        try:
            message = build_alert(item["symbol"], item["tf"], item["rsi"], item["candles"])
        except Exception as exc:
            print(f"Alert build failed for {key}: {exc}")
            continue

        for chat_id in active_users:
            if send_message(chat_id, message):
                alerts_sent += 1

    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    state["last_scan_symbols"] = len(symbols)
    save_json(STATE_FILE, state)
    save_json(USERS_FILE, users)
    print(f"Done. Alerts sent: {alerts_sent}")


if __name__ == "__main__":
    main()
