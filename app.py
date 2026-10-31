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
MAX_HOLD = 7200
COOLDOWN = 300

GREEN = "#16a34a"
RED = "#dc2626"
AMBER = "#f59e0b"
GRAY = "#94a3b8"

st.set_page_config(page_title="UNI सिग्नल परख")
st_autorefresh(interval=10000, key="log")


def j(path, **p):
    r = requests.get(BASE + "/" + path, params=p, timeout=10)
    return r.json()["result"]


def ist(t):
    d = datetime.fromtimestamp(t, timezone.utc)
    d = d + timedelta(hours=5, minutes=30)
    return d.strftime("%d-%m %H:%M")


def box(bg, html):
    s = "background:" + bg + ";padding:14px 16px;"
    s += "border-radius:12px;margin:8px 0;color:#fff;"
    s += "line-height:1.8;font-size:16px"
    st.markdown("<div style='" + s + "'>" + html + "</div>",
                unsafe_allow_html=True)


hist = st.session_state.setdefault("hist_log", [])
closed = st.session_state.setdefault("closed_log", [])
if "open_log" not in st.session_state:
    st.session_state["open_log"] = None
    st.session_state["last_close"] = 0

try:
    now = time.time()
    t = j("tickers/" + SYMBOL)
    price = float(t["mark_price"])

    end = int(now)
    c = j("history/candles", symbol=SYMBOL, resolution="5m",
          start=end - N * 600, end=end)
    c = c[-N:]
    res = max(x["high"] for x in c)
    sup = min(x["low"] for x in c)

    ob = j("l2orderbook/" + SYMBOL)
    bsz = sum(float(x["size"]) for x in ob["buy"][:20])
    asz = sum(float(x["size"]) for x in ob["sell"][:20])
    imb = bsz / max(1, asz)

    tr = j("trades/" + SYMBOL)[:100]
    tb = 0.0
    ts = 0.0
    for x in tr:
        if x.get("buyer_role") == "taker":
            tb += float(x["size"])
        else:
            ts += float(x["size"])

    hist.append({"price": price, "imb": imb,
                 "tbr": tb / max(1, tb + ts)})
    del hist[:-WINDOW]
    n = len(hist)
    avg_imb = sum(h["imb"] for h in hist) / n
    avg_tbr = sum(h["tbr"] for h in hist) / n
    trend = price - hist[0]["price"]
    score = int(avg_imb > 1) + int(avg_tbr > 0.5) + int(trend > 0)

    op = st.session_state["open_log"]

    if op:
        e = op["entry"]
        risk_u = abs(e - op["sl"])
        if op["side"] == "LONG":
            hit_sl = price <= op["sl"]
            hit_tp = price >= op["target"]
            pnl = price - e
        else:
            hit_sl = price >= op["sl"]
            hit_tp = price <= op["target"]
            pnl = e - price
        code = None
        r = 0.0
        if hit_tp:
            code = "TARGET"
            r = op["rr"]
        elif hit_sl:
            code = "STOP"
            r = -1.0
        elif now - op["t"] > MAX_HOLD:
            code = "TIMEOUT"
            r = pnl / risk_u
        if code:
            closed.append({"t": op["t"], "side": op["side"],
                           "entry": e, "sl": op["sl"],
                           "target": op["target"],
                           "code": code, "r": r})
            st.session_state["open_log"] = None
            st.session_state["last_close"] = now
            op = None

    gap = now - st.session_state["last_close"]
    if op is None and n >= MIN_SAMPLES and gap > COOLDOWN:
        at_sup = abs(price - sup) / price <= NEAR
        at_res = abs(price - res) / price <= NEAR
        new = None
        if at_sup and score >= 2:
            sl = sup * (1 - SLBUF)
            tg = res if res > price else price + 2 * (price - sl)
            new = ("LONG", sl, tg)
        elif at_res and score <= 1:
            sl = res * (1 + SLBUF)
            tg = sup if sup < price else price - 2 * (sl - price)
            new = ("SHORT", sl, tg)
        if new:
            rr = abs(new[2] - price) / abs(price - new[1])
            op = {"t": now, "side": new[0], "entry": price,
                  "sl": new[1], "target": new[2], "rr": rr}
            st.session_state["open_log"] = op

    st.markdown("<div style='font-size:15px;color:" + GRAY + "'>"
                + SYMBOL + " | नकली (पेपर) ट्रेड की परख</div>"
                "<div style='font-size:34px;font-weight:700'>"
                + "{:.4f}".format(price) + "</div>",
                unsafe_allow_html=True)

    if n < MIN_SAMPLES:
        box("#92400e", "डेटा इकट्ठा हो रहा है ("
            + str(n) + "/" + str(MIN_SAMPLES) + ")")

    if op:
        if op["side"] == "LONG":
            bg = "#166534"
            name = "🟢 लॉन्ग"
        else:
            bg = "#991b1b"
            name = "🔴 शॉर्ट"
        h = "<b style='font-size:18px'>खुली ट्रेड: " + name + "</b><br>"
        h += "एंट्री {:.4f} | ".format(op["entry"])
        h += "स्टॉप {:.4f}<br>".format(op["sl"])
        h += "टारगेट {:.4f} | ".format(op["target"])
        h += "रिस्क:रिवॉर्ड 1:{:.1f}<br>".format(op["rr"])
        h += "शुरू: " + ist(op["t"])
        box(bg, h)
    elif n >= MIN_SAMPLES:
        box("#334155", "⏳ अभी कोई खुली ट्रेड नहीं, सेटअप का इंतज़ार")

    total = len(closed)
    wins = len([x for x in closed if x["r"] > 0])
    sum_r = sum(x["r"] for x in closed)
    pct = 100 * wins / total if total else 0
    if sum_r > 0:
        col = GREEN
    elif sum_r < 0:
        col = RED
    else:
        col = GRAY
    h = "<div style='border:2px solid " + col
    h += ";border-radius:10px;padding:10px 12px;margin:8px 0'>"
    h += "<b>कुल ट्रेड: " + str(total) + "</b> | "
    h += "जीत: " + str(wins) + " ({:.0f}%)<br>".format(pct)
    h += "<span style='color:" + col + ";font-size:20px;"
    h += "font-weight:700'>कुल R: {:+.2f}</span></div>".format(sum_r)
    st.markdown(h, unsafe_allow_html=True)
    if 0 < total < 30:
        st.caption("30 से कम ट्रेड पर नतीजे पर भरोसा मत करना, "
                   "ये संयोग भी हो सकता है")

    names = {"TARGET": "टारगेट", "STOP": "स्टॉप",
             "TIMEOUT": "समय ख़त्म"}
    for x in reversed(closed[-10:]):
        cc = GREEN if x["r"] > 0 else RED
        line = ist(x["t"]) + "  " + x["side"]
        line += "  {:.4f}  ".format(x["entry"])
        line += names[x["code"]] + "  {:+.2f}R".format(x["r"])
        st.markdown("<div style='color:" + cc + ";font-size:14px;"
                    "font-family:monospace'>" + line + "</div>",
                    unsafe_allow_html=True)

    if closed:
        lines = ["time,side,entry,sl,target,result,R"]
        for x in closed:
            lines.append("{},{},{:.4f},{:.4f},{:.4f},{},{:.2f}".format(
                ist(x["t"]), x["side"], x["entry"], x["sl"],
                x["target"], x["code"], x["r"]))
        st.download_button("CSV डाउनलोड करें", "\n".join(lines),
                           file_name="uni_paper_trades.csv")
except Exception as ex:
    st.error("त्रुटि: " + str(ex))
