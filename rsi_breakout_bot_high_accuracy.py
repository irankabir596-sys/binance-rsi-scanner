# RSI + Support/Resistance Breakout Probability Telegram Bot
# Public Binance spot market data + Telegram Bot API.
# No external ML libraries are required.

from __future__ import annotations

import html
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

BINANCE_BASE = "https://data-api.binance.vision"
TELEGRAM_BASE = "https://api.telegram.org"
POLYMARKET_BASE = "https://gamma-api.polymarket.com"
BOT_USERNAME = "ORKATRADE_bot"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PRIVATE_ACCESS_KEY = os.getenv("PRIVATE_ACCESS_KEY", "").strip()

RSI_PERIOD = 14
TOP_COINS = 100
KLINE_LIMIT = 500
LOOKBACK = 140
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_TRAIN_EVENTS = 80
MAX_TRAIN_EVENTS = 15000
K_NEIGHBORS = 120

TIMEFRAMES: Dict[str, str] = {
    "1D": "1d",
    "4H": "4h",
    "1H": "1h",
    "15M": "15m",
}
TF_ORDER = {"1D": 0, "4H": 1, "1H": 2, "15M": 3}
TF_MS = {"1D": 86_400_000, "4H": 14_400_000, "1H": 3_600_000, "15M": 900_000}
TV_INTERVAL = {"1D": "D", "4H": "240", "1H": "60", "15M": "15"}

STATE_PATH = Path("state.json")
USERS_PATH = Path("users.json")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "RSI-Breakout-Bot/1.0"})


# ----------------------------- persistence -----------------------------

def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def default_state() -> Dict[str, Any]:
    return {
        "telegram_update_offset": 0,
        "last_alert_candle": {},
        "last_zone": {},
        "live_outcomes": [],
    }


state: Dict[str, Any] = load_json(STATE_PATH, default_state())
users: Dict[str, Dict[str, Any]] = load_json(USERS_PATH, {})

for key in ("telegram_update_offset", "last_alert_candle", "last_zone", "live_outcomes"):
    state.setdefault(key, default_state()[key])


# ----------------------------- http helpers -----------------------------

def http_get(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Optional[Any]:
    for attempt in range(4):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (418, 429, 500, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                sleep_s = float(retry_after) if retry_after else min(8.0, 1.5 ** attempt)
                time.sleep(sleep_s)
                continue
            return None
        except requests.RequestException:
            time.sleep(min(8.0, 1.5 ** attempt))
    return None


def telegram_call(method: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not BOT_TOKEN:
        return None
    url = f"{TELEGRAM_BASE}/bot{BOT_TOKEN}/{method}"
    try:
        r = SESSION.post(url, data=data, timeout=20)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def send_message(chat_id: str, text: str) -> None:
    telegram_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


# ----------------------------- telegram auth ----------------------------

def process_telegram_updates() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    if not PRIVATE_ACCESS_KEY:
        raise RuntimeError("PRIVATE_ACCESS_KEY is missing")

    offset = int(state.get("telegram_update_offset", 0) or 0)
    result = telegram_call(
        "getUpdates",
        {
            "offset": offset,
            "limit": 100,
            "timeout": 0,
            "allowed_updates": json.dumps(["message"]),
        },
    )
    if not result or not result.get("ok"):
        return

    for update in result.get("result", []):
        uid = int(update.get("update_id", 0))
        state["telegram_update_offset"] = max(uid + 1, int(state["telegram_update_offset"]))
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = str(message.get("text", "")).strip()
        if not chat_id or not text.startswith("/"):
            continue

        command_line = text.split(maxsplit=1)
        command = command_line[0].split("@", 1)[0].lower()
        argument = command_line[1].strip() if len(command_line) == 2 else ""

        if command == "/start":
            if argument and argument == PRIVATE_ACCESS_KEY:
                users[chat_id] = {"enabled": True}
                save_json(USERS_PATH, users)
                send_message(chat_id, "✅ Access granted. RSI alerts are ON.")
            else:
                send_message(chat_id, "❌ Invalid access link.")

        elif command == "/stop":
            if chat_id in users:
                users[chat_id]["enabled"] = False
                save_json(USERS_PATH, users)
                send_message(chat_id, "⏸ Alerts are OFF.")
            else:
                send_message(chat_id, "❌ Access denied.")

        elif command == "/status":
            if chat_id in users and users[chat_id].get("enabled"):
                send_message(chat_id, "✅ Alerts are ON.")
            elif chat_id in users:
                send_message(chat_id, "⏸ Alerts are OFF.")
            else:
                send_message(chat_id, "❌ Access denied.")


def active_chat_ids() -> List[str]:
    return [cid for cid, info in users.items() if isinstance(info, dict) and info.get("enabled")]


# ----------------------------- Binance data ------------------------------

def binance_exchange_info() -> Tuple[List[str], Dict[str, float]]:
    data = http_get(f"{BINANCE_BASE}/api/v3/exchangeInfo", timeout=20)
    if not isinstance(data, dict):
        raise RuntimeError("Could not load Binance exchangeInfo")

    symbols: List[str] = []
    tick_sizes: Dict[str, float] = {}
    for item in data.get("symbols", []):
        symbol = str(item.get("symbol", ""))
        if item.get("status") != "TRADING":
            continue
        if item.get("quoteAsset") != "USDT":
            continue
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        if base in {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "BUSD"}:
            continue
        if any(x in base for x in ("UP", "DOWN", "BULL", "BEAR")):
            continue
        symbols.append(symbol)
        for flt in item.get("filters", []):
            if flt.get("filterType") == "PRICE_FILTER":
                try:
                    tick_sizes[symbol] = float(flt.get("tickSize", "0.00000001"))
                except (TypeError, ValueError):
                    tick_sizes[symbol] = 1e-8
                break
    return symbols, tick_sizes


def top_symbols(all_symbols: Sequence[str]) -> List[str]:
    data = http_get(f"{BINANCE_BASE}/api/v3/ticker/24hr", timeout=20)
    if not isinstance(data, list):
        raise RuntimeError("Could not load Binance 24h ticker")
    allowed = set(all_symbols)
    rows: List[Tuple[str, float]] = []
    for item in data:
        s = str(item.get("symbol", ""))
        if s not in allowed:
            continue
        try:
            qv = float(item.get("quoteVolume", 0.0))
        except (TypeError, ValueError):
            qv = 0.0
        rows.append((s, qv))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:TOP_COINS]]


def get_klines(symbol: str, interval: str) -> Optional[List[List[float]]]:
    raw = http_get(
        f"{BINANCE_BASE}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": KLINE_LIMIT},
        timeout=15,
    )
    if not isinstance(raw, list) or len(raw) < RSI_PERIOD + 5:
        return None

    now_ms = int(time.time() * 1000)
    interval_ms = next((ms for label, ms in TF_MS.items() if TIMEFRAMES[label] == interval), 0)
    rows: List[List[float]] = []
    for row in raw:
        try:
            open_ms = int(row[0])
            close_ms = int(row[6])
            # Ignore the currently-forming candle.
            if close_ms >= now_ms or (interval_ms and open_ms + interval_ms > now_ms):
                continue
            rows.append([
                float(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            ])
        except (TypeError, ValueError, IndexError):
            continue
    return rows if len(rows) >= RSI_PERIOD + 5 else None


def fetch_all_market_data(symbols: Sequence[str]) -> Dict[Tuple[str, str], List[List[float]]]:
    out: Dict[Tuple[str, str], List[List[float]]] = {}
    jobs: Dict[Any, Tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for symbol in symbols:
            for tf_label, interval in TIMEFRAMES.items():
                fut = ex.submit(get_klines, symbol, interval)
                jobs[fut] = (symbol, tf_label)
        for fut in as_completed(jobs):
            symbol, tf_label = jobs[fut]
            try:
                data = fut.result()
            except Exception:
                data = None
            if data:
                out[(symbol, tf_label)] = data
    return out


# ----------------------------- indicators --------------------------------

def rsi_values(closes: Sequence[float], period: int = RSI_PERIOD) -> List[Optional[float]]:
    n = len(closes)
    rsis: List[Optional[float]] = [None] * n
    if n <= period:
        return rsis

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains[i] = max(diff, 0.0)
        losses[i] = max(-diff, 0.0)

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    if avg_loss == 0:
        rsis[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsis[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period + 1, n):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        if avg_loss == 0:
            rsis[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsis[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsis


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def true_range(candles: Sequence[List[float]], i: int) -> float:
    if i <= 0:
        return candles[i][2] - candles[i][3]
    high = candles[i][2]
    low = candles[i][3]
    prev_close = candles[i - 1][4]
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr_values(candles: Sequence[List[float]], period: int = 14) -> List[Optional[float]]:
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out
    trs = [true_range(candles, i) for i in range(n)]
    seed = sum(trs[1 : period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = ((prev * (period - 1)) + trs[i]) / period
        out[i] = prev
    return out


def volume_ratio(candles: Sequence[List[float]], i: int, period: int = 20) -> float:
    start = max(0, i - period)
    vals = [c[5] for c in candles[start:i] if c[5] > 0]
    if not vals:
        return 1.0
    avg = sum(vals) / len(vals)
    return candles[i][5] / avg if avg else 1.0


def candle_features(candles: Sequence[List[float]], i: int, atr: float, ema20: Optional[float], ema50: Optional[float], rsi: float) -> List[float]:
    o, h, l, c, _ = candles[i][1], candles[i][2], candles[i][3], candles[i][4], candles[i][0]
    rng = max(h - l, 1e-12)
    body = abs(c - o) / rng
    close_loc = ((c - l) / rng) * 2.0 - 1.0
    upper_wick = max(h - max(o, c), 0.0) / rng
    lower_wick = max(min(o, c) - l, 0.0) / rng
    vr = max(volume_ratio(candles, i), 0.01)
    if i >= 3:
        momentum = (c - candles[i - 3][4]) / max(atr, 1e-12)
    else:
        momentum = 0.0
    ema_gap = 0.0
    trend = 0.0
    if ema20 is not None and ema50 is not None:
        ema_gap = (ema20 - ema50) / max(atr, 1e-12)
        trend = (c - ema50) / max(atr, 1e-12)
    atr_pct = (atr / max(c, 1e-12)) * 100.0

    # Features are deliberately bounded/normalized for KNN distance.
    return [
        min(max(body, 0.0), 1.0),
        min(max(close_loc, -1.0), 1.0),
        min(max(upper_wick, 0.0), 1.5),
        min(max(lower_wick, 0.0), 1.5),
        min(max(math.log1p(vr), 0.0), 2.5),
        min(max((rsi - 50.0) / 25.0, -2.0), 2.0),
        min(max(momentum / 2.0, -3.0), 3.0),
        min(max(ema_gap / 2.0, -3.0), 3.0),
        min(max(trend / 3.0, -3.0), 3.0),
        min(max(atr_pct / 2.0, 0.0), 3.0),
    ]


# ------------------------- support/resistance ---------------------------

def pivot_indices(candles: Sequence[List[float]]) -> Tuple[List[int], List[int]]:
    highs: List[int] = []
    lows: List[int] = []
    n = len(candles)
    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        hi = candles[i][2]
        lo = candles[i][3]
        if all(hi >= candles[j][2] for j in range(i - PIVOT_LEFT, i)) and all(hi > candles[j][2] for j in range(i + 1, i + PIVOT_RIGHT + 1)):
            highs.append(i)
        if all(lo <= candles[j][3] for j in range(i - PIVOT_LEFT, i)) and all(lo < candles[j][3] for j in range(i + 1, i + PIVOT_RIGHT + 1)):
            lows.append(i)
    return highs, lows


def cluster_levels(points: List[Tuple[int, float]], tolerance: float, max_points: int = 40) -> List[Tuple[float, int, float]]:
    if not points:
        return []
    pts = sorted(points, key=lambda x: x[1])
    clusters: List[List[Tuple[int, float]]] = []
    for idx, price in pts:
        if not clusters or abs(price - (sum(x[1] for x in clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([(idx, price)])
        else:
            clusters[-1].append((idx, price))
    now_idx = max(idx for idx, _ in points)
    scored: List[Tuple[float, int, float]] = []
    for cl in clusters:
        weights = []
        for idx, price in cl:
            age = max(1, now_idx - idx)
            w = 1.0 + 2.0 / math.sqrt(age)
            weights.append((price, w))
        level = sum(p * w for p, w in weights) / sum(w for _, w in weights)
        touch_count = len(cl)
        recency = max(0.0, 1.0 - (now_idx - max(idx for idx, _ in cl)) / max(1, LOOKBACK))
        score = touch_count + recency
        scored.append((level, touch_count, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:max_points]


def nearest_level(
    candles: Sequence[List[float]],
    idx: int,
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    if idx < 20:
        return None, None, None
    atrs = atr_values(candles)
    atr = atrs[idx] or max(candles[idx][2] - candles[idx][3], candles[idx][4] * 0.001)
    start = max(PIVOT_RIGHT + PIVOT_LEFT + 2, idx - LOOKBACK)
    highs, lows = pivot_indices(candles[:idx])
    highs = [p for p in highs if p >= start and p + PIVOT_RIGHT < idx]
    lows = [p for p in lows if p >= start and p + PIVOT_RIGHT < idx]
    price = candles[idx][4]
    tolerance = max(atr * 0.25, price * 0.002)
    resist = cluster_levels([(p, candles[p][2]) for p in highs], tolerance)
    support = cluster_levels([(p, candles[p][3]) for p in lows], tolerance)
    r_levels = sorted([x[0] for x in resist if x[0] > price])
    s_levels = sorted([x[0] for x in support if x[0] < price], reverse=True)
    near_r = r_levels[0] if r_levels else None
    near_s = s_levels[0] if s_levels else None

    candidates: List[Tuple[float, str, float]] = []
    max_distance = max(2.5 * atr, price * 0.012)
    if near_r is not None and near_r - price <= max_distance:
        candidates.append((near_r - price, "Resistance", near_r))
    if near_s is not None and price - near_s <= max_distance:
        candidates.append((price - near_s, "Support", near_s))
    if not candidates:
        # Fall back to the nearest level even if it is a little farther away.
        if near_r is not None:
            candidates.append((near_r - price, "Resistance", near_r))
        if near_s is not None:
            candidates.append((price - near_s, "Support", near_s))
    if not candidates:
        return None, None, None
    candidates.sort(key=lambda x: x[0])
    _, side, level = candidates[0]
    return side, level, atr


# -------------------------- breakout model ------------------------------

Example = Tuple[List[float], int, int, float, str]


def collect_examples(
    market_data: Dict[Tuple[str, str], List[List[float]],],
    tf_label: str,
) -> List[Example]:
    # limit annotation is intentional: type checker-friendly runtime code.
    examples: List[Example] = []
    for (symbol, tf), candles in market_data.items():
        if tf != tf_label or len(candles) < 80:
            continue
        rsis = rsi_values([c[4] for c in candles])
        atrs = atr_values(candles)
        e20 = ema([c[4] for c in candles], 20)
        e50 = ema([c[4] for c in candles], 50)
        highs, lows = pivot_indices(candles)
        high_set = set(highs)
        low_set = set(lows)
        for i in range(35, len(candles) - 1):
            rsi = rsis[i]
            atr = atrs[i]
            if rsi is None or atr is None or atr <= 0:
                continue
            price = candles[i][4]
            # Only use pivots confirmed before the event candle.
            past_highs = [p for p in highs if p + PIVOT_RIGHT < i and p >= max(5, i - LOOKBACK)]
            past_lows = [p for p in lows if p + PIVOT_RIGHT < i and p >= max(5, i - LOOKBACK)]
            if not past_highs and not past_lows:
                continue
            tolerance = max(atr * 0.25, price * 0.002)
            resist_clusters = cluster_levels([(p, candles[p][2]) for p in past_highs], tolerance)
            support_clusters = cluster_levels([(p, candles[p][3]) for p in past_lows], tolerance)
            rs = sorted([x[0] for x in resist_clusters if x[0] >= price], key=lambda x: x - price)
            ss = sorted([x[0] for x in support_clusters if x[0] <= price], key=lambda x: price - x)
            side: Optional[str] = None
            level: Optional[float] = None
            if rs and rs[0] - price <= max(1.5 * atr, price * 0.008) and candles[i][2] >= rs[0] - tolerance:
                side, level = "Resistance", rs[0]
            elif ss and price - ss[0] <= max(1.5 * atr, price * 0.008) and candles[i][3] <= ss[0] + tolerance:
                side, level = "Support", ss[0]
            if side is None or level is None:
                continue

            # Avoid cases where the event candle is already clearly across the level.
            buffer = 0.10 * atr
            if side == "Resistance" and price > level + buffer:
                continue
            if side == "Support" and price < level - buffer:
                continue

            next_close = candles[i + 1][4]
            breakout = 1 if (next_close > level + 0.05 * atr if side == "Resistance" else next_close < level - 0.05 * atr) else 0
            features = candle_features(candles, i, atr, e20[i], e50[i], rsi)
            # Add level-distance and approximate touch strength.
            dist_atr = abs(level - price) / max(atr, 1e-12)
            features = features + [min(max(dist_atr, 0.0), 3.0)]
            examples.append((features, breakout, int(candles[i][0]), float(level), side))

    examples.sort(key=lambda x: x[2])
    if len(examples) > MAX_TRAIN_EVENTS:
        examples = examples[-MAX_TRAIN_EVENTS:]
    return examples


def knn_raw_probability(train: List[Example], query_features: List[float], side: str, k: int = K_NEIGHBORS) -> Tuple[float, int, float]:
    same = [e for e in train if e[4] == side]
    if not same:
        return 0.5, 0, 0.0
    distances: List[Tuple[float, int]] = []
    for idx, e in enumerate(same):
        f = e[0]
        d = 0.0
        for a, b in zip(f, query_features):
            diff = a - b
            d += diff * diff
        distances.append((math.sqrt(d), idx))
    distances.sort(key=lambda x: x[0])
    picked = distances[: min(k, len(distances))]
    weights: List[float] = []
    ys: List[int] = []
    for d, idx in picked:
        w = 1.0 / (0.18 + d)
        weights.append(w)
        ys.append(same[idx][1])
    total_w = sum(weights)
    raw = sum(w * y for w, y in zip(weights, ys)) / total_w if total_w else 0.5
    ess = (total_w * total_w) / max(1e-12, sum(w * w for w in weights))
    return raw, len(same), ess


def fit_logit_calibration(train: List[Example], validation: List[Example]) -> Tuple[float, float]:
    if len(train) < MIN_TRAIN_EVENTS or len(validation) < 60:
        return 0.0, 1.0
    xs: List[Tuple[float, int]] = []
    step = max(1, len(validation) // 250)
    for e in validation[::step]:
        raw, _, ess = knn_raw_probability(train, e[0], e[4])
        # Discard very low-information validation points.
        if ess < 20:
            continue
        p = min(0.995, max(0.005, raw))
        x = math.log(p / (1.0 - p))
        xs.append((x, e[1]))
    if len(xs) < 30:
        return 0.0, 1.0

    # Regularized logistic calibration: sigmoid(a + b*x).
    a = 0.0
    b = 1.0
    for _ in range(25):
        g0 = -0.02 * a
        g1 = -0.02 * (b - 1.0)
        h00 = 0.02
        h11 = 0.02
        h01 = 0.0
        for x, y in xs:
            z = max(-20.0, min(20.0, a + b * x))
            p = 1.0 / (1.0 + math.exp(-z))
            w = p * (1.0 - p)
            err = y - p
            g0 += err
            g1 += err * x
            h00 += w
            h11 += w * x * x
            h01 += w * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-9:
            break
        da = (g0 * h11 - g1 * h01) / det
        db = (g1 * h00 - g0 * h01) / det
        a += da
        b += db
        b = min(2.5, max(0.35, b))
        if abs(da) + abs(db) < 1e-4:
            break
    return a, b


def calibrate_probability(raw: float, a: float, b: float, ess: float) -> float:
    p = min(0.995, max(0.005, raw))
    logit = math.log(p / (1.0 - p))
    z = max(-20.0, min(20.0, a + b * logit))
    calibrated = 1.0 / (1.0 + math.exp(-z))
    # Conservative shrink for small effective sample size.
    shrink = max(0.0, min(1.0, ess / (ess + 30.0)))
    calibrated = 0.5 + shrink * (calibrated - 0.5)
    return min(0.99, max(0.01, calibrated))


def build_models(market_data: Dict[Tuple[str, str], List[List[float]]]) -> Dict[str, Dict[str, Any]]:
    models: Dict[str, Dict[str, Any]] = {}
    for tf in TIMEFRAMES:
        examples = collect_examples(market_data, tf)
        if len(examples) < MIN_TRAIN_EVENTS:
            models[tf] = {"examples": examples, "a": 0.0, "b": 1.0}
            continue
        split = max(MIN_TRAIN_EVENTS, int(len(examples) * 0.80))
        split = min(split, len(examples) - 30)
        train = examples[:split]
        val = examples[split:]
        a, b = fit_logit_calibration(train, val)
        models[tf] = {"examples": examples, "a": a, "b": b}
    return models


def predict_breakout(
    model: Dict[str, Any],
    candles: Sequence[List[float]],
    idx: int,
    side: str,
    level: float,
    atr: float,
    rsi: float,
) -> float:
    e20 = ema([c[4] for c in candles], 20)
    e50 = ema([c[4] for c in candles], 50)
    features = candle_features(candles, idx, atr, e20[idx], e50[idx], rsi)
    dist_atr = abs(level - candles[idx][4]) / max(atr, 1e-12)
    features += [min(max(dist_atr, 0.0), 3.0)]
    examples = model.get("examples", [])
    if len(examples) < MIN_TRAIN_EVENTS:
        return 0.5
    raw, _, ess = knn_raw_probability(examples, features, side)
    return calibrate_probability(raw, float(model.get("a", 0.0)), float(model.get("b", 1.0)), ess)


# ---------------------------- Polymarket --------------------------------

def normalize_text(value: str) -> str:
    value = value.lower()
    value = value.replace("bitcoin", "btc")
    value = value.replace("ethereum", "eth")
    value = value.replace("solana", "sol")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def timeframe_matches(text: str, tf: str) -> bool:
    t = normalize_text(text)
    if tf == "15M":
        return "15m" in t or "15 minute" in t or "15 min" in t
    if tf == "1H":
        return "1h" in t or "1 hour" in t or "1 hr" in t
    if tf == "4H":
        return "4h" in t or "4 hour" in t or "4 hr" in t
    if tf == "1D":
        return "1d" in t or "1 day" in t or "daily" in t
    return False


def symbol_aliases(symbol: str) -> List[str]:
    base = symbol[:-4]
    aliases = [base.lower()]
    names = {
        "BTC": ["btc", "bitcoin"],
        "ETH": ["eth", "ethereum"],
        "SOL": ["sol", "solana"],
        "XRP": ["xrp", "ripple"],
        "DOGE": ["doge", "dogecoin"],
        "ADA": ["ada", "cardano"],
        "BNB": ["bnb", "binance coin"],
        "AVAX": ["avax", "avalanche"],
        "LINK": ["link", "chainlink"],
        "DOT": ["dot", "polkadot"],
        "LTC": ["ltc", "litecoin"],
        "BCH": ["bch", "bitcoin cash"],
        "SHIB": ["shib", "shiba inu"],
        "SUI": ["sui"],
    }
    return names.get(base, aliases)


def fetch_polymarket_events() -> List[Dict[str, Any]]:
    # Gamma is used only as a public discovery source; if unavailable, Bet is omitted.
    data = http_get(
        f"{POLYMARKET_BASE}/markets",
        params={"active": "true", "closed": "false", "limit": 1000},
        timeout=20,
    )
    return data if isinstance(data, list) else []


def polymarket_link(markets: Sequence[Dict[str, Any]], symbol: str, tf: str) -> Optional[str]:
    aliases = symbol_aliases(symbol)
    best: Optional[Tuple[int, str]] = None
    for m in markets:
        if not isinstance(m, dict):
            continue
        if m.get("closed") is True or m.get("active") is False:
            continue
        text = " ".join(
            str(m.get(k, ""))
            for k in ("question", "title", "groupItemTitle", "slug", "eventSlug")
        )
        nt = normalize_text(text)
        if not timeframe_matches(text, tf):
            continue
        if not any(alias in nt.split() or alias in nt for alias in aliases):
            continue
        if not re.search(r"\b(up|down|up or down|higher|lower)\b", nt):
            # Keep flexible for market naming, but favor explicit up/down markets.
            score = 1
        else:
            score = 4
        if "eventSlug" in m and m.get("eventSlug"):
            slug = str(m.get("eventSlug"))
            url = f"https://polymarket.com/event/{slug}"
        elif m.get("slug"):
            slug = str(m.get("slug"))
            url = f"https://polymarket.com/market/{slug}"
        else:
            continue
        # Prefer the closest expiry by market metadata when available.
        end_date = str(m.get("endDate") or m.get("end_date") or "")
        score2 = score
        if end_date:
            score2 += 1
        candidate = (score2, url)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else None


# ---------------------------- links / format ----------------------------

def html_link(text: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def price_decimals(tick_size: float) -> int:
    if tick_size >= 1:
        return 0
    d = 0
    x = tick_size
    while d < 12 and abs(x - round(x)) > 1e-12:
        x *= 10
        d += 1
    return d


def format_price(value: float, tick_size: float) -> str:
    d = price_decimals(tick_size)
    return f"{value:.{d}f}".rstrip("0").rstrip(".") if d > 0 else f"{value:.0f}"


def tradingview_url(symbol: str, tf: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}&interval={TV_INTERVAL[tf]}"


# ------------------------------ scanning --------------------------------

def current_rsi_entry(candles: Sequence[List[float]]) -> Optional[Tuple[int, float]]:
    rsis = rsi_values([c[4] for c in candles])
    if len(rsis) < 2:
        return None
    cur = rsis[-1]
    prev = rsis[-2]
    if cur is None or prev is None:
        return None
    entered_high = prev <= 80.0 and cur > 80.0
    entered_low = prev >= 20.0 and cur < 20.0
    if entered_high or entered_low:
        return len(candles) - 1, cur
    return None


def signal_for(
    symbol: str,
    tf: str,
    candles: List[List[float]],
    model: Dict[str, Any],
    tick_size: float,
    markets: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    entry = current_rsi_entry(candles)
    if entry is None:
        return None
    idx, rsi = entry
    candle_key = str(int(candles[idx][0]))
    state_key = f"{symbol}|{tf}"
    if state.get("last_alert_candle", {}).get(state_key) == candle_key:
        return None

    side, level, atr = nearest_level(candles, idx)
    if side is None or level is None or atr is None:
        state.setdefault("last_alert_candle", {})[state_key] = candle_key
        return None

    prob = predict_breakout(model, candles, idx, side, level, atr, rsi)
    if side == "Resistance":
        primary_label = "Breakout"
        secondary_label = "Rejection"
    else:
        primary_label = "Breakdown"
        secondary_label = "Bounce"

    poly = polymarket_link(markets, symbol, tf)
    tv = tradingview_url(symbol, tf)
    level_text = format_price(level, tick_size)
    return {
        "symbol": symbol,
        "tf": tf,
        "rsi": rsi,
        "zone": "high" if rsi > 80 else "low",
        "side": side,
        "level": level_text,
        "prob": prob,
        "primary_label": primary_label,
        "secondary_label": secondary_label,
        "polymarket": poly,
        "tradingview": tv,
        "candle_key": candle_key,
        "state_key": state_key,
    }


def render_signal(sig: Dict[str, Any]) -> str:
    p = int(round(sig["prob"] * 100))
    q = 100 - p
    rsi_emoji = "🔴" if sig["rsi"] > 80 else "🟢"
    lines = [
        f"🚨 {html.escape(sig['symbol'])} • {sig['tf']}",
        "",
        f"RSI: {sig['rsi']:.2f} {rsi_emoji}",
        "",
        f"🎯 {sig['side']}: {sig['level']}",
        f"🟢 {sig['primary_label']}: {p}%",
        f"🔴 {sig['secondary_label']}: {q}%",
    ]
    if sig.get("polymarket"):
        lines += ["", html_link("🎲 Bet", sig["polymarket"])]
    lines += ["", html_link("📊 Trading view", sig["tradingview"])]
    return "\n".join(lines)


def sort_signals(signals: List[Dict[str, Any]]) -> None:
    # Primary: RSI value ascending. Secondary: requested timeframe order.
    signals.sort(key=lambda s: (float(s["rsi"]), TF_ORDER.get(s["tf"], 99), s["symbol"]))


def send_signal_messages(signals: List[Dict[str, Any]], chat_ids: Sequence[str]) -> None:
    if not signals:
        return
    sort_signals(signals)
    blocks = [render_signal(s) for s in signals]
    # Telegram message limit is safely handled by splitting between signal blocks.
    chunks: List[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) > 3900 and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    for cid in chat_ids:
        for chunk in chunks:
            send_message(cid, chunk)


# ------------------------------ main -----------------------------------

def main() -> None:
    process_telegram_updates()
    save_json(STATE_PATH, state)

    chat_ids = active_chat_ids()
    if not chat_ids:
        save_json(USERS_PATH, users)
        return

    all_symbols, tick_sizes = binance_exchange_info()
    symbols = top_symbols(all_symbols)
    if not symbols:
        return

    market_data = fetch_all_market_data(symbols)

    # First find fresh RSI entries. Build the heavier breakout models only
    # for timeframes that actually have at least one new signal.
    fresh_candidates: List[Tuple[str, str]] = []
    for symbol in symbols:
        for tf in TIMEFRAMES:
            candles = market_data.get((symbol, tf))
            if not candles or len(candles) < 60:
                continue
            if current_rsi_entry(candles) is not None:
                state_key = f"{symbol}|{tf}"
                candle_key = str(int(candles[-1][0]))
                if state.get("last_alert_candle", {}).get(state_key) != candle_key:
                    fresh_candidates.append((symbol, tf))

    if not fresh_candidates:
        save_json(STATE_PATH, state)
        save_json(USERS_PATH, users)
        return

    active_tfs = sorted({tf for _, tf in fresh_candidates}, key=lambda x: TF_ORDER[x])
    models = build_models({k: v for k, v in market_data.items() if k[1] in active_tfs})

    # Build predictions first. Polymarket discovery is done only when there
    # is an actual alert to link, reducing unnecessary network traffic.
    empty_markets: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    for symbol, tf in fresh_candidates:
        candles = market_data.get((symbol, tf))
        if not candles:
            continue
        sig = signal_for(symbol, tf, candles, models[tf], tick_sizes.get(symbol, 1e-8), empty_markets)
        if sig:
            signals.append(sig)
            state.setdefault("last_alert_candle", {})[sig["state_key"]] = sig["candle_key"]
            state.setdefault("last_zone", {})[sig["state_key"]] = "high" if sig["rsi"] > 80 else "low"

    if signals:
        markets = fetch_polymarket_events()
        for sig in signals:
            sig["polymarket"] = polymarket_link(markets, sig["symbol"], sig["tf"])

    save_json(STATE_PATH, state)
    save_json(USERS_PATH, users)
    send_signal_messages(signals, chat_ids)


if __name__ == "__main__":
    main()
