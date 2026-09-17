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
            label = "🔥"
        elif ratio >= 0.75:
            label = "🟡"
        else:
            label = "🟢"

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
            return "صعودی", "🟢"
        if last < first:
            return "نزولی", "🔴"

        return "خنثی", "⚪"

    except Exception:
        return "خنثی", "⚪"


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
# TRADINGVIEW-STYLE TECHNICAL RATING
# ============================================================

def _sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None

def _ema(v, n):
    if len(v) < n: return None
    x=sum(v[:n])/n
    k=2/(n+1)
    for z in v[n:]: x=(z-x)*k+x
    return x

def _wma(v, n):
    if len(v)<n: return None
    a=v[-n:]; d=n*(n+1)/2
    return sum(x*(i+1) for i,x in enumerate(a))/d

def _hma(v, n=9):
    if len(v)<n: return None
    half=n//2; root=max(1,int(n**0.5)); raw=[]
    for i in range(n-1,len(v)):
        a=v[:i+1]
        wh=_wma(a,half); wf=_wma(a,n)
        if wh is not None and wf is not None: raw.append(2*wh-wf)
    return _wma(raw,root)

def _cci(h,l,c,n=20):
    if len(c)<n: return None
    t=[(a+b+d)/3 for a,b,d in zip(h,l,c)][-n:]
    m=sum(t)/n; dev=sum(abs(x-m) for x in t)/n
    return 0 if dev==0 else (t[-1]-m)/(0.015*dev)

def _stoch(h,l,c,n=14):
    if len(c)<n:return None
    hi=max(h[-n:]); lo=min(l[-n:])
    return 50 if hi==lo else 100*(c[-1]-lo)/(hi-lo)

def _macd(c):
    if len(c)<40:return None
    fast=[]; slow=[]
    for i in range(25,len(c)):
        fast.append(_ema(c[:i+1],12)); slow.append(_ema(c[:i+1],26))
    m=[a-b for a,b in zip(fast[-len(slow):],slow)]
    if len(m)<9:return None
    return m[-1],_ema(m,9)

def _ao(h,l):
    m=[(a+b)/2 for a,b in zip(h,l)]
    if len(m)<36:return None
    return _sma(m,5)-_sma(m,34)

def _uo(h,l,c):
    if len(c)<29:return None
    bp=[]; tr=[]
    for i in range(len(c)):
        pc=c[i-1] if i else c[i]
        bp.append(c[i]-min(l[i],pc)); tr.append(max(h[i],pc)-min(l[i],pc))
    vals=[]
    for n in (7,14,28):
        den=sum(tr[-n:]); vals.append(0 if den==0 else sum(bp[-n:])/den)
    return 100*(4*vals[0]+2*vals[1]+vals[2])/7

def _adx(h,l,c,n=14):
    if len(c)<n*2+2:return None
    tr=[]; pd=[]; md=[]
    for i in range(1,len(c)):
        up=h[i]-h[i-1]; dn=l[i-1]-l[i]
        tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
        pd.append(up if up>dn and up>0 else 0); md.append(dn if dn>up and dn>0 else 0)
    def rma(a):
        if len(a)<n:return []
        x=sum(a[:n])/n; out=[x]
        for z in a[n:]: x=((n-1)*x+z)/n; out.append(x)
        return out
    atr=rma(tr); p=rma(pd); m=rma(md)
    dx=[]; pis=[]; mis=[]
    for a,b,d in zip(atr,p,m):
        pi=100*b/a if a else 0; mi=100*d/a if a else 0
        pis.append(pi); mis.append(mi); dx.append(100*abs(pi-mi)/(pi+mi) if pi+mi else 0)
    ad=rma(dx)
    return (ad[-1], ad[-2] if len(ad)>1 else ad[-1], pis[-1], mis[-1]) if ad else None

def _stoch_rsi(c):
    if len(c)<60:return None
    rs=[]
    for i in range(14,len(c)):
        x=calculate_rsi(c[:i+1],14)
        if x is not None: rs.append(x)
    if len(rs)<17:return None
    raw=[]
    for i in range(13,len(rs)):
        w=rs[i-13:i+1]; lo=min(w); hi=max(w)
        raw.append(50 if hi==lo else 100*(w[-1]-lo)/(hi-lo))
    if len(raw)<3:return None
    k=sum(raw[-3:])/3; d=sum(raw[-5:-2])/3 if len(raw)>=5 else k
    return k,d

def technical_rating(data):
    closed=data[:-1]
    try:
        c=[float(x[4]) for x in closed]; h=[float(x[2]) for x in closed]; l=[float(x[3]) for x in closed]; v=[float(x[5]) for x in closed]
    except Exception:return None
    if len(c)<220:return None
    price=c[-1]; ma=[]
    for n in (10,20,30,50,100,200):
        x=_sma(c,n); ma.append(1 if price>x else -1 if price<x else 0)
    for n in (10,20,30,50,100,200):
        x=_ema(c,n); ma.append(1 if price>x else -1 if price<x else 0)
    for x in (_hma(c,9), _sma([a*b for a,b in zip(c[-20:],v[-20:])],20)/(_sma(v,20) or 1)):
        ma.append(1 if x is not None and price>x else -1 if x is not None and price<x else 0)
    conv=(max(h[-9:])+min(l[-9:]))/2; base=(max(h[-26:])+min(l[-26:]))/2; a=(conv+base)/2; b=(max(h[-52:])+min(l[-52:]))/2
    ma.append(1 if a>b and base>a and conv>base and price>conv else -1 if a<b and base<a and conv<base and price<conv else 0)
    o=[]
    r=calculate_rsi(c,14); rp=calculate_rsi(c[:-1],14)
    o.append(1 if r<30 and r>rp else -1 if r>70 and r<rp else 0)
    sk=_stoch(h,l,c,14); sp=_stoch(h[:-1],l[:-1],c[:-1],14); o.append(1 if sk<20 and sk>sp else -1 if sk>80 and sk<sp else 0)
    q=_cci(h,l,c); qp=_cci(h[:-1],l[:-1],c[:-1]); o.append(1 if q<-100 and q>qp else -1 if q>100 and q<qp else 0)
    ad=_adx(h,l,c); o.append(1 if ad and ad[2]>ad[3] and ad[0]>20 and ad[0]>ad[1] else -1 if ad and ad[2]<ad[3] and ad[0]>20 and ad[0]<ad[1] else 0)
    ao=_ao(h,l); ao0=_ao(h[:-1],l[:-1]); ao2=_ao(h[:-2],l[:-2]); o.append(1 if ao is not None and ((ao0 is not None and ao0<=0<ao) or (ao>0 and ao0>0 and ao>ao0 and ao0<ao2)) else -1 if ao is not None and ((ao0 is not None and ao0>=0>ao) or (ao<0 and ao0<0 and ao<ao0 and ao0>ao2)) else 0)
    mom=c[-1]-c[-11]; momp=c[-2]-c[-12]; o.append(1 if mom>momp else -1 if mom<momp else 0)
    mc=_macd(c); o.append(1 if mc and mc[0]>mc[1] else -1 if mc and mc[0]<mc[1] else 0)
    sr=_stoch_rsi(c); o.append(1 if sr and sr[0]<20 and sr[1]<20 and sr[0]>sr[1] else -1 if sr and sr[0]>80 and sr[1]>80 and sr[0]<sr[1] else 0)
    wr=-100*(max(h[-14:])-c[-1])/(max(h[-14:])-min(l[-14:])) if max(h[-14:])!=min(l[-14:]) else -50
    wrp=-100*(max(h[-15:-1])-c[-2])/(max(h[-15:-1])-min(l[-15:-1])) if max(h[-15:-1])!=min(l[-15:-1]) else -50
    o.append(1 if wr<-80 and wr>wrp else -1 if wr>-20 and wr<wrp else 0)
    e=_ema(c,13); ep=_ema(c[:-1],13); bp=h[-1]-e; bear=l[-1]-e; bpp=h[-2]-ep; bearp=l[-2]-ep
    o.append(1 if price>e and bear<0 and bear>bearp else -1 if price<e and bp>0 and bp<bpp else 0)
    u=_uo(h,l,c); o.append(1 if u>70 else -1 if u<30 else 0)
    mar=sum(ma)/len(ma); orat=sum(o)/len(o); total=(mar+orat)/2
    cat='Strong Sell' if total<-0.5 else 'Sell' if total<-0.1 else 'Neutral' if total<=0.1 else 'Buy' if total<=0.5 else 'Strong Buy'
    return {'overall':total,'ma':mar,'oscillators':orat,'category':cat}

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



def next_candle_prediction(data, technical_5m=None, technical_15m=None):
    """Independent next-candle confluence model.

    This is NOT a statistical probability and is intentionally separate from
    Technical Rating. It uses the latest closed candle and checks five
    conditions: RSI extreme + turn, 5m/15m Technical Rating, MACD cross,
    and price vs EMA20.
    """
    try:
        closed = data[:-1]
        if len(closed) < 60:
            return None

        c = [float(x[4]) for x in closed]
        h = [float(x[2]) for x in closed]
        l = [float(x[3]) for x in closed]

        rsi_now = calculate_rsi(c, 14)
        rsi_prev = calculate_rsi(c[:-1], 14)
        if rsi_now is None or rsi_prev is None:
            return None

        # RSI reversal from an extreme.
        bullish_rsi = rsi_now <= 30 and rsi_now > rsi_prev
        bearish_rsi = rsi_now >= 70 and rsi_now < rsi_prev

        def rating_direction(t):
            if not t:
                return 0
            cat = t.get("category")
            if cat in ("Strong Buy", "Buy"):
                return 1
            if cat in ("Strong Sell", "Sell"):
                return -1
            return 0

        r5 = rating_direction(technical_5m)
        r15 = rating_direction(technical_15m)
        bullish_ratings = r5 == 1 and r15 == 1
        bearish_ratings = r5 == -1 and r15 == -1

        macd_now = _macd(c)
        macd_prev = _macd(c[:-1])
        bullish_macd = bool(macd_now and macd_prev and
                            macd_prev[0] <= macd_prev[1] and
                            macd_now[0] > macd_now[1])
        bearish_macd = bool(macd_now and macd_prev and
                            macd_prev[0] >= macd_prev[1] and
                            macd_now[0] < macd_now[1])

        ema20 = _ema(c, 20)
        price = c[-1]
        bullish_ema = ema20 is not None and price > ema20
        bearish_ema = ema20 is not None and price < ema20

        bull_checks = [bullish_rsi, bullish_ratings, bullish_macd, bullish_ema, r5 == 1 or r15 == 1]
        bear_checks = [bearish_rsi, bearish_ratings, bearish_macd, bearish_ema, r5 == -1 or r15 == -1]
        bull = sum(bool(x) for x in bull_checks)
        bear = sum(bool(x) for x in bear_checks)

        # Require at least 3 of 5 conditions and a directional edge.
        if bull >= 3 and bull > bear:
            return {"direction": "Bullish", "emoji": "🟢", "confluence": bull, "total": 5}
        if bear >= 3 and bear > bull:
            return {"direction": "Bearish", "emoji": "🔴", "confluence": bear, "total": 5}

        return {"direction": "Neutral", "emoji": "🟡", "confluence": max(bull, bear), "total": 5}
    except Exception:
        return None

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
        return "🟢 تمایل صعودی قوی"

    if score > 0:
        return "🟢 تمایل صعودی"

    if score <= -3:
        return "🔴 تمایل نزولی قوی"

    if score < 0:
        return "🔴 تمایل نزولی"

    return "⚪ خنثی"


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


def build_message(symbol, timeframe, rsi, volume_ratio, volume_emoji, trend, trend_emoji, market_probability, technical=None, next_candle=None):
    score = score_signal(rsi, volume_ratio, trend, market_probability)
    label = score_label(score)
    market_text = format_market_probability(market_probability)
    technical_text = technical["category"] if technical else "N/A"
    if technical_text in ("Strong Buy", "Buy"):
        technical_text += " 🟢"
    elif technical_text == "Neutral":
        technical_text += " 🟡"
    elif technical_text in ("Sell", "Strong Sell"):
        technical_text += " 🔴"
    if next_candle:
        d = next_candle.get("direction", "N/A")
        c = next_candle.get("confluence", "N/A")
        nc = f"🟢 Bullish ({c})" if d == "Bullish" else f"🔴 Bearish ({c})" if d == "Bearish" else f"🟡 Neutral ({c})"
    else:
        nc = "N/A"
    rsi_status = "🔴 Overbought" if rsi >= 70 else "🟢 Oversold" if rsi <= 30 else "🟡 Neutral"
    return (f"<b>📊 {symbol} | {timeframe}</b>\n"
            f"<code>┌────────────────────────────┐\n"
            f"│ Metric              Value  │\n"
            f"├────────────────────────────┤\n"
            f"│ RSI                 {rsi:5.1f}  │\n"
            f"│ RSI Status       {rsi_status:<9}│\n"
            f"│ Volume            {volume_ratio:4.1f}x {volume_emoji} │\n"
            f"│ Market          {market_text:<11}│\n"
            f"│ Technical       {technical_text:<11}│\n"
            f"│ Next Candle     {nc:<11}│\n"
            f"└────────────────────────────┘</code>\n\n"
            f"<b>📈 Signal</b>\n{label}\n\n"
            f"🔗 <a href=\"{tv_link(symbol, timeframe)}\">Open TradingView</a>")


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

        rating_data = get_klines(signal["symbol"], TIMEFRAMES[signal["timeframe"]], limit=260)
        technical = technical_rating(rating_data) if rating_data else None

        # Independent next-candle model: 5m + 15m Technical Rating
        # are used as confirmation, while MACD/EMA/RSI are calculated
        # from the signal timeframe's closed candles.
        next_data = rating_data if rating_data else get_klines(
            signal["symbol"], TIMEFRAMES[signal["timeframe"]], limit=260
        )
        tr5_data = get_klines(signal["symbol"], "5m", limit=260)
        tr15_data = get_klines(signal["symbol"], "15m", limit=260)
        tr5 = technical_rating(tr5_data) if tr5_data else None
        tr15 = technical_rating(tr15_data) if tr15_data else None
        next_candle = next_candle_prediction(next_data, tr5, tr15) if next_data else None

        message = build_message(
            symbol=signal["symbol"],
            timeframe=signal["timeframe"],
            rsi=signal["rsi"],
            volume_ratio=signal["volume_ratio"],
            volume_emoji=signal["volume_emoji"],
            trend=signal["trend"],
            trend_emoji=signal["trend_emoji"],
            market_probability=probability,
            technical=technical,
            next_candle=next_candle,
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
