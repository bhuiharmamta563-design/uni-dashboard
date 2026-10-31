import time, requests, streamlit as st
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
SYMBOL = "UNIUSD"
N = 20

st.set_page_config(page_title="UNI Dashboard")
st_autorefresh(interval=10000, key="r")

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

    prev = st.session_state.get("prev")
    dp = price - prev[0] if prev else 0
    do = oi - prev[1] if prev else 0
    st.session_state["prev"] = (price, oi)

    ob = j(f"l2orderbook/{SYMBOL}")
    bids = [(float(x["price"]), float(x["size"])) for x in ob["buy"][:20]]
    asks = [(float(x["price"]), float(x["size"])) for x in ob["sell"][:20]]
    bsz = sum(s for _, s in bids); asz = sum(s for _, s in asks)
    imb = round(bsz / max(1, asz), 2)
    bwall = max(bids, key=lambda x: x[1]); awall = max(asks, key=lambda x: x[1])

    tr = j(f"trades/{SYMBOL}")[:100]
    tb = sum(float(x["size"]) for x in tr if x.get("buyer_role") == "taker")
    ts = sum(float(x["size"]) for x in tr if x.get("buyer_role") != "taker")

    score = (imb > 1) + (tb > ts) + (dp > 0)
    bias = ["Bearish", "Halka Bearish", "Halka Bullish", "Bullish"][score]
    zone = "Break Out" if price > res else "Break Down" if price < sup else "Range"

    st.title(f"{SYMBOL}  {price}")
    st.subheader(f"Signal: {bias} ({score}/3) | {zone}")
    st.write(f"OI: {oi:,.0f} | OI change: {do:,.0f} | Funding: {t.get('funding_rate')}")
    st.write(f"Bids/Asks: {bsz:.0f} / {asz:.0f} (ratio {imb})")
    st.write(f"Upar sell wall: {awall[0]} ({awall[1]:.0f})")
    st.write(f"Neeche buy wall: {bwall[0]} ({bwall[1]:.0f})")
    st.write(f"Taker buy {tb:.0f} | Taker sell {ts:.0f}")
    st.write(f"Resistance: {res} | Support: {sup}")
    st.caption(time.strftime("Updated %H:%M:%S"))
except Exception as e:
    st.error(f"Error: {e}")
