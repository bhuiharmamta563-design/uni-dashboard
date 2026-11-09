"""
Delta Exchange: S/R + Demand/Supply zones + AI + Trader (ek hi file: CLI aur Streamlit dono)
----------------------------------------------------------------
Sirf stdlib use hota hai, pip install ki zaroorat nahi.

API key code me NAHI likhni. Environment variable se aati hai:
    export DELTA_API_KEY="..."
    export DELTA_API_SECRET="..."
    export ANTHROPIC_API_KEY="..."     # sirf 'ai' command ke liye

Streamlit UI:
    streamlit run app.py

CLI Commands:
    python app.py scan UNIUSD AVAXUSD --tf 15m            # analysis (key nahi chahiye)
    python app.py scan UNIUSD --tf 1h --watch 60          # har 60 sec repeat
    python app.py trade UNIUSD --risk 5 --lev 5 --max-margin 20          # DRY RUN (side auto)
    python app.py trade UNIUSD --side long --risk 5 --lev 5 --live       # asli order
    python app.py positions
    python app.py cancel UNIUSD --live
    python app.py ai UNIUSD AVAXUSD --tfs 15m,1h,4h       # AI demand/supply analysis

DEFAULT = DRY RUN. Order tabhi jayega jab --live lagaoge.
Delta India ke liye BASE neeche set hai. Global account ho to
--base https://api.delta.exchange do. Pehle testnet pe try karo.
"""
import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.india.delta.exchange"
KEY = os.environ.get("DELTA_API_KEY", "")
SECRET = os.environ.get("DELTA_API_SECRET", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
              "1h": 3600, "4h": 14400, "1d": 86400}


# =====================  API helpers  =====================
def api_get(path, params=None):
    """Public endpoint (key nahi chahiye)."""
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "delta-app/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    if not data.get("success", True):
        raise RuntimeError(f"API error: {data}")
    return data["result"]


def signed(method, path, params=None, body=None):
    """Private endpoint (HMAC signature ke saath)."""
    if not KEY or not SECRET:
        raise SystemExit("DELTA_API_KEY / DELTA_API_SECRET set karo")
    ts = str(int(time.time()))
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    payload = json.dumps(body, separators=(",", ":")) if body else ""
    msg = method + ts + path + qs + payload
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    headers = {"api-key": KEY, "timestamp": ts, "signature": sig,
               "User-Agent": "delta-app/1.0", "Content-Type": "application/json",
               "Accept": "application/json"}
    req = urllib.request.Request(BASE + path + qs,
                                 data=payload.encode() if payload else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()}")


def get_candles(symbol, tf="15m", n=300):
    sec = TF_SECONDS[tf]
    end = int(time.time())
    start = end - n * sec
    res = api_get("/v2/history/candles",
                  {"symbol": symbol, "resolution": tf, "start": start, "end": end})
    res = sorted(res, key=lambda x: x["time"])
    return [{**{k: float(c[k]) for k in ("open", "high", "low", "close", "volume")},
             "time": int(c["time"])} for c in res]


def get_orderbook(symbol, depth=100):
    ob = api_get(f"/v2/l2orderbook/{symbol}", {"depth": depth})
    bids = [(float(x["price"]), float(x["size"])) for x in ob.get("buy", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in ob.get("sell", [])]
    return bids, asks


def get_ticker(symbol):
    t = api_get(f"/v2/tickers/{symbol}")
    return float(t.get("mark_price") or t["close"])


def product_info(symbol):
    p = api_get(f"/v2/products/{symbol}")
    return {"id": int(p["id"]), "tick": float(p["tick_size"]),
            "cv": float(p["contract_value"])}


def round_tick(price, tick):
    return round(round(price / tick) * tick, 10)


def fmt(p):
    return f"{p:.6g}"


# =====================  Analysis  =====================
def swing_points(c, left=3, right=3):
    highs, lows = [], []
    for i in range(left, len(c) - right):
        h, l = c[i]["high"], c[i]["low"]
        if all(h >= c[j]["high"] for j in range(i - left, i + right + 1)):
            highs.append(h)
        if all(l <= c[j]["low"] for j in range(i - left, i + right + 1)):
            lows.append(l)
    return highs, lows


def cluster_levels(prices, tol_pct=0.4):
    """Paas ke levels ko ek zone banata hai. Jitne zyada touches, utna strong."""
    prices = sorted(prices)
    groups = []
    for p in prices:
        if groups and abs(p - groups[-1][-1]) / groups[-1][-1] * 100 <= tol_pct:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(sum(g) / len(g), len(g)) for g in groups]


def volume_profile(c, bins=40):
    lo = min(x["low"] for x in c)
    hi = max(x["high"] for x in c)
    step = (hi - lo) / bins or 1e-9
    vol = [0.0] * bins
    for x in c:
        a = min(int((x["low"] - lo) / step), bins - 1)
        b = min(int((x["high"] - lo) / step), bins - 1)
        share = x["volume"] / (b - a + 1)
        for k in range(a, b + 1):
            vol[k] += share
    nodes = [(lo + (k + 0.5) * step, v) for k, v in enumerate(vol)]
    poc = max(nodes, key=lambda n: n[1])
    avg = sum(vol) / bins
    hvn = [n for n in nodes if n[1] > 1.5 * avg]
    return poc, hvn


def book_walls(levels, price, bucket_pct=0.2, top=3):
    bucket = price * bucket_pct / 100
    agg = {}
    for p, s in levels:
        k = round(p / bucket)
        agg[k] = agg.get(k, 0) + s
    walls = [(k * bucket, s) for k, s in agg.items()]
    return sorted(walls, key=lambda w: w[1], reverse=True)[:top]


def atr_series(c, n=14):
    tr = [c[0]["high"] - c[0]["low"]]
    for i in range(1, len(c)):
        h, l, pc = c[i]["high"], c[i]["low"], c[i - 1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = []
    for i in range(len(tr)):
        w = tr[max(0, i - n + 1):i + 1]
        out.append(sum(w) / len(w))
    return out


def find_zones(c, impulse_atr=1.5, look=3):
    """Demand/Supply zones: chhoti 'base' candle ke baad tez impulse move.
    Demand = base ke baad upar ka tez move (buyers ne yahan se price uthaya).
    Supply = base ke baad neeche ka tez move (sellers ne yahan se girAya).
    Jo zone close se tut gaya wo hata diya jata hai. Fresh zone ko zyada score."""
    a = atr_series(c)
    avg_vol = (sum(x["volume"] for x in c) / len(c)) or 1e-9
    demand, supply = [], []
    for i in range(14, len(c) - look):
        b = c[i]
        rng = b["high"] - b["low"]
        body = abs(b["close"] - b["open"])
        if rng <= 0 or body > 0.5 * rng or rng > 1.5 * a[i]:
            continue
        fut = c[i + 1:i + 1 + look]
        up = max(x["close"] for x in fut) - b["close"]
        dn = b["close"] - min(x["close"] for x in fut)
        vol = sum(x["volume"] for x in fut) / look / avg_vol
        if up >= impulse_atr * a[i]:
            kind, lo, hi, imp = "demand", b["low"], max(b["open"], b["close"]), up / a[i]
        elif dn >= impulse_atr * a[i]:
            kind, lo, hi, imp = "supply", min(b["open"], b["close"]), b["high"], dn / a[i]
        else:
            continue
        touches, broken = 0, False
        for x in c[i + look + 1:]:
            if kind == "demand":
                if x["close"] < lo:
                    broken = True
                    break
                if x["low"] <= hi:
                    touches += 1
            else:
                if x["close"] > hi:
                    broken = True
                    break
                if x["high"] >= lo:
                    touches += 1
        if broken:
            continue
        score = imp + 0.5 * vol + (1.5 if touches == 0 else 0.5 if touches == 1 else 0)
        z = {"lo": lo, "hi": hi, "score": round(score, 2), "touches": touches,
             "age": len(c) - 1 - i}
        (demand if kind == "demand" else supply).append(z)

    def dedupe(zs):
        out = []
        for z in sorted(zs, key=lambda z: -z["score"]):
            if all(z["hi"] < o["lo"] or z["lo"] > o["hi"] for o in out):
                out.append(z)
        return out
    return dedupe(demand), dedupe(supply)


def nearby_zones(c, price, top=3, max_dist_pct=10):
    demand, supply = find_zones(c)
    d = [z for z in demand if z["hi"] < price and (price - z["hi"]) / price * 100 <= max_dist_pct]
    s = [z for z in supply if z["lo"] > price and (z["lo"] - price) / price * 100 <= max_dist_pct]
    d = sorted(sorted(d, key=lambda z: -z["score"])[:top], key=lambda z: -z["hi"])
    s = sorted(sorted(s, key=lambda z: -z["score"])[:top], key=lambda z: z["lo"])
    return d, s


def analyze(symbol, tf):
    c = get_candles(symbol, tf)
    if len(c) < 30:
        print(f"{symbol}: kam candles mili, symbol/timeframe check karo")
        return
    price = get_ticker(symbol)
    highs, lows = swing_points(c)
    res_z = sorted([z for z in cluster_levels(highs) if z[0] > price],
                   key=lambda z: z[0])[:3]
    sup_z = sorted([z for z in cluster_levels(lows) if z[0] < price],
                   key=lambda z: z[0], reverse=True)[:3]
    poc, hvn = volume_profile(c)
    bids, asks = get_orderbook(symbol)
    bid_total = sum(s for _, s in bids)
    ask_total = sum(s for _, s in asks)
    imb = bid_total / (bid_total + ask_total) if (bid_total + ask_total) else 0.5

    print(f"\n===== {symbol} | {tf} | price {fmt(price)} =====")
    print("Resistance (upar):")
    for p, n in res_z:
        print(f"  {fmt(p)}  (touches: {n}, door: {(p/price-1)*100:+.2f}%)")
    print("Support (neeche):")
    for p, n in sup_z:
        print(f"  {fmt(p)}  (touches: {n}, door: {(p/price-1)*100:+.2f}%)")
    print(f"Volume POC (sabse zyada volume): {fmt(poc[0])}")
    near = [h for h in hvn if abs(h[0] / price - 1) < 0.03]
    if near:
        print("Paas ke high-volume zones: " + ", ".join(fmt(h[0]) for h in near))
    print("Order-book BUY walls:  " + ", ".join(
        f"{fmt(p)} (size {s:.0f})" for p, s in book_walls(bids, price)))
    print("Order-book SELL walls: " + ", ".join(
        f"{fmt(p)} (size {s:.0f})" for p, s in book_walls(asks, price)))
    side = "BUYERS heavy" if imb > 0.55 else "SELLERS heavy" if imb < 0.45 else "balanced"
    print(f"Book imbalance: {imb*100:.0f}% bids -> {side}")
    dz, sz = nearby_zones(c, price)
    print("Supply zones (sellers, upar):")
    for z in sz:
        print(f"  {fmt(z['lo'])} - {fmt(z['hi'])}  (score {z['score']}, "
              f"{'fresh' if z['touches'] == 0 else str(z['touches']) + ' touch'}, "
              f"door: {(z['lo']/price-1)*100:+.2f}%)")
    print("Demand zones (buyers, neeche):")
    for z in dz:
        print(f"  {fmt(z['lo'])} - {fmt(z['hi'])}  (score {z['score']}, "
              f"{'fresh' if z['touches'] == 0 else str(z['touches']) + ' touch'}, "
              f"door: {(z['hi']/price-1)*100:+.2f}%)")
    if sup_z and res_z:
        s, r = sup_z[0][0], res_z[0][0]
        risk = price - s * 0.998
        if risk > 0:
            print(f"Long idea: entry ~{fmt(price)}, SL {fmt(s*0.998)}, "
                  f"TP {fmt(r)}, RR {(r-price)/risk:.2f}")


# =====================  Trading  =====================
HTF = {"1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "1d", "1d": "1d"}
MAX_LEV = 10  # isse zyada leverage allow nahi


def ema(vals, n=50):
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def htf_trend(symbol, tf):
    """Bade timeframe ka trend: price EMA50 ke upar = up, neeche = down."""
    c = get_candles(symbol, HTF[tf], 200)
    if len(c) < 60:
        return "unknown"
    closes = [x["close"] for x in c]
    e, p = ema(closes, 50), closes[-1]
    return "up" if p > e * 1.002 else "down" if p < e * 0.998 else "range"


def zone_plan(c, price, side, atr, info, risk_usd, lev):
    """Demand zone se long / Supply zone se short.
    Entry = zone ka kinara (limit), SL = zone ke bahar 0.25 ATR, TP = samne wala zone/level."""
    demand, supply = nearby_zones(c, price)
    highs, lows = swing_points(c)
    buf, min_risk = 0.25 * atr, 0.5 * atr
    if side == "long":
        if not demand:
            return None
        z = max(demand, key=lambda z: z["score"])
        entry, sl = z["hi"], z["lo"] - buf
        targets = [s["lo"] for s in supply if s["lo"] > entry] + \
                  [p for p, _ in cluster_levels(highs) if p > entry]
        if not targets:
            return None
        tp = min(targets)
        if entry - sl < min_risk:
            sl = entry - min_risk
        risk, reward = entry - sl, tp - entry
    else:
        if not supply:
            return None
        z = max(supply, key=lambda z: z["score"])
        entry, sl = z["lo"], z["hi"] + buf
        targets = [d["hi"] for d in demand if d["hi"] < entry] + \
                  [p for p, _ in cluster_levels(lows) if p < entry]
        if not targets:
            return None
        tp = max(targets)
        if sl - entry < min_risk:
            sl = entry + min_risk
        risk, reward = sl - entry, entry - tp
    if risk <= 0 or reward <= 0:
        return None
    size = int(risk_usd / (risk * info["cv"]))
    if size < 1:
        return None
    notional = size * info["cv"] * entry
    return {"info": info, "side": side,
            "entry": round_tick(entry, info["tick"]),
            "sl": round_tick(sl, info["tick"]),
            "tp": round_tick(tp, info["tick"]),
            "size": size, "rr": reward / risk, "notional": notional,
            "margin": notional / lev, "score": z["score"],
            "note": f"{'demand' if side == 'long' else 'supply'} zone "
                    f"{fmt(z['lo'])}-{fmt(z['hi'])} (score {z['score']}, "
                    f"{'fresh' if z['touches'] == 0 else str(z['touches']) + ' touch'})"}


def plan_trade(symbol, side, tf, risk_usd, lev, rr_min, max_margin=0):
    """side = long / short / auto. Auto me HTF trend ke saath wali side aur behtar RR*zone-score chunta hai."""
    if lev > MAX_LEV:
        raise RuntimeError(f"Leverage {lev}x > max {MAX_LEV}x. Kam rakho.")
    info = product_info(symbol)
    c = get_candles(symbol, tf)
    if len(c) < 60:
        raise RuntimeError("Kam candles mili")
    price = get_ticker(symbol)
    atr = atr_series(c)[-1]
    trend = htf_trend(symbol, tf)

    plans = []
    for sd in (["long", "short"] if side == "auto" else [side]):
        if side == "auto" and ((sd == "long" and trend == "down") or
                               (sd == "short" and trend == "up")):
            continue  # bade trend ke khilaf auto trade nahi
        p = zone_plan(c, price, sd, atr, info, risk_usd, lev)
        if p and p["rr"] >= rr_min:
            plans.append(p)

    if plans:
        best = max(plans, key=lambda p: p["rr"] * p["score"])
    elif side != "auto":
        best = plan_trade_sr(symbol, side, tf, risk_usd, lev, rr_min)  # fallback: S/R
        best["note"] = "zone nahi mila, S/R fallback"
    else:
        raise RuntimeError(f"Koi acha zone setup nahi (HTF trend: {trend}, min RR {rr_min})")

    best["trend"] = trend
    if side != "auto" and ((best["side"] == "long" and trend == "down") or
                           (best["side"] == "short" and trend == "up")):
        print(f"WARNING: {HTF[tf]} trend {trend} hai, tumhari side uske khilaf hai.")
    if max_margin and best["margin"] > max_margin:
        best["size"] = int(max_margin * lev / (info["cv"] * best["entry"]))
        if best["size"] < 1:
            raise RuntimeError("max-margin me 1 contract bhi nahi aata")
        best["notional"] = best["size"] * info["cv"] * best["entry"]
        best["margin"] = best["notional"] / lev
        print("NOTE: max-margin ki wajah se size kam kiya gaya (risk ab target se kam hai).")
    return best


def plan_trade_sr(symbol, side, tf, risk_usd, lev, rr_min):
    info = product_info(symbol)
    c = get_candles(symbol, tf)
    price = get_ticker(symbol)
    highs, lows = swing_points(c)
    sup = sorted([z for z in cluster_levels(lows) if z[0] < price],
                 key=lambda z: z[0], reverse=True)
    res = sorted([z for z in cluster_levels(highs) if z[0] > price],
                 key=lambda z: z[0])
    if not sup or not res:
        raise RuntimeError("Support/Resistance nahi mila")

    buf = 0.002  # SL ko level se thoda bahar rakhna
    entry = price
    if side == "long":
        sl, tp = sup[0][0] * (1 - buf), res[0][0] * (1 - buf)
        risk_per_unit, reward = entry - sl, tp - entry
    else:
        sl, tp = res[0][0] * (1 + buf), sup[0][0] * (1 + buf)
        risk_per_unit, reward = sl - entry, entry - tp
    if risk_per_unit <= 0 or reward <= 0:
        raise RuntimeError("Setup valid nahi (SL/TP galat taraf)")
    rr = reward / risk_per_unit
    if rr < rr_min:
        raise RuntimeError(f"RR {rr:.2f} < {rr_min}, trade skip")

    size = int(risk_usd / (risk_per_unit * info["cv"]))
    if size < 1:
        raise RuntimeError("Risk bahut chhota hai, 1 contract bhi nahi banta")
    notional = size * info["cv"] * entry
    return {"info": info, "side": side,
            "entry": round_tick(entry, info["tick"]),
            "sl": round_tick(sl, info["tick"]),
            "tp": round_tick(tp, info["tick"]),
            "size": size, "rr": rr, "notional": notional,
            "margin": notional / lev}


def place(symbol, plan, lev, live):
    print(f"\n{symbol} {plan['side'].upper()} | size {plan['size']} | "
          f"entry {plan['entry']} | SL {plan['sl']} | TP {plan['tp']} | "
          f"RR {plan['rr']:.2f} | notional ${plan['notional']:.2f} | "
          f"margin ~${plan['margin']:.2f} ({lev}x)")
    print(f"Setup: {plan.get('note', '')} | HTF trend: {plan.get('trend', '?')}")
    if not live:
        print("DRY RUN: order nahi lagaya. Asli ke liye --live lagao.")
        return
    pid = plan["info"]["id"]
    open_pos = signed("GET", "/v2/positions/margined").get("result", [])
    if any(p.get("product_symbol") == symbol and float(p.get("size") or 0) != 0
           for p in open_pos):
        raise RuntimeError(f"{symbol} me pehle se position khuli hai, naya order nahi lagaya")
    signed("POST", f"/v2/products/{pid}/orders/leverage", body={"leverage": str(lev)})
    body = {
        "product_id": pid,
        "size": plan["size"],
        "side": "buy" if plan["side"] == "long" else "sell",
        "order_type": "limit_order",
        "limit_price": str(plan["entry"]),
        "time_in_force": "gtc",
        "bracket_stop_loss_price": str(plan["sl"]),
        "bracket_take_profit_price": str(plan["tp"]),
    }
    res = signed("POST", "/v2/orders", body=body)
    print("Order response:", json.dumps(res.get("result", res), indent=2))


def show_positions():
    rows = signed("GET", "/v2/positions/margined").get("result", [])
    if not rows:
        print("Koi open position nahi")
    for p in rows:
        print(p.get("product_symbol"), "size", p.get("size"),
              "entry", p.get("entry_price"), "uPnL", p.get("unrealized_pnl"))


def cancel_all(symbol):
    pid = product_info(symbol)["id"]
    print(signed("DELETE", "/v2/orders/all",
                 body={"product_id": pid, "cancel_limit_orders": True,
                       "cancel_stop_orders": True}))


# =====================  AI analysis (Claude API)  =====================
AI_SYSTEM = (
    "Tum ek crypto futures technical analyst ho. Delta Exchange ka computed data JSON me "
    "diya gaya hai (demand/supply zones, support/resistance, volume POC, order-book walls). "
    "Sirf diye gaye numbers use karo, koi level khud mat banao. Jawab Hindi (Devanagari) me do, "
    "trading terms English me rakh sakte ho. Format: "
    "1) Sabse strong DEMAND zones (kyun strong) 2) Sabse strong SUPPLY zones "
    "3) Bias: bullish/bearish/range aur wajah (timeframes ka confluence dekho) "
    "4) Ek concrete plan: entry zone, SL, TP, approx RR "
    "5) Kab ye idea galat ho jayega (invalidation). "
    "250 shabdon se kam. Aakhir me ek chhoti line: ye financial advice nahi hai."
)


def build_snapshot(symbol, tfs):
    price = get_ticker(symbol)
    snap = {"symbol": symbol, "price": float(fmt(price)), "timeframes": {}}
    for tf in tfs:
        c = get_candles(symbol, tf)
        if len(c) < 30:
            continue
        highs, lows = swing_points(c)
        d, s = nearby_zones(c, price)
        poc, _ = volume_profile(c)
        a = atr_series(c)[-1]
        snap["timeframes"][tf] = {
            "atr": float(fmt(a)),
            "volume_poc": float(fmt(poc[0])),
            "demand_zones": [{"lo": float(fmt(z["lo"])), "hi": float(fmt(z["hi"])),
                              "score": z["score"], "touches": z["touches"],
                              "age_candles": z["age"]} for z in d],
            "supply_zones": [{"lo": float(fmt(z["lo"])), "hi": float(fmt(z["hi"])),
                              "score": z["score"], "touches": z["touches"],
                              "age_candles": z["age"]} for z in s],
            "resistance": [{"price": float(fmt(p)), "touches": n} for p, n in sorted(
                [z for z in cluster_levels(highs) if z[0] > price])[:3]],
            "support": [{"price": float(fmt(p)), "touches": n} for p, n in sorted(
                [z for z in cluster_levels(lows) if z[0] < price], reverse=True)[:3]],
            "last_closes": [float(fmt(x["close"])) for x in c[-5:]],
        }
    bids, asks = get_orderbook(symbol)
    bt, at = sum(s for _, s in bids), sum(s for _, s in asks)
    snap["orderbook"] = {
        "bid_ask_imbalance_pct": round(bt / (bt + at) * 100) if (bt + at) else 50,
        "buy_walls": [{"price": float(fmt(p)), "size": round(s)} for p, s in book_walls(bids, price)],
        "sell_walls": [{"price": float(fmt(p)), "size": round(s)} for p, s in book_walls(asks, price)],
    }
    return snap


def ask_claude(user_text):
    if not ANTHROPIC_KEY:
        raise SystemExit("ANTHROPIC_API_KEY set karo (sirf 'ai' command ke liye)")
    body = {"model": CLAUDE_MODEL, "max_tokens": 1500, "system": AI_SYSTEM,
            "messages": [{"role": "user", "content": user_text}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Claude API HTTP {e.code}: {e.read().decode()}")
    return "".join(b.get("text", "") for b in data.get("content", []))


def ai_analyze(symbol, tfs):
    snap = build_snapshot(symbol, tfs)
    return ask_claude("Is data ka analysis karo:\n" + json.dumps(snap, indent=1))


# =====================  CLI  =====================
def main():
    global BASE
    ap = argparse.ArgumentParser(description="Delta S/R scanner + trader")
    ap.add_argument("--base", default=BASE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="S/R, volume profile, order-book walls")
    s.add_argument("symbols", nargs="+")
    s.add_argument("--tf", default="15m", choices=TF_SECONDS.keys())
    s.add_argument("--watch", type=int, default=0, help="har N second me repeat")

    t = sub.add_parser("trade", help="S/R se plan banao aur (optional) order lagao")
    t.add_argument("symbol")
    t.add_argument("--side", choices=["long", "short", "auto"], default="auto",
                   help="auto = zone + bade timeframe ke trend se side khud chunta hai")
    t.add_argument("--tf", default="15m", choices=TF_SECONDS.keys())
    t.add_argument("--risk", type=float, default=5, help="ek trade me max loss (USD)")
    t.add_argument("--lev", type=int, default=5)
    t.add_argument("--rr", type=float, default=1.5, help="minimum risk:reward")
    t.add_argument("--max-margin", type=float, default=0,
                   help="ek trade me max margin (USD), 0 = koi limit nahi")
    t.add_argument("--live", action="store_true")

    sub.add_parser("positions", help="open positions dikhao")

    ai = sub.add_parser("ai", help="Claude se demand/supply zone analysis")
    ai.add_argument("symbols", nargs="+")
    ai.add_argument("--tfs", default="15m,1h,4h", help="comma se alag timeframes")

    c = sub.add_parser("cancel", help="ek symbol ke saare orders cancel")
    c.add_argument("symbol")
    c.add_argument("--live", action="store_true")

    a = ap.parse_args()
    BASE = a.base

    if a.cmd == "scan":
        while True:
            for sym in a.symbols:
                try:
                    analyze(sym.upper(), a.tf)
                except Exception as e:
                    print(f"{sym}: error -> {e}")
            if not a.watch:
                break
            time.sleep(a.watch)
    elif a.cmd == "trade":
        sym = a.symbol.upper()
        place(sym, plan_trade(sym, a.side, a.tf, a.risk, a.lev, a.rr, a.max_margin),
              a.lev, a.live)
    elif a.cmd == "positions":
        show_positions()
    elif a.cmd == "ai":
        tfs = [t.strip() for t in a.tfs.split(",") if t.strip() in TF_SECONDS]
        for sym in a.symbols:
            print(f"\n===== {sym.upper()} | AI analysis ({', '.join(tfs)}) =====")
            try:
                print(ai_analyze(sym.upper(), tfs))
            except Exception as e:
                print(f"{sym}: error -> {e}")
    elif a.cmd == "cancel":
        if not a.live:
            print("DRY RUN: cancel nahi kiya. --live lagao.")
        else:
            cancel_all(a.symbol.upper())


# =====================  Interactive chart (touch + mouse)  =====================
C_SUP, C_DEM, C_RES, C_SPT = "#ff5252", "#00e676", "#ffab00", "#00bcd4"
C_SW, C_BW, C_POC = "#ec407a", "#9ccc65", "#ce93d8"


def _rgba(hexc, a):
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{a})"


CHART_JS = r"""
(function(){
const META=__META__;
const gd=document.getElementById('chart');
const SEC=META.sec*1000;
function s2ms(s){s=String(s).replace(' ','T');if(s.length===10)s+='T00:00:00';s=s.slice(0,19);if(s.length===16)s+=':00';return Date.parse(s+'Z');}
function ms2s(ms){return new Date(ms).toISOString().slice(0,19).replace('T',' ');}
const XSTR=META.x.slice(),XS=META.x.map(s2ms),LO=META.lo.slice(),HI=META.hi.slice();
const OP=META.o.slice(),CL=META.c.slice();
const T0=XS[0];let T1=XS[XS.length-1];
let lockY=false,pend={},raf=0,st=null,pin=null;
const fl=()=>gd._fullLayout;
const size=()=>fl()._size;
const xr=()=>fl().xaxis.range.map(s2ms);
const yr=()=>fl().yaxis.range.map(Number);
function clampX(a,b){
  let sp=Math.min(Math.max(b-a,4*SEC),(T1-T0)+20*SEC);
  const c=(a+b)/2;a=c-sp/2;b=c+sp/2;
  const lo=T0-5*SEC,hi=T1+8*SEC;
  if(a<lo){b+=lo-a;a=lo;}
  if(b>hi){a-=b-hi;b=hi;}
  if(a<lo)a=lo;
  return [a,b];
}
function flush(){
  if(raf)return;
  raf=requestAnimationFrame(function(){
    raf=0;const p=pend;pend={};
    Plotly.relayout(gd,p).then(function(){if(('xaxis.range' in p)&&!lockY)fitY();saveView();});
  });
}
function setX(a,b){const r=clampX(a,b);pend['xaxis.range']=[ms2s(r[0]),ms2s(r[1])];flush();}
function setY(a,b){if(!(b>a))return;lockY=true;pend['yaxis.range']=[a,b];flush();}
function fitY(){
  const r=xr();let lo=Infinity,hi=-Infinity;
  for(let i=0;i<XS.length;i++){if(XS[i]>=r[0]-SEC&&XS[i]<=r[1]+SEC){if(LO[i]<lo)lo=LO[i];if(HI[i]>hi)hi=HI[i];}}
  if(!isFinite(lo))return;
  const p=(hi-lo)*0.08||hi*0.001;
  Plotly.relayout(gd,{'yaxis.range':[lo-p,hi+p]});
}
function zoneAt(cx,cy){
  const r=gd.getBoundingClientRect(),s=size();
  const x=cx-r.left,y=cy-r.top;
  const inX=x>=s.l&&x<=s.l+s.w,inY=y>=s.t&&y<=s.t+s.h;
  if(inY&&x>s.l+s.w)return 'price';
  if(inX&&y>s.t+s.h)return (y-(s.t+s.h))<22?'time':'date';
  return 'body';
}
function fracAt(cx){const r=gd.getBoundingClientRect(),s=size();return Math.min(1,Math.max(0,(cx-r.left-s.l)/s.w));}
function zoomXAt(f,frac){const r=xr(),sp=r[1]-r[0],nsp=sp*f,c=r[0]+frac*sp;setX(c-frac*nsp,c-frac*nsp+nsp);}
function zoomY(f){const r=yr(),sp=r[1]-r[0],c=(r[0]+r[1])/2;setY(c-sp*f/2,c+sp*f/2);}
function panX(fr){const r=xr(),sp=r[1]-r[0];setX(r[0]+fr*sp,r[1]+fr*sp);}
function reset(){
  lockY=false;
  const i0=Math.max(0,XS.length-80);
  setX(XS[i0],T1+3*SEC);
}
function begin(z,x,y){const s=size();st={z:z,x0:x,y0:y,xr:xr(),yr:yr(),w:s.w,h:s.h,vm:false};}
function drag(x,y){
  if(!st)return;
  const dx=x-st.x0,dy=y-st.y0,sx=st.xr[1]-st.xr[0],sy=st.yr[1]-st.yr[0];
  if(st.z==='time'||st.z==='body'){const sh=-dx/st.w*sx;setX(st.xr[0]+sh,st.xr[1]+sh);}
  if(st.z==='date'){const f=Math.exp(-dx/150),c=(st.xr[0]+st.xr[1])/2;setX(c-sx*f/2,c+sx*f/2);}
  if(st.z==='price'||(st.z==='body'&&(st.vm||Math.abs(dy)>6))){
    st.vm=true;const shy=dy/st.h*sy;setY(st.yr[0]+shy,st.yr[1]+shy);
  }
}
function dist(a,b){return Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY);}
function mid(t){
  const r=gd.getBoundingClientRect(),s=size();
  return {fx:Math.min(1,Math.max(0,((t[0].clientX+t[1].clientX)/2-r.left-s.l)/s.w)),
          fy:Math.min(1,Math.max(0,((t[0].clientY+t[1].clientY)/2-r.top-s.t)/s.h))};
}
function pinchBegin(t){
  pin={dx0:Math.abs(t[0].clientX-t[1].clientX),dy0:Math.abs(t[0].clientY-t[1].clientY),
       d0:dist(t[0],t[1])||1,xr:xr(),yr:yr(),m0:mid(t)};
}
function pinchMove(t){
  const ax=Math.abs(t[0].clientX-t[1].clientX),ay=Math.abs(t[0].clientY-t[1].clientY);
  const d=dist(t[0],t[1])||1,m=mid(t);
  const useX=pin.dx0>=40||pin.dy0<40,useY=pin.dy0>=40;
  let fx=1,fy=1;
  if(pin.dx0>=40)fx=pin.dx0/Math.max(ax,1);else if(useX)fx=pin.d0/d;
  if(useY)fy=pin.dy0/Math.max(ay,1);
  fx=Math.min(5,Math.max(0.2,fx));fy=Math.min(5,Math.max(0.2,fy));
  const sx=pin.xr[1]-pin.xr[0],nsx=sx*fx,cx=pin.xr[0]+pin.m0.fx*sx;
  setX(cx-m.fx*nsx,cx-m.fx*nsx+nsx);
  if(useY){
    const sy=pin.yr[1]-pin.yr[0],nsy=sy*fy,cy=pin.yr[1]-pin.m0.fy*sy,top=cy+m.fy*nsy;
    setY(top-nsy,top);
  }
}
const OFF=19800000;
const KEY='dzview:'+META.key;
function saveView(){
  try{const r=xr();localStorage.setItem(KEY,JSON.stringify({a:r[0]-T1,b:r[1]-T1,lock:lockY,y:yr()}));}catch(e){}
}
function restoreView(){
  try{
    const s=JSON.parse(localStorage.getItem(KEY)||'null');if(!s)return;
    const r=clampX(T1+s.a,T1+s.b);
    const p={'xaxis.range':[ms2s(r[0]),ms2s(r[1])]};
    if(s.lock&&s.y&&s.y[1]>s.y[0]){lockY=true;p['yaxis.range']=s.y;}
    Plotly.relayout(gd,p).then(function(){if(!lockY)fitY();});
  }catch(e){}
}
function fmtP(p){return String(Number(Number(p).toPrecision(6)));}
function setStatus(t,c){const e=document.getElementById('st');if(e){e.textContent=t;e.style.color=c;}}
let dirty=false,lastPx=null,wsFails=0,wsOpened=false,prevPx=null;
function onCandle(m){
  const ms=Math.round(Number(m.candle_start_time)/1000)+OFF;
  const o=+m.open,h=+m.high,l=+m.low,c=+m.close;
  if(!(o>0)||!(c>0))return;
  const n=XS.length-1;
  if(ms===XS[n]){OP[n]=o;HI[n]=h;LO[n]=l;CL[n]=c;}
  else if(ms>XS[n]){
    const r=xr(),atEdge=r[1]>=XS[n],d=ms-XS[n];
    XS.push(ms);XSTR.push(ms2s(ms));OP.push(o);HI.push(h);LO.push(l);CL.push(c);T1=ms;
    if(atEdge)setX(r[0]+d,r[1]+d);
  }else return;
  lastPx=c;dirty=true;
}
function onTick(m){
  const px=+(m.close||m.mark_price);
  if(!(px>0))return;
  const n=XS.length-1;
  CL[n]=px;if(px>HI[n])HI[n]=px;if(px<LO[n])LO[n]=px;
  lastPx=px;dirty=true;
}
function paint(){
  if(!dirty)return;dirty=false;
  const n=XS.length-1,p=lastPx;
  Plotly.restyle(gd,{x:[XSTR],open:[OP],high:[HI],low:[LO],close:[CL]},[0]);
  const u={};
  if(META.pi_shape>=0){u['shapes['+META.pi_shape+'].y0']=p;u['shapes['+META.pi_shape+'].y1']=p;}
  const up=(prevPx===null||p>=prevPx),col=up?'#26a69a':'#ef5350';
  if(META.pi_ann>=0){u['annotations['+META.pi_ann+'].y']=p;u['annotations['+META.pi_ann+'].text']=fmtP(p);u['annotations['+META.pi_ann+'].bgcolor']=col;}
  Plotly.relayout(gd,u);
  prevPx=p;
  const e=document.getElementById('px');if(e){e.textContent=fmtP(p);e.style.color=col;}
  if(!lockY){const y=yr();if(HI[n]>y[1]||LO[n]<y[0])fitY();}
}
function connectWS(){
  if(!META.ws){setStatus('Live off','#999');return;}
  let ws;
  try{ws=new WebSocket(META.ws);}catch(e){setStatus('WS error','#b00');return;}
  ws.onopen=function(){
    wsOpened=true;wsFails=0;setStatus('● LIVE','#1a9d4a');
    ws.send(JSON.stringify({type:'subscribe',payload:{channels:[
      {name:'candlestick_'+META.tf,symbols:[META.symbol]},
      {name:'v2/ticker',symbols:[META.symbol]}]}}));
  };
  ws.onmessage=function(ev){
    let m;try{m=JSON.parse(ev.data);}catch(e){return;}
    if(m.type==='candlestick_'+META.tf)onCandle(m);
    else if(m.type==='v2/ticker')onTick(m);
  };
  ws.onclose=function(){
    wsFails++;
    if(!wsOpened&&wsFails>=3){setStatus('Live nahi chala - sidebar me Auto refresh on karo','#b00');return;}
    setStatus('○ reconnecting...','#e08a00');
    setTimeout(connectWS,Math.min(15000,1000*wsFails));
  };
  ws.onerror=function(){try{ws.close();}catch(e){}};
}
const H0=META.h;
function fsEl(){return document.fullscreenElement||document.webkitFullscreenElement;}
function applyH(){
  const bar=document.getElementById('bar');
  const h=fsEl()?Math.max(300,window.innerHeight-bar.offsetHeight-8):H0;
  Plotly.relayout(gd,{height:h});
  document.getElementById('fs').textContent=fsEl()?'\u2715 Exit':'\u26F6 Full';
}
function toggleFs(){
  const msg=document.getElementById('msg');
  msg.textContent='';
  if(fsEl()){(document.exitFullscreen||document.webkitExitFullscreen).call(document);return;}
  const el=document.documentElement,rq=el.requestFullscreen||el.webkitRequestFullscreen;
  const fail=function(){msg.textContent='Is phone/browser me full screen nahi chala. Sidebar me "Bada chart" on karo.';};
  if(!rq){fail();return;}
  try{const p=rq.call(el);if(p&&p.catch)p.catch(fail);}catch(err){fail();}
}
function init(){
  gd.style.touchAction='none';
  document.addEventListener('fullscreenchange',function(){setTimeout(applyH,200);});
  document.addEventListener('webkitfullscreenchange',function(){setTimeout(applyH,200);});
  window.addEventListener('resize',function(){if(fsEl())applyH();});
  gd.addEventListener('mousedown',function(e){
    if(e.button!==0)return;
    e.preventDefault();
    begin(zoneAt(e.clientX,e.clientY),e.clientX,e.clientY);
    const mv=function(ev){drag(ev.clientX,ev.clientY);};
    const up=function(){st=null;window.removeEventListener('mousemove',mv);window.removeEventListener('mouseup',up);};
    window.addEventListener('mousemove',mv);window.addEventListener('mouseup',up);
  },true);
  gd.addEventListener('touchstart',function(e){
    e.preventDefault();
    if(e.touches.length===1){const t=e.touches[0];begin(zoneAt(t.clientX,t.clientY),t.clientX,t.clientY);pin=null;}
    else if(e.touches.length>=2){st=null;pinchBegin(e.touches);}
  },{capture:true,passive:false});
  gd.addEventListener('touchmove',function(e){
    e.preventDefault();
    if(pin&&e.touches.length>=2)pinchMove(e.touches);
    else if(st&&e.touches.length===1)drag(e.touches[0].clientX,e.touches[0].clientY);
  },{capture:true,passive:false});
  gd.addEventListener('touchend',function(e){
    if(e.touches.length===0){st=null;pin=null;}
    else if(e.touches.length===1){pin=null;const t=e.touches[0];begin(zoneAt(t.clientX,t.clientY),t.clientX,t.clientY);}
  },{capture:true,passive:false});
  gd.addEventListener('wheel',function(e){
    e.preventDefault();
    const z=zoneAt(e.clientX,e.clientY),f=Math.exp(e.deltaY*0.0015);
    if(z==='price'||e.ctrlKey||e.metaKey)zoomY(f);
    else if(z==='time')panX(e.deltaY>0?0.1:-0.1);
    else zoomXAt(f,fracAt(e.clientX));
  },{passive:false});
  gd.addEventListener('dblclick',reset);
  const B=function(id,fn){document.getElementById(id).addEventListener('click',fn);};
  B('zin',function(){zoomXAt(0.7,0.5);});
  B('zout',function(){zoomXAt(1/0.7,0.5);});
  B('pl',function(){panX(-0.25);});
  B('pr',function(){panX(0.25);});
  B('yin',function(){zoomY(0.8);});
  B('yout',function(){zoomY(1.25);});
  B('fit',reset);
  B('fs',toggleFs);
  B('live',function(){const r=xr(),sp=r[1]-r[0];setX(T1+3*SEC-sp,T1+3*SEC);});
  restoreView();
  setInterval(paint,120);
  connectWS();
}
function ready(){
  if(gd._fullLayout&&gd._fullLayout._size&&gd._fullLayout.xaxis&&gd._fullLayout.xaxis.range)init();
  else setTimeout(ready,50);
}
ready();
})();
"""

CHART_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
html,body{margin:0;font-family:system-ui,sans-serif;background:__BG__;color:__FG__}
#bar{display:flex;gap:6px;flex-wrap:wrap;padding:4px 2px}
#bar button{border:1px solid __BTNB__;background:__BTN__;color:__FG__;border-radius:8px;padding:8px 12px;font-size:16px;cursor:pointer;touch-action:manipulation}
#chart{touch-action:none;user-select:none;-webkit-user-select:none}
</style></head><body>
<div id="bar">
<span id="st" style="font-size:13px;font-weight:600;color:#999;align-self:center">Live: connecting...</span><span id="px" style="font-size:17px;font-weight:700;align-self:center;margin:0 6px"></span>
<button id="zin">＋</button><button id="zout">－</button>
<button id="pl">◀</button><button id="pr">▶</button>
<button id="yin">↕＋</button><button id="yout">↕－</button>
<button id="fit">Fit</button><button id="live">Live ⏭</button>
<button id="fs">⛶ Full</button>
<span id="msg" style="font-size:13px;color:#b00;align-self:center"></span>
</div>
__PLOT__
<script>__JS__</script>
</body></html>"""


def ws_url(base):
    """REST base URL se Delta WebSocket URL nikalta hai (nahi mila to khali)."""
    if "api.india.delta.exchange" in base:
        return "wss://socket.india.delta.exchange"
    if "api.delta.exchange" in base:
        return "wss://socket.delta.exchange"
    return ""


def chart_html(candles, tf, price, demand, supply, res_lv, sup_lv, poc, height=560,
               symbol="", ws="", bid_walls=(), ask_walls=(), dark=True):
    """Touch/mouse se chalne wala candlestick chart (Plotly.js + custom gestures)."""
    import datetime as dt
    import plotly.graph_objects as go
    sec = TF_SECONDS[tf]
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    n = len(candles)
    real = [c.get("time") for c in candles]
    last = int(real[-1]) if all(real) else int(time.time() // sec * sec)

    def ts(k):  # last candle se k candles aage/peeche ka IST time
        return dt.datetime.fromtimestamp(last + k * sec, ist).strftime("%Y-%m-%d %H:%M:%S")

    if all(real):
        xs = [dt.datetime.fromtimestamp(int(t), ist).strftime("%Y-%m-%d %H:%M:%S") for t in real]
    else:
        xs = [ts(i - (n - 1)) for i in range(n)]
    th = dict(bg="#131722", grid="#2a2e39", fg="#d1d4dc", btn="#1e222d", btnb="#363a45",
              tmpl="plotly_dark") if dark else \
        dict(bg="#ffffff", grid="#e6e8ee", fg="#222222", btn="#f4f5f7", btnb="#cfd3da",
             tmpl="plotly_white")
    fig = go.Figure(go.Candlestick(
        x=xs, open=[x["open"] for x in candles], high=[x["high"] for x in candles],
        low=[x["low"] for x in candles], close=[x["close"] for x in candles],
        increasing_line_color="#26a69a", increasing_fillcolor="#26a69a",
        decreasing_line_color="#ef5350", decreasing_fillcolor="#ef5350", name="price"))

    def zone(z, color, label):
        al = min(0.38, 0.12 + 0.05 * z["score"])  # strong zone zyada gehra
        fig.add_hrect(
            y0=z["lo"], y1=z["hi"], fillcolor=_rgba(color, al), line_color=color,
            line_width=1, layer="below",
            annotation_text=f"{label} {fmt(z['lo'])}-{fmt(z['hi'])}  *{z['score']}"
                            f"{'  fresh' if z['touches'] == 0 else ''}",
            annotation_position="top left", annotation_font_color=color,
            annotation_font_size=11)

    def hline(p, color, text, pos="top right", dash="solid", width=1.5):
        fig.add_hline(y=p, line_color=color, line_width=width, line_dash=dash,
                      annotation_text=text, annotation_position=pos,
                      annotation_font_color=color, annotation_font_size=11)

    for z in demand:
        zone(z, C_DEM, "DEMAND")
    for z in supply:
        zone(z, C_SUP, "SUPPLY")
    for p, k in res_lv:
        hline(p, C_RES, f"R {fmt(p)} x{k}")
    for p, k in sup_lv:
        hline(p, C_SPT, f"S {fmt(p)} x{k}")
    for p, _s in bid_walls:
        hline(p, C_BW, f"BUY wall {fmt(p)}", pos="bottom right", dash="dot", width=1)
    for p, _s in ask_walls:
        hline(p, C_SW, f"SELL wall {fmt(p)}", pos="top right", dash="dot", width=1)
    hline(poc, C_POC, "POC", pos="top left", dash="dash", width=1)
    fig.add_hline(y=price, line_color=th["fg"], line_width=1, line_dash="dot",
                  annotation_text=fmt(price), annotation_position="right",
                  annotation_font_color="#ffffff", annotation_font_size=12,
                  annotation_bgcolor="#26a69a")
    pi_shape = len(fig.layout.shapes) - 1
    pi_ann = len(fig.layout.annotations) - 1
    view = candles[-80:]
    lo = min(x["low"] for x in view)
    hi = max(x["high"] for x in view)
    pad = (hi - lo) * 0.08 or hi * 0.001
    fig.update_layout(
        template=th["tmpl"], paper_bgcolor=th["bg"], plot_bgcolor=th["bg"],
        font=dict(color=th["fg"]), height=height, margin=dict(l=10, r=72, t=10, b=56),
        dragmode=False, hovermode="x", showlegend=False,
        xaxis=dict(type="date", rangeslider=dict(visible=False), fixedrange=True,
                   gridcolor=th["grid"], tickformat="%H:%M<br>%d %b",
                   range=[xs[max(0, n - 80)], ts(3)]),
        yaxis=dict(side="right", fixedrange=True, gridcolor=th["grid"],
                   range=[lo - pad, hi + pad]))
    plot = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="chart",
                       config={"displayModeBar": False, "scrollZoom": False,
                               "doubleClick": False, "responsive": True})
    meta = json.dumps({"x": xs, "o": [x["open"] for x in candles],
                       "c": [x["close"] for x in candles],
                       "lo": [x["low"] for x in candles],
                       "hi": [x["high"] for x in candles], "sec": sec, "h": height,
                       "tf": tf, "symbol": symbol, "ws": ws, "key": f"{symbol}|{tf}",
                       "pi_shape": pi_shape, "pi_ann": pi_ann})
    js = CHART_JS.replace("__META__", meta)
    html = CHART_TEMPLATE.replace("__JS__", js)
    for k, v in (("__BG__", th["bg"]), ("__FG__", th["fg"]), ("__BTN__", th["btn"]),
                 ("__BTNB__", th["btnb"])):
        html = html.replace(k, v)
    return html.replace("__PLOT__", plot)


# =====================  Streamlit UI  =====================
class _G:
    """Is module ke globals ka proxy (UI code 'dx.' se functions/BASE use karta hai)."""
    def __getattr__(self, n):
        return globals()[n]

    def __setattr__(self, n, v):
        globals()[n] = v


dx = _G()


def run_ui():
    import contextlib
    import datetime as dt
    import io

    import streamlit as st
    import streamlit.components.v1 as components

    st.set_page_config(page_title="Delta Zones Scanner", layout="wide")


    def secret(name):
        try:
            return st.secrets.get(name, "")
        except Exception:
            return ""


    dx.KEY = secret("DELTA_API_KEY") or dx.KEY
    dx.SECRET = secret("DELTA_API_SECRET") or dx.SECRET
    dx.ANTHROPIC_KEY = secret("ANTHROPIC_API_KEY") or dx.ANTHROPIC_KEY

    URLS = {"India": "https://api.india.delta.exchange",
            "Global": "https://api.delta.exchange"}

    # ---------------- Sidebar ----------------
    st.sidebar.title("Settings")
    region = st.sidebar.selectbox("Exchange", ["India", "Global", "Custom (testnet etc.)"])
    if region.startswith("Custom"):
        base = st.sidebar.text_input("Base URL", "").strip().rstrip("/")
    else:
        base = URLS[region]
    if not base:
        st.warning("Base URL daalo")
        st.stop()
    symbol = st.sidebar.text_input("Symbol", "UNIUSD").upper().strip()
    tf = st.sidebar.selectbox("Timeframe", list(dx.TF_SECONDS), index=2)
    auto = st.sidebar.checkbox("Auto refresh")
    tall = st.sidebar.checkbox("Bada chart (full screen na chale to)")
    chart_h = 820 if tall else 560
    theme = st.sidebar.selectbox("Chart theme", ["Dark", "Light"])
    live_on = st.sidebar.checkbox("Live tick-by-tick (WebSocket)", True)
    ws = st.sidebar.text_input("WebSocket URL", ws_url(base)).strip() if live_on else ""
    interval = st.sidebar.slider("Zones/orderbook refresh (sec)", 5, 120, 30) if auto else 30
    if not dx.ANTHROPIC_KEY:
        dx.ANTHROPIC_KEY = st.sidebar.text_input("Anthropic API key (AI ke liye)", type="password")
    st.sidebar.caption(f"Delta key: {'set' if dx.KEY and dx.SECRET else 'nahi (sirf analysis chalega)'}")
    if st.sidebar.button("Refresh now"):
        st.cache_data.clear()


    @st.cache_data(ttl=3, show_spinner=False)
    def load(base_url, sym, timeframe):
        dx.BASE = base_url
        c = dx.get_candles(sym, timeframe)
        price = dx.get_ticker(sym)
        bids, asks = dx.get_orderbook(sym)
        return c, price, bids, asks


    def run_captured(fn, *args, **kwargs):
        """Function ke print() ko pakad kar (output, result/error) deta hai."""
        buf = io.StringIO()
        res, err = None, None
        with contextlib.redirect_stdout(buf):
            try:
                res = fn(*args, **kwargs)
            except (Exception, SystemExit) as e:
                err = e
        return buf.getvalue().strip(), res, err


    st.title(f"{symbol} · {tf}")
    dx.BASE = base
    try:
        candles, price, bids, asks = load(base, symbol, tf)
    except Exception as e:
        st.error(f"Data nahi aaya: {e}")
        st.info("Symbol sahi hai? (jaise UNIUSD, AVAXUSD) Exchange India/Global sahi chuna? "
                "Kuch jagah se Delta API block bhi ho sakti hai.")
        st.stop()
    if len(candles) < 60:
        st.error("Kam candles mili, symbol/timeframe check karo")
        st.stop()

    tab_scan, tab_ai, tab_trade, tab_pos = st.tabs(["Scanner", "AI analysis", "Trade", "Positions"])

    # ---------------- Scanner ----------------
    with tab_scan:
        highs, lows = dx.swing_points(candles)
        res_lv = sorted([z for z in dx.cluster_levels(highs) if z[0] > price], key=lambda z: z[0])[:3]
        sup_lv = sorted([z for z in dx.cluster_levels(lows) if z[0] < price],
                        key=lambda z: z[0], reverse=True)[:3]
        demand, supply = dx.nearby_zones(candles, price)
        poc, hvn = dx.volume_profile(candles)
        atr = dx.atr_series(candles)[-1]
        bt, at = sum(s for _, s in bids), sum(s for _, s in asks)
        imb = bt / (bt + at) * 100 if (bt + at) else 50

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Price", dx.fmt(price))
        m2.metric("ATR", dx.fmt(atr))
        m3.metric("Volume POC", dx.fmt(poc[0]))
        m4.metric("Bids in book", f"{imb:.0f}%",
                  "buyers heavy" if imb > 55 else "sellers heavy" if imb < 45 else "balanced")

        components.html(
            chart_html(candles, tf, price, demand, supply, res_lv, sup_lv, poc[0], height=chart_h,
                       symbol=symbol, ws=ws,
                       bid_walls=dx.book_walls(bids, price, top=2),
                       ask_walls=dx.book_walls(asks, price, top=2),
                       dark=(theme == "Dark")),
            height=chart_h + 100, scrolling=False)
        st.caption("Chart: neeche **time** par ungli/mouse se kheencho = aage-peeche, **date** par = zoom, "
                   "**price** par = upar-neeche. Chart par kheencho = move, 2 ungli se pinch = zoom, "
                   "double-tap = reset, ⛶ Full = poora screen. Upar ● LIVE hara ho to chart tick-by-tick chal raha hai. Hara = demand, laal = supply, peela = resistance, neela = support, jamuni = POC, gulabi/lime = order-book walls. Time IST me.")

        def card(title, color, rows):
            body = "".join(
                "<div style='display:flex;justify-content:space-between;padding:4px 0;"
                "border-bottom:1px solid rgba(128,128,128,.2)'><span>" + a_ + "</span>"
                "<span style='opacity:.85'>" + b_ + "</span></div>" for a_, b_ in rows
            ) or "<i style='opacity:.6'>koi nahi</i>"
            st.markdown(
                "<div style='border-left:5px solid " + color + ";background:" + color + "22;"
                "border-radius:8px;padding:8px 12px;margin-bottom:10px'>"
                "<b style='color:" + color + "'>" + title + "</b>" + body + "</div>",
                unsafe_allow_html=True)

        def zrows(zs, kind):
            out = []
            for z in zs:
                dist = ((z["lo"] if kind == "s" else z["hi"]) / price - 1) * 100
                out.append((f"{dx.fmt(z['lo'])} - {dx.fmt(z['hi'])}",
                            f"★{z['score']} · "
                            f"{'fresh' if z['touches'] == 0 else str(z['touches']) + ' touch'}"
                            f" · {dist:+.2f}%"))
            return out

        def lrows(ls):
            return [(dx.fmt(p), f"{k}x touch · {(p / price - 1) * 100:+.2f}%") for p, k in ls]

        def wrows(wl):
            return [(dx.fmt(p), f"size {round(s)}") for p, s in wl]

        c1, c2 = st.columns(2)
        with c1:
            card("🔴 Supply zones (sellers, upar)", C_SUP, zrows(supply, "s"))
            card("🟠 Resistance", C_RES, lrows(res_lv))
            card("🌸 Order-book SELL walls", C_SW, wrows(dx.book_walls(asks, price)))
        with c2:
            card("🟢 Demand zones (buyers, neeche)", C_DEM, zrows(demand, "d"))
            card("🔵 Support", C_SPT, lrows(sup_lv))
            card("🍏 Order-book BUY walls", C_BW, wrows(dx.book_walls(bids, price)))

    # ---------------- AI ----------------
    with tab_ai:
        st.write("Claude zones, S/R, volume aur order-book ka data dekh kar bataega ki demand/supply "
                 "kahan strong hai aur kya plan banta hai.")
        tfs = st.multiselect("Timeframes", list(dx.TF_SECONDS), default=["15m", "1h", "4h"])
        if st.button("AI se analysis karao"):
            if not tfs:
                st.warning("Kam se kam ek timeframe chuno")
            else:
                with st.spinner("Analysis ho raha hai..."):
                    out, text, err = run_captured(dx.ai_analyze, symbol, tfs)
                if err:
                    st.error(f"Error: {err}")
                else:
                    st.markdown(text)

    # ---------------- Trade ----------------
    with tab_trade:
        st.info("Pehle 'Plan banao' dabao (kuch order nahi jata). Asli order sirf neeche ke "
                "confirm ke baad. Exchange abhi: " + base)
        a1, a2, a3, a4, a5 = st.columns(5)
        side = a1.selectbox("Side", ["auto", "long", "short"])
        risk = a2.number_input("Risk (USD)", 0.5, 1000.0, 2.0, step=0.5)
        lev = a3.slider("Leverage", 1, dx.MAX_LEV, 3)
        rr_min = a4.number_input("Min RR", 1.0, 10.0, 1.5, step=0.1)
        max_margin = a5.number_input("Max margin (USD, 0=off)", 0.0, 100000.0, 0.0, step=5.0)

        if st.button("Plan banao (DRY RUN)"):
            out, plan, err = run_captured(dx.plan_trade, symbol, side, tf, risk, lev, rr_min, max_margin)
            if err:
                st.session_state.pop("plan", None)
                st.error(str(err))
            else:
                st.session_state["plan"] = plan
                st.session_state["plan_ctx"] = (symbol, base, lev, time.time())
                if out:
                    st.warning(out)

        plan = st.session_state.get("plan")
        ctx = st.session_state.get("plan_ctx")
        if plan and ctx:
            p_sym, p_base, p_lev, p_time = ctx
            age = int(time.time() - p_time)
            st.subheader(f"{p_sym} {plan['side'].upper()}")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Entry (limit)", plan["entry"])
            k2.metric("Stop loss", plan["sl"])
            k3.metric("Take profit", plan["tp"])
            k4.metric("RR", f"{plan['rr']:.2f}")
            st.write(f"Size: **{plan['size']}** contracts · Notional: ${plan['notional']:.2f} · "
                     f"Margin ~${plan['margin']:.2f} ({p_lev}x)")
            st.caption(f"Setup: {plan.get('note', '')} · HTF trend: {plan.get('trend', '?')} · "
                       f"plan {age}s purana")

            st.divider()
            st.subheader("Asli order")
            has_keys = bool(dx.KEY and dx.SECRET)
            stale = age > 120 or p_sym != symbol or p_base != base
            if not has_keys:
                st.warning("DELTA_API_KEY / DELTA_API_SECRET secrets me set karo.")
            if stale:
                st.warning("Plan purana ho gaya ya symbol/exchange badla. Dobara 'Plan banao' dabao.")
            sure = st.checkbox(f"Mujhe pata hai ye {p_base} par ASLI order hai")
            if st.button("ASLI ORDER LAGAO", type="primary",
                         disabled=not (has_keys and sure and not stale)):
                dx.BASE = p_base
                out, _, err = run_captured(dx.place, p_sym, plan, p_lev, True)
                if out:
                    st.code(out)
                if err:
                    st.error(str(err))
                else:
                    st.success("Order bhej diya gaya. Positions tab me check karo.")
                    st.session_state.pop("plan", None)

    # ---------------- Positions ----------------
    with tab_pos:
        dx.BASE = base
        if st.button("Positions dikhao"):
            out, _, err = run_captured(dx.signed, "GET", "/v2/positions/margined")
            if err:
                st.error(str(err))
            else:
                rows = _["result"] if isinstance(_, dict) else []
                st.dataframe([{"symbol": p.get("product_symbol"), "size": p.get("size"),
                               "entry": p.get("entry_price"), "uPnL": p.get("unrealized_pnl")}
                              for p in rows] or [{"info": "Koi open position nahi"}],
                             hide_index=True, use_container_width=True)
        st.divider()
        sure_c = st.checkbox(f"{symbol} ke saare open orders cancel karne hain")
        if st.button("Cancel orders", disabled=not (sure_c and dx.KEY and dx.SECRET)):
            out, _, err = run_captured(dx.cancel_all, symbol)
            st.error(str(err)) if err else st.success(out or "Cancel request bhej di")

    if auto:
        time.sleep(interval)
        st.rerun()


def _in_streamlit():
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        try:
            return get_script_run_ctx(suppress_warning=True) is not None
        except TypeError:
            return get_script_run_ctx() is not None
    except Exception:
        return False


if _in_streamlit():
    run_ui()
elif __name__ == "__main__":
    main()
