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
    bwall = max(bids, key=lambda x: x[1])
    awall = max(asks, key=lambda x: x[1])

    tr = j(f"trades/{SYMBOL}")[:100]
    tb = sum(float(x["size"]) for x in tr if x.get("buyer_role") == "taker")
    ts = sum(float(x["size"]) for x in tr if x.get("buyer_role") != "taker")

    # Pichle ~10 minute ka data jodkar stable signal
    hist = st.session_state.setdefault("hist", [])
    hist.append(dict(price=price, oi=oi, imb=imb, tbr=tb / max(1, tb + ts)))
    del hist[:-WINDOW]
    n = len(hist)
    avg_imb = sum(h["imb"] for h in hist) / n
    avg_tbr = sum(h["tbr"] for h in hist) / n
    trend = price - hist[0]["price"]
    oi_chg = oi - hist[0]["oi"]

    score = (avg_imb > 1) + (avg_tbr > 0.5) + (trend > 0)
    bias = ["Bearish", "Halka Bearish", "Halka Bullish", "Bullish"][score]
    bias_col = [RED, "#ea580c", "#65a30d", GREEN][score]

    at_sup = abs(price - sup) / price <= NEAR
    at_res = abs(price - res) / price <= NEAR

    setup, entry, sl, target = "WAIT", None, None, None
    if n < MIN_SAMPLES:
        msg = f"Data ikattha ho raha hai ({n}/{MIN_SAMPLES}), thoda ruko"
    elif at_sup and score >= 2:
        setup, entry, sl = "LONG", price, sup * (1 - SLBUF)
        target = res if res > entry else entry + 2 * (entry - sl)
    elif at_res and score <= 1:
        setup, entry, sl = "SHORT", price, res * (1 + SLBUF)
        target = sup if sup < entry else entry - 2 * (sl - entry)
    elif score >= 2:
        msg = "Bullish hai, par price support ke paas nahi. Pullback ka wait karo"
    elif score <= 1:
        msg = "Bearish hai, par price resistance ke paas nahi. Bounce ka wait karo"
    else:
        msg = "Abhi koi saaf entry nahi"

    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

    # Price header
    pcol = GREEN if trend > 0 else RED if trend < 0 else AMBER
    arrow = "▲" if trend > 0 else "▼" if trend < 0 else "●"
    st.markdown(
        f"<div style='font-size:15px;color:{GRAY}'>{SYMBOL}</div>"
        f"<div style='font-size:42px;font-weight:700;color:{pcol}'>"
        f"{arrow} {price:.4f}</div>", unsafe_allow_html=True)

    # Trend banner
    dots = "●" * score + "○" * (3 - score)
    box(bias_col, f"<b style='font-size:20px'>Trend: {bias}</b><br>"
                  f"<span style='font-size:22px;letter-spacing:4px'>{dots}</span> "
                  f"&nbsp;({score}/3)")

    # Setup box
    if setup != "WAIT":
        rr = abs(target - entry) / abs(entry - sl)
        lots = int(risk / abs(entry - sl))
        bg = "#166534" if setup == "LONG" else "#991b1b"
        icon = "🟢" if setup == "LONG" else "🔴"
        html = (f"<b style='font-size:20px'>{icon} {setup} setup</b><br>"
                f"Entry ~ <b>{entry:.4f}</b><br>"
                f"Stop-loss: <b>{sl:.4f}</b><br>"
                f"Target: <b>{target:.4f}</b><br>"
                f"Risk:Reward = <b>1:{rr:.1f}</b><br>"
                f"{risk} USD risk par quantity: <b>{lots} lot</b> (1 lot = 1 UNI)")
        if rr < 1.5:
            html += "<br>⚠️ RR kam hai, entry sochkar lena"
        box(bg, html)
    else:
        box("#92400e", f"<b>⏳ WAIT</b><br>{msg}")

    # Levels
    two("Support", f"{sup}", GREEN, "Resistance", f"{res}", RED)
    two("Neeche buy wall", f"{bwall[0]}", GREEN, "Upar sell wall", f"{awall[0]}", RED)

    # Buyers vs sellers bars
    bar(100 * bsz / max(1, bsz + asz),
        f"Bids {bsz:.0f}", f"Asks {asz:.0f}")
    bar(avg_tbr * 100,
        f"Taker buy {avg_tbr * 100:.0f}% (10 min)",
        f"Taker sell {100 - avg_tbr * 100:.0f}%")

    st.markdown(
        f"<div style='margin-top:14px;color:{GRAY};font-size:14px'>"
        f"OI: {oi:,.0f} &nbsp;|&nbsp; OI change (10 min): "
        f"<b style='color:#38bdf8'>{oi_chg:,.0f}</b> &nbsp;|&nbsp; "
        f"Funding: {float(t.get('funding_rate') or 0):.4f}<br>"
        f"{ist.strftime('Updated %H:%M:%S IST')}</div>",
        unsafe_allow_html=True)
except Exception as e:
    st.error(f"Error: {e}")
