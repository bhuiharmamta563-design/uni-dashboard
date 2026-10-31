import time
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
SYMBOL = "UNIUSD"
N = 20
NEAR = 0.003
SLBUF = 0.002
WINDOW = 60
MIN_SAMPLES = 30

GREEN = "#16a34a"
RED = "#dc2626"
AMBER = "#f59e0b"
GRAY = "#94a3b8"

st.set_page_config(page_title="UNI एंट्री डैशबोर्ड")
st_autorefresh(interval=10000, key="r")


def box(bg, html):
    s = "background:" + bg + ";padding:14px 16px;"
    s += "border-radius:12px;margin:8px 0;color:#fff;"
    s += "line-height:1.8;font-size:16px"
    st.markdown("<div style='" + s + "'>" + html + "</div>",
                unsafe_allow_html=True)


def bar(pct, left, right):
    pct = max(0, min(100, pct))
    h = "<div style='margin:12px 0 4px;font-size:14px;"
    h += "color:" + GRAY + "'>" + left + " | " + right + "</div>"
    h += "<div style='display:flex;height:14px;"
    h += "border-radius:7px;overflow:hidden'>"
    h += "<div style='width:" + str(round(pct)) + "%;"
    h += "background:" + GREEN + "'></div>"
    h += "<div style='width:" + str(100 - round(pct)) + "%;"
    h += "background:" + RED + "'></div></div>"
    st.markdown(h, unsafe_allow_html=True)


def card(title, val, col):
    s = "flex:1;border:2px solid " + col + ";"
    s += "border-radius:10px;padding:8px 10px"
    h = "<div style='" + s + "'>"
    h += "<div style='font-size:13px;color:" + GRAY + "'>"
    h += title + "</div>"
    h += "<div style='font-size:20px;font-weight:700;"
    h += "color:" + col + "'>" + val + "</div></div>"
    return h


def two(t1, v1, c1, t2, v2, c2):
    h = "<div style='display:flex;gap:10px;margin:8px 0'>"
    h += card(t1, v1, c1) + card(t2, v2, c2) + "</div>"
    st.markdown(h, unsafe_allow_html=True)


def j(path, **p):
    r = requests.get(BASE + "/" + path, params=p, timeout=10)
    return r.json()["result"]


def biggest(levels):
    return max(levels, key=lambda x: x[1])


risk = st.number_input("एक ट्रेड में कितना रिस्क (USD)",
                       min_value=0.5, value=5.0, step=0.5)

try:
    t = j("tickers/" + SYMBOL)
    price = float(t["mark_price"])
    oi = float(t.get("oi") or 0)

    end = int(time.time())
    c = j("history/candles", symbol=SYMBOL, resolution="5m",
          start=end - N * 600, end=end)
    c = c[-N:]
    res = max(x["high"] for x in c)
    sup = min(x["low"] for x in c)

    ob = j("l2orderbook/" + SYMBOL)
    bids = []
    for x in ob["buy"][:20]:
        bids.append((float(x["price"]), float(x["size"])))
    asks = []
    for x in ob["sell"][:20]:
        asks.append((float(x["price"]), float(x["size"])))
    bsz = sum(s for _, s in bids)
    asz = sum(s for _, s in asks)
    imb = bsz / max(1, asz)
    bwall = biggest(bids)
    awall = biggest(asks)

    tr = j("trades/" + SYMBOL)[:100]
    tb = 0.0
    ts = 0.0
    for x in tr:
        if x.get("buyer_role") == "taker":
            tb += float(x["size"])
        else:
            ts += float(x["size"])

    hist = st.session_state.setdefault("hist", [])
    hist.append({"price": price, "oi": oi, "imb": imb,
                 "tbr": tb / max(1, tb + ts)})
    del hist[:-WINDOW]
    n = len(hist)
    avg_imb = sum(h["imb"] for h in hist) / n
    avg_tbr = sum(h["tbr"] for h in hist) / n
    trend = price - hist[0]["price"]
    oi_chg = oi - hist[0]["oi"]

    score = int(avg_imb > 1) + int(avg_tbr > 0.5) + int(trend > 0)
    names = ["मंदी (Bearish)", "हल्की मंदी",
             "हल्की तेज़ी", "तेज़ी (Bullish)"]
    cols = [RED, "#ea580c", "#65a30d", GREEN]
    bias = names[score]
    bias_col = cols[score]

    at_sup = abs(price - sup) / price <= NEAR
    at_res = abs(price - res) / price <= NEAR

    setup = "WAIT"
    entry = Non
