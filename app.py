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

st.set_page_config(page_title="UNI Entry Dashboard")
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


def ist(ts):
    d = datetime.fromtimestamp(ts, timezone.utc)
    d = d + timedelta(hours=5, minutes=30)
    return d.strftime("%d-%m %H:%M")


hist = st.session_state.setdefault("hist", [])
closed = st.session_state.setdefault("closed", [])
if "open_trade" not in st.session_state:
    st.session_state["open_trade"] = None
    st.session_state["last_close_t"] = 0

risk = st.number_input("Ek trade mein kitna risk (USD)",
                       min_value=0.5, value=5.0, step=0.5)

try:
    now = time.time()
    t = j("tickers/" + SYMBOL)
    price = float(t["mark_price"])
    oi = float(t.get("oi") or 0)

    end = int(now)
    c = j("history/candles", symbol=SYMBOL, resolution="5m",
          start=end - N * 600, end=end)
        c = sorted(c, key=lambda x: x["time"])[-N:]
    st.caption("Candles: " + ist(c[0]["time"]) + " se " + ist(c[-1]["time"]))
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

    hist.append({"price": price, "oi": oi, "imb": imb,
                 "tbr": tb / max(1, tb + ts)})
    del hist[:-WINDOW]
    n = len(hist)
    avg_imb = sum(h["imb"] for h in hist) / n
    avg_tbr = sum(h["tbr"] for h in hist) / n
    trend = price - hist[0]["price"]
    oi_chg = oi - hist[0]["oi"]

    score = int(avg_imb > 1) + int(avg_tbr > 0.5) + int(trend > 0)
    names = ["Bearish", "Halka Bearish", "Halka Bullish", "Bullish"]
    cols = [RED, "#ea580c", "#65a30d", GREEN]
    bias = names[score]
    bias_col = cols[score]

    at_sup = abs(price - sup) / price <= NEAR
    at_res = abs(price - res) / price <= NEAR

    setup = "WAIT"
    entry = None
    sl = None
    target = None
    msg = ""

    if n < MIN_SAMPLES:
        msg = "Data ikattha ho raha hai (" + str(n) + "/"
        msg += str(MIN_SAMPLES) + "), thoda ruko"
    elif at_sup and score >= 2:
        setup = "LONG"
        entry = price
        sl = sup * (1 - SLBUF)
        if res > entry:
            target = res
        else:
            target = entry + 2 * (entry - sl)
    elif at_res and score <= 1:
        setup = "SHORT"
        entry = price
        sl = res * (1 + SLBUF)
        if sup < entry:
            target = sup
        else:
            target = entry - 2 * (sl - entry)
    elif score >= 2:
        msg = "Bullish hai, par price support ke paas nahi. "
        msg += "Pullback ka wait karo"
    elif score <= 1:
        msg = "Bearish hai, par price resistance ke paas nahi. "
        msg += "Bounce ka wait karo"
    else:
        msg = "Abhi koi saaf entry nahi"

    # ---- paper trade: khuli trade ki jaanch ----
    op = st.session_state["open_trade"]
    if op:
        e0 = op["entry"]
        risk_u = abs(e0 - op["sl"])
        if op["side"] == "LONG":
            hit_sl = price <= op["sl"]
            hit_tp = price >= op["target"]
            pnl = price - e0
        else:
            hit_sl = price >= op["sl"]
            hit_tp = price <= op["target"]
            pnl = e0 - price
        code = None
        rv = 0.0
        if hit_tp:
            code = "TARGET"
            rv = op["rr"]
        elif hit_sl:
            code = "STOP"
            rv = -1.0
        elif now - op["t"] > MAX_HOLD:
            code = "TIMEOUT"
            rv = pnl / risk_u
        if code:
            closed.append({"t": op["t"], "side": op["side"],
                           "entry": e0, "sl": op["sl"],
                           "target": op["target"],
                           "code": code, "r": rv})
            st.session_state["open_trade"] = None
            st.session_state["last_close_t"] = now
            op = None

    gap = now - st.session_state["last_close_t"]
    if op is None and setup != "WAIT" and gap > COOLDOWN:
        rr0 = abs(target - entry) / abs(entry - sl)
        op = {"t": now, "side": setup, "entry": entry,
              "sl": sl, "target": target, "rr": rr0}
        st.session_state["open_trade"] = op

    # ---- upar: entry dashboard ----
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

    if trend > 0:
        pcol = GREEN
        arrow = "▲"
    elif trend < 0:
        pcol = RED
        arrow = "▼"
    else:
        pcol = AMBER
        arrow = "●"

    h = "<div style='font-size:15px;color:" + GRAY + "'>"
    h += SYMBOL + "</div>"
    h += "<div style='font-size:42px;font-weight:700;"
    h += "color:" + pcol + "'>" + arrow + " "
    h += "{:.4f}".format(price) + "</div>"
    st.markdown(h, unsafe_allow_html=True)

    dots = "●" * score + "○" * (3 - score)
    h = "<b style='font-size:20px'>Trend: " + bias + "</b><br>"
    h += "<span style='font-size:22px;letter-spacing:4px'>"
    h += dots + "</span> (" + str(score) + "/3)"
    box(bias_col, h)

    if setup != "WAIT":
        rr = abs(target - entry) / abs(entry - sl)
        lots = int(risk / abs(entry - sl))
        if setup == "LONG":
            bg = "#166534"
            icon = "🟢"
        else:
            bg = "#991b1b"
            icon = "🔴"
        h = "<b style='font-size:20px'>" + icon + " " + setup
        h += " setup</b><br>"
        h += "Entry ~ <b>{:.4f}</b><br>".format(entry)
        h += "Stop-loss: <b>{:.4f}</b><br>".format(sl)
        h += "Target: <b>{:.4f}</b><br>".format(target)
        h += "Risk:Reward = <b>1:{:.1f}</b><br>".format(rr)
        h += str(risk) + " USD risk par quantity: <b>"
        h += str(lots) + " lot</b> (1 lot = 1 UNI)"
        if rr < 1.5:
            h += "<br>⚠️ RR kam hai, entry sochkar lena"
        box(bg, h)
    else:
        box("#92400e", "<b>⏳ WAIT</b><br>" + msg)

    two("Support", str(sup), GREEN, "Resistance", str(res), RED)
    two("Neeche buy wall", str(bwall[0]), GREEN,
        "Upar sell wall", str(awall[0]), RED)

    bar(100 * bsz / max(1, bsz + asz),
        "Bids {:.0f}".format(bsz), "Asks {:.0f}".format(asz))
    bar(avg_tbr * 100,
        "Taker buy {:.0f}% (10 min)".format(avg_tbr * 100),
        "Taker sell {:.0f}%".format(100 - avg_tbr * 100))

    fund = float(t.get("funding_rate") or 0)
    h = "<div style='margin-top:14px;color:" + GRAY
    h += ";font-size:14px'>"
    h += "OI: {:,.0f} | OI change (10 min): ".format(oi)
    h += "<b style='color:#38bdf8'>{:,.0f}</b> | ".format(oi_chg)
    h += "Funding: {:.4f}<br>".format(fund)
    h += ist_now.strftime("Updated %H:%M:%S IST") + "</div>"
    st.markdown(h, unsafe_allow_html=True)

    # ---- neeche: paper trade parakh ----
    st.markdown("<hr style='border-color:#334155;margin:22px 0 10px'>"
                "<div style='font-size:18px;font-weight:700'>"
                "Signal parakh (nakli trade, asli paisa nahi)</div>",
                unsafe_allow_html=True)

    if op:
        if op["side"] == "LONG":
            bg = "#166534"
            name = "🟢 LONG"
        else:
            bg = "#991b1b"
            name = "🔴 SHORT"
        h = "<b style='font-size:18px'>Khuli trade: " + name + "</b><br>"
        h += "Entry {:.4f} | ".format(op["entry"])
        h += "Stop {:.4f}<br>".format(op["sl"])
        h += "Target {:.4f} | ".format(op["target"])
        h += "RR 1:{:.1f}<br>".format(op["rr"])
        h += "Shuru: " + ist(op["t"])
        box(bg, h)
    else:
        box("#334155", "⏳ Abhi koi khuli nakli trade nahi")

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
    h += "<b>Kul trade: " + str(total) + "</b> | "
    h += "Jeet: " + str(wins) + " ({:.0f}%)<br>".format(pct)
    h += "<span style='color:" + col + ";font-size:20px;"
    h += "font-weight:700'>Kul R: {:+.2f}</span></div>".format(sum_r)
    st.markdown(h, unsafe_allow_html=True)
    if 0 < total < 30:
        st.caption("30 se kam trade par natije par bharosa mat karna")

    rnames = {"TARGET": "Target", "STOP": "Stop", "TIMEOUT": "Samay khatam"}
    for x in reversed(closed[-10:]):
        cc = GREEN if x["r"] > 0 else RED
        line = ist(x["t"]) + "  " + x["side"]
        line += "  {:.4f}  ".format(x["entry"])
        line += rnames[x["code"]] + "  {:+.2f}R".format(x["r"])
        st.markdown("<div style='color:" + cc + ";font-size:14px;"
                    "font-family:monospace'>" + line + "</div>",
                    unsafe_allow_html=True)

    if closed:
        lines = ["time,side,entry,sl,target,result,R"]
        for x in closed:
            lines.append("{},{},{:.4f},{:.4f},{:.4f},{},{:.2f}".format(
                ist(x["t"]), x["side"], x["entry"], x["sl"],
                x["target"], x["code"], x["r"]))
        st.download_button("CSV download karo", "\n".join(lines),
                           file_name="uni_paper_trades.csv")
except Exception as e:
    st.error("Error: " + str(e))
