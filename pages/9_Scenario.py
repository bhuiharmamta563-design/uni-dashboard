import math
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
IST = timezone(timedelta(hours=5, minutes=30))

GREEN = "#16a34a"
RED = "#dc2626"
AMBER = "#f59e0b"
GRAY = "#94a3b8"

st.set_page_config(page_title="COA Scenario")
st_autorefresh(interval=15000, key="scn")

now = datetime.now(IST)
day = now
if now.hour * 60 + now.minute >= 17 * 60 + 30:
    day = now + timedelta(days=1)
coin = st.selectbox("Coin", ["BTC", "ETH"])
expiry = st.text_input("Expiry (DD-MM-YYYY)",
                       value=day.strftime("%d-%m-%Y"))
basis = st.radio("S/R kis par", ["OI", "Volume"], horizontal=True)
price_src = st.radio("Premium (reversal ke liye)",
                     ["Mark", "Last (LTP)"], horizontal=True)
thr = st.slider("Weak (WTT/WTB) ki seema %", 50, 95, 75)

# scenario: [safe bottom, risky bottom, risky top, safe top]
SC = {
    1: ["EOS-1", "EOS", "EOR", "EOR+1"],
    2: ["EOS", "EOS+1", "WTT-1", None],
    3: [None, "EOS-1", "EOR-1", "EOR"],
    4: ["EOS", "EOS+1", "EOR+1", None],
    5: [None, "WTB+1", "EOR-1", "EOR"],
    6: ["EOS", "EOS+1", None, None],
    7: [None, None, "EOR-1", "EOR"],
    8: ["WTT-1", "WTT", "WTB", "WTB+1"],
    9: ["WTB", "WTB+1", "WTT-1", "WTT"],
}
STATE_TO_NO = {("S", "S"): 1, ("S", "WTT"): 2, ("S", "WTB"): 3,
               ("WTT", "S"): 4, ("WTB", "S"): 5, ("WTT", "WTT"): 6,
               ("WTB", "WTB"): 7, ("WTT", "WTB"): 8, ("WTB", "WTT"): 9}
COURSE_NO = {1: 1, 2: 3, 3: 2, 4: 5, 5: 4, 6: 7, 7: 6, 8: 9, 9: 8}
NOTE = {
    1: "Dono strong: dono taraf se palatna",
    2: "Resistance WTT: upar ka dabav (bullish)",
    3: "Resistance WTB: neeche ka dabav (bearish)",
    4: "Support WTT: upar ka dabav (bullish)",
    5: "Support WTB: neeche ka dabav (bearish)",
    6: "Dono WTT: bull run, upar ka top nahi",
    7: "Dono WTB: blood bath, neeche ka bottom nahi",
    8: "Dono kamzor: dono taraf risky",
    9: "Dono kamzor: dono taraf risky",
}


def num(x):
    try:
        return float(x)
    except Exception:
        return None


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, v, cp):
    if T <= 0 or v <= 0:
        if cp == "C":
            return max(S - K, 0.0)
        return max(K - S, 0.0)
    sq = v * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * v * v * T) / sq
    d2 = d1 - sq
    if cp == "C":
        return S * ncdf(d1) - K * ncdf(d2)
    return K * ncdf(-d2) - S * ncdf(-d1)


def bs_delta(S, K, T, v, cp):
    sq = v * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * v * v * T) / sq
    if cp == "C":
        return ncdf(d1)
    return ncdf(d1) - 1.0


def implied_vol(price, S, K, T, cp):
    if price is None or T <= 0:
        return None
    if cp == "C":
        intr = max(S - K, 0.0)
    else:
        intr = max(K - S, 0.0)
    if price <= intr + 1e-9:
        return None
    lo = 0.001
    hi = 8.0
    if bs_price(S, K, T, hi, cp) < price:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, mid, cp) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def reversal(ce, pe, K, Kp, S, T):
    vc = implied_vol(ce, S, K, T, "C")
    vp = implied_vol(pe, S, Kp, T, "P")
    if vc is None or vp is None:
        return None
    den = bs_delta(S, K, T, vc, "C") - bs_delta(S, Kp, T, vp, "P")
    if abs(den) < 1e-9:
        return None
    return S - (ce - pe) / den


def years_left(expiry):
    try:
        ex = datetime.strptime(expiry, "%d-%m-%Y")
        ex = ex.replace(hour=17, minute=30, tzinfo=IST)
        sec = (ex - datetime.now(IST)).total_seconds()
    except Exception:
        return None
    return max(sec, 60.0) / (365.0 * 86400.0)


def pick_side(cands, vals, thr):
    """cands: strikes; vals: strike->metric. Returns dict."""
    arr = [k for k in cands if vals.get(k, 0) > 0]
    if not arr:
        return None
    arr.sort(key=lambda k: -vals[k])
    first = arr[0]
    out = {"k": first, "state": "S", "weak_k": None, "pct": None,
           "second": None}
    if len(arr) > 1:
        sec = arr[1]
        pct = 100.0 * vals[sec] / vals[first]
        out["second"] = sec
        out["pct"] = pct
        if pct >= thr:
            out["weak_k"] = sec
            out["state"] = "WTT" if sec > first else "WTB"
    return out


def resolve(tok, row, sup, res, ks, X):
    """row: 0,1 bottom rows ; 2,3 top rows. Returns (strike, price)."""
    base = tok[:3]
    off = int(tok[3:]) if len(tok) > 3 else 0
    if base == "EOS":
        k0, kind = sup["k"], "S"
    elif base == "EOR":
        k0, kind = res["k"], "R"
    elif row < 2:
        k0, kind = sup["weak_k"], "S"
    else:
        k0, kind = res["weak_k"], "R"
    if k0 is None or k0 not in ks:
        return None, None
    i = ks.index(k0) + off
    if i < 0 or i >= len(ks):
        return None, None
    if kind == "S":
        return ks[i], X.get(ks[i])
    if i + 1 >= len(ks):
        return ks[i], None
    return ks[i], X.get(ks[i + 1])


try:
    T = years_left(expiry)
    r = requests.get(
        BASE + "/tickers",
        params={"contract_types": "call_options,put_options",
                "underlying_asset_symbols": coin,
                "expiry_date": expiry},
        timeout=10)
    data = r.json()["result"]
    spot = None
    ch = {}
    for t in data:
        k = num(t.get("strike_price"))
        if k is None:
            continue
        sp = num(t.get("spot_price"))
        if sp:
            spot = sp
        s = "C" if t.get("contract_type") == "call_options" else "P"
        oi = num(t.get("oi_contracts"))
        if oi is None:
            oi = num(t.get("oi"))
        p = num(t.get("mark_price"))
        if price_src.startswith("Last"):
            lp = num(t.get("close"))
            if lp:
                p = lp
        ch.setdefault(k, {})[s] = {"oi": oi or 0.0,
                                   "vol": num(t.get("volume")) or 0.0,
                                   "px": p}
    if not data or not spot or not T:
        st.warning("Data, spot ya expiry sahi nahi. Expiry badal ke dekho.")
    else:
        ks = sorted(ch)
        field = "oi" if basis == "OI" else "vol"
        cv = {k: ch[k].get("C", {}).get(field, 0.0) for k in ks}
        pv = {k: ch[k].get("P", {}).get(field, 0.0) for k in ks}
        above = [k for k in ks if k >= spot]
        below = [k for k in ks if k < spot]
        res = pick_side(above, cv, thr)
        sup = pick_side(below, pv, thr)

        X = {}
        for i in range(1, len(ks)):
            k = ks[i]
            kp = ks[i - 1]
            ce = ch[k].get("C", {}).get("px")
            pe = ch[kp].get("P", {}).get("px")
            if ce is None or pe is None:
                continue
            x = reversal(ce, pe, k, kp, spot, T)
            if x is not None:
                X[k] = x

        st.markdown("<div style='font-size:15px;color:" + GRAY + "'>"
                    + coin + " | Expiry " + expiry + "</div>"
                    "<div style='font-size:32px;font-weight:700'>"
                    "Spot {:,.2f}</div>".format(spot),
                    unsafe_allow_html=True)

        if not res or not sup:
            st.warning("Support ya resistance nahi mila. "
                       "OI/Volume ka data khali ho sakta hai.")
        else:
            def desc(side, name):
                t = name + " " + "{:,.0f}".format(side["k"])
                if side["state"] == "S":
                    t += " | STRONG"
                else:
                    t += " | " + side["state"] + " "
                    t += "{:,.0f}".format(side["weak_k"])
                    t += " ({:.1f}%)".format(side["pct"])
                return t

            n = STATE_TO_NO[(sup["state"], res["state"])]
            bg = "#334155"
            if n in (1,):
                bg = "#166534"
            elif n in (8, 9):
                bg = "#92400e"
            h = "<b style='font-size:20px'>Scenario " + str(n)
            h += "</b> (course ki list me " + str(COURSE_NO[n]) + ")"
            h += "<br>" + NOTE[n] + "<br>"
            h += desc(res, "Resistance") + "<br>"
            h += desc(sup, "Support")
            st.markdown("<div style='background:" + bg
                        + ";padding:12px 14px;border-radius:12px;"
                        "color:#fff;line-height:1.7'>" + h + "</div>",
                        unsafe_allow_html=True)

            names = ["Safe Bottom", "Risky Bottom",
                     "Risky Top", "Safe Top"]
            rows = []
            for ri, tok in enumerate(SC[n]):
                if tok is None:
                    rows.append({"Level": names[ri], "Token": "-",
                                 "Strike": "-", "Price": "-",
                                 "Spot se": "-"})
                    continue
                k, px = resolve(tok, ri, sup, res, ks, X)
                if px is None:
                    rows.append({"Level": names[ri], "Token": tok,
                                 "Strike": "-" if k is None
                                 else "{:,.0f}".format(k),
                                 "Price": "nahi nikla",
                                 "Spot se": "-"})
                else:
                    rows.append({"Level": names[ri], "Token": tok,
                                 "Strike": "{:,.0f}".format(k),
                                 "Price": "{:,.2f}".format(px),
                                 "Spot se": "{:+.2f}%".format(
                                     (px - spot) / spot * 100)})
            st.dataframe(rows, hide_index=True)
            st.caption("Token: EOS/EOR = support/resistance ka reversal. "
                       "+1/-1 = ek strike upar/neeche. "
                       "WTT/WTB = kamzor strike. "
                       "Ye kitaabi niyam hai, test nahi hua, "
                       "trade ki salah nahi.")
        with st.expander("Raw data (pehli row)"):
            st.json(data[0])
except Exception as ex:
    st.error("Error: " + str(ex))
