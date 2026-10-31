
import time, requests, streamlit as st
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
SYMBOL = "UNIUSD"
N = 20            # support/resistance ke liye pichli 5m candles
NEAR = 0.003      # support/resistance ke 0.3% ke andar = entry zone
SLBUF = 0.002     # stop-loss level se 0.2% door
WINDOW = 60       # ~10 minute ka data (10 sec x 60)
MIN_SAMPLES = 30  # ~5 minute baad signal pakka maano

GREEN, RED, AMBER, GRAY = "#16a34a", "#dc2626", "#f59e0b", "#94a3b8"

st.set_page_config(page_title="UNI Entry Dashboard")
st_autorefresh(interval=10000, key="r")

def box(bg, html):
    st.markdown(
        f"<div style='background:{bg};padding:14px 16px;border-radius:12px;"
        f"margin:8px 0;color:#fff;line-height:1.8;font-size:16px'>{html}</div>",
        unsafe_allow_html=True)

def bar(pct, left, right):
    pct = max(0, min(100, pct))
    st.markdown(
        f"<div style='margin:12px 0 4px;font-size:14px;color:{GRAY}'>"
        f"{left} &nbsp;|&nbsp; {right}</div>"
        f"<div style='display:flex;height:14px;border-radius:7px;overflow:hidden'>"
        f"<div style='width:{pct:.0f}%;background:{GREEN}'></div>"
        f"<div style='width:{100 - pct:.0f}%;background:{RED}'></div></div>",
        unsafe_allow_html=True)

def two(l_title, l_val, l_col, r_title, r_val, r_col):
    st.markdown(
        f"<div style='display:flex;gap:10px;margin:8px 0'>"
        f"<div style='flex:1;border:2px solid {l_col};border-radius:10px;padding:8px 10px'>"
        f"<div style='font-size:13px;color:{GRAY}'>{l_title}</div>"
        f"<div style='font-size:20px;font-weight:700;color:{l_col}'>{l_val}</div></div>"
        f"<div style='flex:1;border:2px solid {r_col};border-radius:10px;padding:8px 10px'>"
        f"<div style='font-size:13px;color:{GRAY}'>{r_title}</div>"
        f"<div style='font-size:20px;font-weight:700;color:{r_col}'>{r_val}</div></div>"
        f"</div>", unsafe_allow_html=True)

risk = st.number_input("Ek trade mein kitna risk (USD)", min_value=0.5,
                       value=5.0, step=0.5)

def j(path, **p):
    return requests.get(f"{BASE}/{path}", params=p, timeout=10).json()["result"]

try:
    t = j(f"tickers/{SYMBOL}")
    price = float(t["mark_price"])
    oi = float(t.get("oi") or 0)

    end = int(time.time())
    c = j("history/candles", symbol=SYMBOL, resolution="5m",
          start=end - N * 600, end=end)[-N:]
    res = max(x["high"] for x in c)
    sup = min(x["low"] for x in c)

    ob = j(f"l2orderbook/{SYMBOL}")
    bids = [(float(x["price"]), float(x["size"])) for x in ob["buy"][:20]]
    asks = [(float(x["price"]), float(x["size"])) for x in ob["sell"][:20]]
    bsz = sum(s for _, s in bids)
    asz = sum(s for _, s in asks)
    imb = bsz / max(1, asz)
    bwall = max(bids,
