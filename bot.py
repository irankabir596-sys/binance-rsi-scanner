import os
import json
import re
import time
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

BINANCE_BASE = "https://data-api.binance.vision"
TG_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLY_GAMMA = "https://gamma-api.polymarket.com"

TOP_COINS = 100
RSI_PERIOD = 14
STATE_FILE = "state.json"
USERS_FILE = "users.json"
TIMEFRAMES = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1D": "1d"}
TV_INTERVAL = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "1D": "D"}
TIMEOUT = 20
WORKERS = 12
HEADERS = {"User-Agent": "Mozilla/5.0 Binance-RSI-Telegram-Bot/1.0"}

ALIASES = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"], "XRP": ["ripple", "xrp"],
    "DOGE": ["dogecoin", "doge"], "BNB": ["binance coin", "bnb"],
    "ADA": ["cardano", "ada"], "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"], "DOT": ["polkadot", "dot"],
    "TRX": ["tron", "trx"], "SUI": ["sui"], "TON": ["toncoin", "ton"],
    "LTC": ["litecoin", "ltc"], "BCH": ["bitcoin cash", "bch"],
    "SHIB": ["shiba inu", "shib"], "PEPE": ["pepe"],
    "UNI": ["uniswap", "uni"], "ATOM": ["cosmos", "atom"],
    "NEAR": ["near protocol", "near"], "APT": ["aptos", "apt"],
    "ARB": ["arbitrum", "arb"], "OP": ["optimism", "op"],
    "FIL": ["filecoin", "fil"], "AAVE": ["aave"],
    "MKR": ["maker", "mkr"], "ETC": ["ethereum classic", "etc"],
    "XLM": ["stellar", "xlm"], "ALGO": ["algorand", "algo"],
    "ICP": ["internet computer", "icp"], "HBAR": ["hedera", "hbar"],
    "VET": ["vechain", "vet"], "INJ": ["injective", "inj"],
    "SEI": ["sei"], "TIA": ["celestia", "tia"],
    "WIF": ["dogwifhat", "wif"], "BONK": ["bonk"],
}

def now():
    return datetime.now(timezone.utc).isoformat()

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Read {path} error: {e}")
        return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def num(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def normalize_users(raw):
    if not isinstance(raw, dict):
        return {}
    out = {}
    for cid, value in raw.items():
        cid = str(cid)
        if isinstance(value, dict):
            out[cid] = {
                "active": bool(value.get("active", True)),
                "created_at": value.get("created_at", now()),
                "last_seen": value.get("last_seen", now()),
            }
        elif isinstance(value, (int, float, bool)):
            out[cid] = {"active": bool(value), "created_at": now(), "last_seen": now()}
        else:
            out[cid] = {"active": True, "created_at": now(), "last_seen": now()}
    return out

def load_users():
    users = normalize_users(load_json(USERS_FILE, {}))
    save_json(USERS_FILE, users)
    return users

def active_users(users):
    return [str(cid) for cid, v in users.items()
            if isinstance(v, dict) and v.get("active", False)]

def tg(method, data=None):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    r = requests.post(f"{TG_BASE}/{method}", data=data or {}, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(j.get("description", str(j)))
    return j

def send_message(chat_id, text):
    try:
        tg("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True
        })
        return True
    except Exception as e:
        print(f"Telegram send error {chat_id}: {e}")
        return False

def get_updates(offset):
    p = {"limit": 100, "timeout": 1, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        p["offset"] = offset
    try:
        return tg("getUpdates", p).get("result", [])
    except Exception as e:
        print(f"Telegram updates error: {e}")
        return []

def process_commands(users, state):
    offset = state.get("telegram_offset")
    try:
        offset = int(offset) if offset is not None else None
    except Exception:
        offset = None
    updates = get_updates(offset)
    highest = offset - 1 if offset is not None else -1

    for u in updates:
        uid = u.get("update_id")
        if isinstance(uid, int):
            highest = max(highest, uid)
        msg = u.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat_id is None or not text:
            continue
        cmd = text.split()[0].split("@")[0].lower()
        cid = str(chat_id)

        if cmd == "/start":
            users.setdefault(cid, {"active": True, "created_at": now(), "last_seen": now()})
            users[cid]["active"] = True
            users[cid]["last_seen"] = now()
            send_message(chat_id,
                "✅ <b>RSI Bot فعال شد</b>\n\n"
                "سیگنال‌های جدید برای شما ارسال می‌شوند.\n\n"
                "/stop — توقف\n/status — وضعیت")
        elif cmd == "/stop":
            users.setdefault(cid, {"active": False, "created_at": now(), "last_seen": now()})
            users[cid]["active"] = False
            users[cid]["last_seen"] = now()
            send_message(chat_id, "⛔ <b>دریافت سیگنال متوقف شد.</b>\nبرای فعال‌سازی /start را بفرست.")
        elif cmd == "/status":
            status = "🟢 فعال" if cid in users and users[cid].get("active", False) else "🔴 غیرفعال"
            send_message(chat_id, f"📊 <b>وضعیت</b>\n\n{status}\nکاربران فعال: {len(active_users(users))}")

    if highest >= 0:
        state["telegram_offset"] = highest + 1
    return users, state

def binance_get(path, params=None):
    r = requests.get(BINANCE_BASE + path, params=params or {}, headers=HEADERS, timeout=TIMEOUT)
    print(f"Binance {path}: HTTP {r.status_code}")
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("code", 0) < 0:
        raise RuntimeError(data.get("msg", str(data)))
    return data

def get_top_coins():
    try:
        print("Loading Binance Top 100...")
        data = binance_get("/api/v3/ticker/24hr")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected response: {type(data).__name__}")
        coins = []
        bad = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
        for x in data:
            if not isinstance(x, dict):
                continue
            symbol = str(x.get("symbol", "")).upper()
            if not symbol.endswith("USDT") or symbol.endswith(bad):
                continue
            volume = num(x.get("quoteVolume"), 0)
            if volume and volume > 0:
                coins.append({"symbol": symbol, "volume": volume})
        coins.sort(key=lambda x: x["volume"], reverse=True)
        coins = coins[:TOP_COINS]
        print(f"Binance coins loaded: {len(coins)}")
        if not coins:
            raise RuntimeError("No USDT coins found")
        return coins
    except Exception as e:
        print(f"BINANCE ERROR: {repr(e)}")
        traceback.print_exc()
        return []

def klines(symbol, interval):
    return binance_get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": 100})

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    value = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(changes)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        value = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return round(value, 2)

def analyze(symbol, tf):
    data = klines(symbol, TIMEFRAMES[tf])
    if not isinstance(data, list) or len(data) < RSI_PERIOD + 22:
        return None
    closed = data[:-1]
    closes = [num(x[4]) for x in closed]
    volumes = [num(x[5], 0) for x in data]
    if any(x is None for x in closes):
        return None
    cur = rsi(closes, RSI_PERIOD)
    prev = rsi(closes[:-1], RSI_PERIOD)
    if cur is None or prev is None:
        return None
    avg = sum(volumes[-21:-1]) / 20
    vr = volumes[-1] / avg if avg > 0 else 0
    vlabel = "🔥 زیاد" if vr >= 1.5 else ("🟡 متوسط" if vr >= 0.75 else "🟢 کم")
    recent = closes[-6:]
    if recent[-1] > recent[0]:
        trend, te = "صعودی", "🟢"
    elif recent[-1] < recent[0]:
        trend, te = "نزولی", "🔴"
    else:
        trend, te = "خنثی", "⚪"
    return {
        "symbol": symbol, "timeframe": tf, "rsi": cur, "previous_rsi": prev,
        "overbought": prev < 70 <= cur, "oversold": prev > 30 >= cur,
        "volume_ratio": vr, "volume_label": vlabel,
        "trend": trend, "trend_emoji": te,
        "rsi_emoji": "🔴" if cur >= 70 else ("🟢" if cur <= 30 else "⚪")
    }

def scan_coin(symbol):
    out = []
    for tf in TIMEFRAMES:
        try:
            x = analyze(symbol, tf)
            if x:
                out.append(x)
        except Exception as e:
            print(f"Scan error {symbol} {tf}: {e}")
    return out

def parse_value(v):
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v

def market_text(m):
    return " ".join(str(m.get(k, "")) for k in
                    ("question", "description", "slug", "title")).lower()

def tf_match(text, tf):
    patterns = {
        "5m": [r"\b5\s*min", r"\b5\s*minute", r"\b5m\b", "5-minute", "5-min"],
        "15m": [r"\b15\s*min", r"\b15\s*minute", r"\b15m\b", "15-minute", "15-min"],
        "1h": [r"\b1\s*hour", r"\b1\s*hr", r"\b1h\b", "1-hour"],
        "4h": [r"\b4\s*hour", r"\b4\s*hr", r"\b4h\b", "4-hour"],
        "1D": [r"\b1\s*day", r"\b1d\b", r"\bdaily\b", r"\b24\s*hour", "24-hour"],
    }
    return any(re.search(p, text) for p in patterns[tf])

def is_up_down(m):
    t = market_text(m)
    return any(x in t for x in ("up or down", "up/down", "up down", "updown"))

def market_up_probability(m):
    outcomes = parse_value(m.get("outcomes", []))
    prices = parse_value(m.get("outcomePrices", []))
    if not isinstance(outcomes, list) or not isinstance(prices, list):
        return None
    for outcome, price in zip(outcomes, prices):
        if str(outcome).strip().lower() in ("up", "yes"):
            return num(price)
    return None

def load_polymarket():
    markets = []
    try:
        print("Loading active Polymarket markets...")
        for offset in range(0, 1000, 100):
            r = requests.get(
                POLY_GAMMA + "/markets",
                params={"active": "true", "closed": "false", "limit": 100, "offset": offset},
                headers=HEADERS, timeout=TIMEOUT)
            print(f"Polymarket offset {offset}: HTTP {r.status_code}")
            r.raise_for_status()
            page = r.json()
            if isinstance(page, dict):
                page = page.get("data") or page.get("markets") or []
            if not isinstance(page, list):
                break
            markets.extend(page)
            if len(page) < 100:
                break
        print(f"Polymarket markets loaded: {len(markets)}")
        return markets
    except Exception as e:
        print(f"Polymarket discovery error: {e}")
        return []

def attach_matches(markets, symbols):
    for m in markets:
        text = market_text(m)
        matched = set()
        for symbol in symbols:
            base = symbol[:-4].lower() if symbol.endswith("USDT") else symbol.lower()
            aliases = set(ALIASES.get(base.upper(), []))
            aliases.add(base)
            for a in aliases:
                if len(a) <= 2:
                    if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", text):
                        matched.add(symbol)
                        break
                elif a in text:
                    matched.add(symbol)
                    break
        m["_matched"] = matched
    return markets

def build_poly_index(markets):
    idx = {}
    for m in markets:
        if not isinstance(m, dict) or m.get("closed") is True or m.get("active") is False:
            continue
        if not is_up_down(m):
            continue
        text = market_text(m)
        for tf in TIMEFRAMES:
            if tf_match(text, tf):
                for symbol in m.get("_matched", set()):
                    idx.setdefault((symbol, tf), []).append(m)
    return idx

def poly_probability(index, symbol, tf):
    candidates = index.get((symbol, tf), [])
    candidates = sorted(candidates, key=lambda m: num(m.get("volume24hr"), 0) or 0, reverse=True)
    for m in candidates:
        p = market_up_probability(m)
        if p is not None:
            return p
    return None

def score(signal, market_p):
    r = signal["rsi"]
    vr = signal["volume_ratio"]
    s = 2 if r <= 30 else (-2 if r >= 70 else (1 if r < 45 else (-1 if r > 55 else 0)))
    if vr >= 1.5:
        if r <= 30: s += 2
        elif r >= 70: s -= 2
    elif vr >= 0.75:
        if r <= 30: s += 1
        elif r >= 70: s -= 1
    s += 2 if signal["trend"] == "صعودی" else (-2 if signal["trend"] == "نزولی" else 0)
    if market_p is not None:
        if market_p >= 0.60: s += 3
        elif market_p <= 0.40: s -= 3
    if s >= 3: label = "🟢 تمایل صعودی قوی"
    elif s > 0: label = "🟢 تمایل صعودی"
    elif s <= -3: label = "🔴 تمایل نزولی قوی"
    elif s < 0: label = "🔴 تمایل نزولی"
    else: label = "⚪ خنثی"
    return s, label

def make_message(signal, market_p):
    s = signal["symbol"]
    tf = signal["timeframe"]
    if market_p is None:
        mp, me = "N/A", "⚪"
    else:
        mp = f"{market_p * 100:.0f}%"
        me = "🟢" if market_p >= 0.60 else ("🔴" if market_p <= 0.40 else "⚪")
    _, label = score(signal, market_p)
    url = f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{s}&interval={TV_INTERVAL[tf]}"
    return (
        f"<b>{esc(s)} | {esc(tf)}</b>\n\n"
        f"RSI: {signal['rsi']:.1f}       {signal['rsi_emoji']}\n"
        f"Volume: {signal['volume_ratio']:.1f}x    {signal['volume_label']}\n"
        f"Trend: {signal['trend']}    {signal['trend_emoji']}\n"
        f"Market: UP {mp} {me}\n\n"
        f"📊 <b>Signal:</b>\n{label}\n\n"
        f'<a href="{url}">📈 TradingView</a>'
    )

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN secret is missing.")
        return 1

    print("=" * 55)
    print("BINANCE TOP 100 + RSI + POLYMARKET")
    print("=" * 55)

    state = load_json(STATE_FILE, {"rsi": {}, "telegram_offset": None})
    if not isinstance(state, dict):
        state = {"rsi": {}, "telegram_offset": None}
    if not isinstance(state.get("rsi"), dict):
        state["rsi"] = {}

    users = load_users()
    users, state = process_commands(users, state)
    save_json(USERS_FILE, users)

    au = active_users(users)
    print(f"Active users: {len(au)}")
    if not au:
        print("No active users.")
        state["last_run"] = now()
        save_json(STATE_FILE, state)
        return 0

    coins = get_top_coins()
    if not coins:
        print("ERROR: Could not load Binance coins.")
        state["last_run"] = now()
        save_json(STATE_FILE, state)
        return 1

    symbols = [x["symbol"] for x in coins]

    poly_markets = load_polymarket()
    if poly_markets:
        poly_index = build_poly_index(attach_matches(poly_markets, symbols))
        print(f"Polymarket index entries: {len(poly_index)}")
    else:
        poly_index = {}
        print("Polymarket unavailable; Market will show N/A.")

    all_results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(scan_coin, s): s for s in symbols}
        for f in as_completed(futures):
            try:
                all_results.extend(f.result())
            except Exception as e:
                print(f"Worker error {futures[f]}: {e}")

    print(f"Timeframe results: {len(all_results)}")
    new = []
    order = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "1D": 4}

    for x in all_results:
        key = f"{x['symbol']}:{x['timeframe']}"
        old = state["rsi"].get(key)
        if old is None:
            state["rsi"][key] = {"rsi": x["rsi"], "updated_at": now()}
            continue
        old_rsi = num(old.get("rsi") if isinstance(old, dict) else old)
        if old_rsi is None:
            old_rsi = x["rsi"]
        if (old_rsi < 70 <= x["rsi"]) or (old_rsi > 30 >= x["rsi"]):
            new.append(x)
        state["rsi"][key] = {"rsi": x["rsi"], "updated_at": now()}

    new.sort(key=lambda x: (0, -x["rsi"], order[x["timeframe"]]) if x["rsi"] >= 70
             else (1, x["rsi"], order[x["timeframe"]]))

    print(f"New RSI entry signals: {len(new)}")
    sent = 0
    for x in new:
        p = poly_probability(poly_index, x["symbol"], x["timeframe"])
        text = make_message(x, p)
        print(f"SIGNAL {x['symbol']} {x['timeframe']} RSI={x['rsi']} Market={p}")
        for cid in active_users(users):
            if send_message(cid, text):
                sent += 1
            time.sleep(0.05)

    state["last_run"] = now()
    save_json(STATE_FILE, state)
    save_json(USERS_FILE, users)
    print(f"Finished. Signals={len(new)}, messages={sent}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as e:
        print("FATAL ERROR:", repr(e))
        traceback.print_exc()
        raise SystemExit(1)
