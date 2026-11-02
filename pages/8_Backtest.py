import time
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone

BASE = "https://api.india.delta.exchange/v2"
IST = timezone(timedelta(hours=5, minutes=30))

GREEN = "#16a34a"
RED = "#dc2626"
GRAY = "#94a3b8"

st.set_page_config(page_title="Backtest")

try:
    from common import pick_coin
    SYMBOL = pick_coin()
except Exception:
    SYMBOL = st.selectbox("Coin", ["UNIUSD", "AVAXUSD"])


def to_sec(t):
    t = float(t)
    if t > 1e14:
        return t / 1e6
    if t > 1e11:
        return t / 1e3
    return t


@st.cache_data(ttl=1800)
def fetch(symbol, res, start, end):
    sec = {"5m": 300, "15m": 900}[res]
    out = {}
    s = start
    step = 1500 * sec
    while s < end:
        e = min(end, s + step)
        r = requests.get(BASE + "/history/candles",
                         params={"symbol": symbol, "resolution": res,
                                 "start": s, "end": e}, timeout=20)
        for x in r.json()["result"]:
            t = to_sec(x.get("time", 0))
            out[t] = {"t": t, "o": float(x["open"]),
                      "h": float(x["high"]), "l": float(x["low"]),
                      "c": float(x["close"])}
        s = e
    return [out[k] for k in sorted(out)]


def run_bt(cs, N, near, slbuf, min_rr, max_hold, cost_pct,
           filt, sides, cooldown):
    trades = []
    n = len(cs)
    i = N
    while i < n - 1:
        win = cs[i - N:i]
        res = max(x["h"] for x in win)
        sup = min(x["l"] for x in win)
        price = cs[i]["c"]
        side = None
        if sup <= price <= sup * (1 + near):
            side = "LONG"
        elif res * (1 - near) <= price <= res:
            side = "SHORT"
        if side is None or (sides != "Dono" and side != sides):
            i += 1
            continue
        if filt == "Rang palta (reversal candle)":
            green = cs[i]["c"] > cs[i]["o"]
            if (side == "LONG") != green:
                i += 1
                continue
        elif filt == "Momentum saath me (2 candle)":
            up = cs[i]["c"] > cs[i - 2]["c"]
            if (side == "LONG") != up:
                i += 1
                continue
        entry = cs[i + 1]["o"]
        if side == "LONG":
            sl = sup * (1 - slbuf)
            target = res if res > entry else entry + 2 * (entry - sl)
            ok = sl < entry < target
        else:
            sl = res * (1 + slbuf)
            target = sup if sup < entry else entry - 2 * (sl - entry)
            ok = target < entry < sl
        if not ok:
            i += 1
            continue
        risk_u = abs(entry - sl)
        rr = abs(target - entry) / risk_u
        if rr < min_rr:
            i += 1
            continue
        last = min(n - 1, i + max_hold)
        code = None
        gross = 0.0
        j = i + 1
        while j <= last:
            o = cs[j]["o"]
            hi = cs[j]["h"]
            lo = cs[j]["l"]
            if side == "LONG":
                if j > i + 1 and o <= sl:
                    code = "STOP"
                    gross = (o - entry) / risk_u
                    break
                if lo <= sl:
                    code = "STOP"
                    gross = -1.0
                    break
                if hi >= target:
                    code = "TARGET"
                    gross = rr
                    break
            else:
                if j > i + 1 and o >= sl:
                    code = "STOP"
                    gross = (entry - o) / risk_u
                    break
                if hi >= sl:
                    code = "STOP"
                    gross = -1.0
                    break
                if lo <= target:
                    code = "TARGET"
                    gross = rr
                    break
            j += 1
        if code is None:
            j = last
            code = "TIMEOUT"
            if side == "LONG":
                gross = (cs[j]["c"] - entry) / risk_u
            else:
                gross = (entry - cs[j]["c"]) / risk_u
        cost = cost_pct / 100.0 * entry / risk_u
        d = datetime.fromtimestamp(cs[i + 1]["t"], IST)
        iso = d.isocalendar()
        trades.append({"Time": d.strftime("%d-%m %H:%M"),
                       "Week": "{}-W{:02d}".format(iso[0], iso[1]),
                       "Side": side, "Entry": round(entry, 4),
                       "SL": round(sl, 4), "Target": round(target, 4),
                       "Result": code, "Bars": j - i,
                       "R": round(gross - cost, 3)})
        i = j + max(1, cooldown)
    return trades


def stats(trades):
    rs = [t["R"] for t in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    curve = []
    streak = 0
    worst = 0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
        curve.append(eq)
        if r <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    gl = abs(sum(losses))
    pf = (sum(wins) / gl) if gl > 0 else None
    return {"n": n, "wins": len(wins), "total": sum(rs),
            "avg": (sum(rs) / n) if n else 0.0, "pf": pf,
            "mdd": mdd, "streak": worst, "curve": curve}


days = st.slider("Kitne din ka data", 3, 30, 14)
res_tf = st.radio("Candle", ["5m", "15m"], horizontal=True)
with st.expander("Settings (dashboard jaisi)"):
    N = st.number_input("Support/Resistance ke liye pichli candles",
                        value=20, min_value=5, max_value=100)
    near = st.number_input("Level ke kitne % paas entry", value=0.30,
                           step=0.05) / 100
    slbuf = st.number_input("Stop-loss level se kitna % door",
                            value=0.20, step=0.05) / 100
    min_rr = st.number_input("Kam se kam risk:reward", value=1.0,
                             step=0.25)
    max_hold = st.number_input("Zyada se zyada kitni candle tak rukna",
                               value=24, min_value=2)
    cost_pct = st.number_input("Kul kharcha har trade par (% price, "
                               "fee + slippage)", value=0.10, step=0.01)
    filt = st.selectbox("Filter", ["Koi nahi",
                                   "Rang palta (reversal candle)",
                                   "Momentum saath me (2 candle)"])
    sides = st.selectbox("Disha", ["Dono", "LONG", "SHORT"])
    cooldown = st.number_input("Trade ke baad kitni candle ruko",
                               value=1, min_value=1)

if st.button("Backtest chalao"):
    st.session_state["bt_done"] = True

if st.session_state.get("bt_done"):
    try:
        end = int(time.time())
        cs = fetch(SYMBOL, res_tf, end - days * 86400, end)
        if len(cs) < int(N) + 10:
            st.warning("Kaafi candles nahi mili.")
        else:
            tr = run_bt(cs, int(N), near, slbuf, min_rr, int(max_hold),
                        cost_pct, filt, sides, int(cooldown))
            s = stats(tr)
            st.write(SYMBOL, "|", len(cs), "candles |",
                     datetime.fromtimestamp(cs[0]["t"], IST)
                     .strftime("%d-%m"), "se",
                     datetime.fromtimestamp(cs[-1]["t"], IST)
                     .strftime("%d-%m"))
            if s["n"] == 0:
                st.info("Is setting par koi trade nahi bani.")
            else:
                col = GREEN if s["total"] > 0 else RED
                h = "<div style='border:2px solid " + col
                h += ";border-radius:10px;padding:10px 12px'>"
                h += "<b>Kul trade: " + str(s["n"]) + "</b> | "
                h += "Jeet: {:.0f}%<br>".format(100 * s["wins"] / s["n"])
                h += "<span style='color:" + col
                h += ";font-size:22px;font-weight:700'>"
                h += "Kul R (kharche ke baad): {:+.1f}</span><br>".format(
                    s["total"])
                h += "Ausat R/trade: {:+.3f} | ".format(s["avg"])
                pf = "-" if s["pf"] is None else "{:.2f}".format(s["pf"])
                h += "Profit factor: " + pf + "<br>"
                h += "Sabse bada girna: {:.1f}R | ".format(s["mdd"])
                h += "Lagatar haar: " + str(s["streak"]) + "</div>"
                st.markdown(h, unsafe_allow_html=True)
                if s["n"] < 30:
                    st.warning("30 se kam trade: nateeja bharose ke "
                               "laayak nahi.")
                st.line_chart(s["curve"])
                weeks = {}
                for t in tr:
                    weeks.setdefault(t["Week"], []).append(t["R"])
                wk = []
                for k in sorted(weeks):
                    wk.append({"Hafta": k, "Trade": len(weeks[k]),
                               "R": round(sum(weeks[k]), 2)})
                st.write("Hafte ke hisaab se (kitne hafte kamai me?)")
                st.dataframe(wk, hide_index=True)

                with st.expander("Alag settings par bhi wahi nateeja?"):
                    chk = []
                    for nn in [10, 20, 40]:
                        t2 = run_bt(cs, nn, near, slbuf, min_rr,
                                    int(max_hold), cost_pct, filt,
                                    sides, int(cooldown))
                        s2 = stats(t2)
                        chk.append({"Candles": nn, "Trade": s2["n"],
                                    "Kul R": round(s2["total"], 1)})
                    st.dataframe(chk, hide_index=True)
                    st.caption("Agar ek hi setting me kamai aur baaki me "
                               "nuksan ho, to wo sirf sanyog ho sakta hai.")
                with st.expander("Saari trades"):
                    st.dataframe(tr, hide_index=True)
                    lines = ["time,side,entry,sl,target,result,bars,R"]
                    for t in tr:
                        lines.append("{},{},{},{},{},{},{},{}".format(
                            t["Time"], t["Side"], t["Entry"], t["SL"],
                            t["Target"], t["Result"], t["Bars"], t["R"]))
                    st.download_button("CSV download karo",
                                       "\n".join(lines),
                                       file_name="backtest.csv")
            st.caption("Sirf price-level rules parakhe gaye. Order book "
                       "aur taker flow ka purana data nahi hota, isliye "
                       "wo shamil nahi. Funding aur partial fill bhi "
                       "nahi. Pichla nateeja aage ki guarantee nahi.")
    except Exception as ex:
        st.error("Error: " + str(ex))
