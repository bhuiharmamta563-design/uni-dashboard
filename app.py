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
    return [{k: float(c[k]) for k in ("open", "high", "low", "close", "volume")}
            for c in res]


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

    import plotly.graph_objects as go
    import streamlit as st

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
    interval = st.sidebar.slider("Refresh (sec)", 15, 120, 30) if auto else 30
    if not dx.ANTHROPIC_KEY:
        dx.ANTHROPIC_KEY = st.sidebar.text_input("Anthropic API key (AI ke liye)", type="password")
    st.sidebar.caption(f"Delta key: {'set' if dx.KEY and dx.SECRET else 'nahi (sirf analysis chalega)'}")
    if st.sidebar.button("Refresh now"):
        st.cache_data.clear()


    @st.cache_data(ttl=15, show_spinner=False)
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

        n = min(150, len(candles))
        view = candles[-n:]
        sec = dx.TF_SECONDS[tf]
        now = dt.datetime.now()
        xs = [now - dt.timedelta(seconds=(n - 1 - i) * sec) for i in range(n)]
        fig = go.Figure(go.Candlestick(
            x=xs, open=[x["open"] for x in view], high=[x["high"] for x in view],
            low=[x["low"] for x in view], close=[x["close"] for x in view], name="price"))
        for z in demand:
            fig.add_hrect(y0=z["lo"], y1=z["hi"], fillcolor="green", opacity=0.22, line_width=0)
        for z in supply:
            fig.add_hrect(y0=z["lo"], y1=z["hi"], fillcolor="red", opacity=0.22, line_width=0)
        for p, _ in res_lv:
            fig.add_hline(y=p, line_dash="dot", line_color="#d9534f")
        for p, _ in sup_lv:
            fig.add_hline(y=p, line_dash="dot", line_color="#2e9e5b")
        fig.add_hline(y=poc[0], line_dash="dash", line_color="#888", annotation_text="POC")
        fig.update_layout(height=560, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Hara = demand zone (buyers), laal = supply zone (sellers), dotted = swing support/resistance.")

        def zrows(zs, kind):
            return [{"zone": f"{dx.fmt(z['lo'])} - {dx.fmt(z['hi'])}", "score": z["score"],
                     "status": "fresh" if z["touches"] == 0 else f"{z['touches']} touch",
                     "door %": round(((z["lo"] if kind == "s" else z["hi"]) / price - 1) * 100, 2)}
                    for z in zs]

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Supply zones (upar)")
            st.dataframe(zrows(supply, "s"), hide_index=True, use_container_width=True)
            st.subheader("Resistance")
            st.dataframe([{"price": dx.fmt(p), "touches": k,
                           "door %": round((p / price - 1) * 100, 2)} for p, k in res_lv],
                         hide_index=True, use_container_width=True)
            st.subheader("Order-book SELL walls")
            st.dataframe([{"price": dx.fmt(p), "size": round(s)} for p, s in dx.book_walls(asks, price)],
                         hide_index=True, use_container_width=True)
        with c2:
            st.subheader("Demand zones (neeche)")
            st.dataframe(zrows(demand, "d"), hide_index=True, use_container_width=True)
            st.subheader("Support")
            st.dataframe([{"price": dx.fmt(p), "touches": k,
                           "door %": round((p / price - 1) * 100, 2)} for p, k in sup_lv],
                         hide_index=True, use_container_width=True)
            st.subheader("Order-book BUY walls")
            st.dataframe([{"price": dx.fmt(p), "size": round(s)} for p, s in dx.book_walls(bids, price)],
                         hide_index=True, use_container_width=True)

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
