import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
SYMBOL = "UNIUSD"
ROWS = 15

GREEN = "#16a34a"
RED = "#dc2626"
GRAY = "#94a3b8"

st.set_page_config(page_title="UNI Order Book")
st_autorefresh(interval=3000, key="ob")


def get_book():
    r = requests.get(BASE + "/l2orderbook/" + SYMBOL, timeout=8)
    return r.json()["result"]


def with_total(levels):
    out = []
    cum = 0.0
    for p, s in levels:
        cum += s
        out.append((p, s, cum))
    return out


def line(price, size, total, col, pct, big):
    if col == RED:
        rgb = "220,38,38"
    else:
        rgb = "22,163,74"
    bg = "linear-gradient(to left, rgba(" + rgb + ",0.40) "
    bg += str(round(pct)) + "%, transparent "
    bg += str(round(pct)) + "%)"
    s = "display:flex;justify-content:space-between;"
    s += "padding:4px 8px;font-family:monospace;font-size:15px;"
    s += "border-radius:4px;margin:1px 0;background:" + bg
    if big:
        s += ";border:1px solid " + col
    h = "<div style='" + s + "'>"
    h += "<span style='color:" + col + ";font-weight:700'>"
    h += "{:.4f}".format(price) + "</span>"
    h += "<span>{:,.0f}</span>".format(size)
    h += "<span style='color:" + GRAY + "'>"
    h += "{:,.0f}</span></div>".format(total)
    return h


try:
    ob = get_book()
    bids = []
    for x in ob["buy"]:
        bids.append((float(x["price"]), float(x["size"])))
    asks = []
    for x in ob["sell"]:
        asks.append((float(x["price"]), float(x["size"])))

    bids = sorted(bids, key=lambda x: -x[0])[:ROWS]
    asks = sorted(asks, key=lambda x: x[0])[:ROWS]

    top = max(s for _, s in bids + asks)
    big_a = max(asks, key=lambda x: x[1])[0]
    big_b = max(bids, key=lambda x: x[1])[0]
    a = with_total(asks)
    b = with_total(bids)

    spread = asks[0][0] - bids[0][0]
    bsum = sum(s for _, s in bids)
    asum = sum(s for _, s in asks)

    st.markdown("<div style='font-size:15px;color:" + GRAY + "'>"
                + SYMBOL + " | order book (1 lot = 1 UNI)</div>",
                unsafe_allow_html=True)

    h = "<div style='display:flex;justify-content:space-between;"
    h += "padding:4px 8px;color:" + GRAY + ";font-size:13px'>"
    h += "<span>Price</span><span>Order (lot)</span>"
    h += "<span>Kul jod</span></div>"

    for p, s, t in reversed(a):
        h += line(p, s, t, RED, 100 * s / top, p == big_a)

    h += "<div style='text-align:center;padding:6px;margin:4px 0;"
    h += "background:#1e293b;border-radius:6px;font-size:14px'>"
    h += "Spread: {:.4f}</div>".format(spread)

    for p, s, t in b:
        h += line(p, s, t, GREEN, 100 * s / top, p == big_b)

    st.markdown(h, unsafe_allow_html=True)

    ratio = bsum / max(1, asum)
    if ratio > 1:
        rcol = GREEN
    else:
        rcol = RED
    st.markdown("<div style='margin-top:12px;font-size:15px'>"
                "Buy orders: <b style='color:" + GREEN + "'>"
                + "{:,.0f}".format(bsum) + "</b> | Sell orders: "
                "<b style='color:" + RED + "'>"
                + "{:,.0f}".format(asum) + "</b><br>"
                "Ratio: <b style='color:" + rcol + "'>"
                + "{:.2f}".format(ratio) + "</b></div>",
                unsafe_allow_html=True)
    st.caption("Border wali line = sabse bada order. 3 second me refresh.")
except Exception as ex:
    st.error("Error: " + str(ex))
