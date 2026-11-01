import time
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
SYMBOL = "UNIUSD"
K = 3

GREEN = "#16a34a"
RED = "#dc2626"
AMBER = "#f59e0b"
GRAY = "#94a3b8"
IST = timezone(timedelta(hours=5, minutes=30))

st.set_page_config(page_title="UNI Level Touches")
st_autorefresh(interval=30000, key="tch")


def j(path, **p):
    r = requests.get(BASE + "/" + path, params=p, timeout=10)
    return r.json()["result"]


def get_candles(start, end):
    c = j("history/candles", symbol=SYMBOL, resolution="5m",
          start=start, end=end)
    c = sorted(c, key=lambda x: x.get("time", 0))
    out = []
    for x in c:
        out.append({"t": x.get("time", 0),
                    "o": float(x["open"]),
                    "h": float(x["high"]),
                    "l": float(x["low"]),
                    "c": float(x["close"])})
    return out


def swings(cs):
    pts = []
    for i in range(K, len(cs) - K):
        win = cs[i - K:i + K + 1]
        if cs[i]["h"] >= max(x["h"] for x in win):
            pts.append(cs[i]["h"])
        if cs[i]["l"] <= min(x["l"] for x in win):
            pts.append(cs[i]["l"])
    return pts


def cluster(pts, tol):
    pts = sorted(pts)
    groups = []
    for p in pts:
        if groups and abs(p - groups[-1][0]) / groups[-1][0] <= tol:
            groups[-1][1].append(p)
            groups[-1][0] = sum(groups[-1][1]) / len(groups[-1][1])
        else:
            groups.append([p, [p]])
    return [g[0] for g in groups]


def visits(cs, level, tol):
    zlo = level * (1 - tol)
    zhi = level * (1 + tol)
    n_touch = 0
    held = 0
    broke = 0
    inz = False
    side_in = "down"
    for i, x in enumerate(cs):
        zone = x["l"] <= zhi and x["h"] >= zlo
        if zone and not inz:
            inz = True
            n_touch += 1
            if i > 0:
                ref = cs[i - 1]["c"]
            else:
                ref = x["o"]
            if ref > zhi:
                side_in = "up"
            else:
                side_in = "down"
        elif (not zone) and inz:
            inz = False
            if x["c"] > zhi:
                side_out = "up"
            else:
                side_out = "down"
            if side_out == side_in:
                held += 1
            else:
                broke += 1
    return n_touch, held, broke


def row_html(r, price, tol, mx):
    lv = r["lv"]
    if lv > price:
        col = RED
        name = "Resistance"
    else:
        col = GREEN
        name = "Support"
    dist = (lv - price) / price * 100
    w = round(100 * r["n"] / mx)
    h = "<div style='border-left:4px solid " + col
    h += ";padding:6px 10px;margin:6px 0;background:#0f172a;"
    h += "border-radius:6px'>"
    h += "<div style='display:flex;justify-content:space-between;"
    h += "font-size:16px'>"
    h += "<b style='color:" + col + "'>" + name
    h += " {:.4f}</b>".format(lv)
    h += "<span style='color:" + GRAY + "'>"
    h += "{:+.2f}%</span></div>".format(dist)
    h += "<div style='font-size:14px'><b>" + str(r["n"])
    h += " baar chhua</b> | " + str(r["held"]) + " baar ruka | "
    h += str(r["broke"]) + " baar toota"
    if abs(dist) <= 2 * tol * 100:
        h += " ⚡ price abhi paas"
    h += "</div>"
    h += "<div style='height:6px;border-radius:3px;"
    h += "background:#334155;margin-top:4px'>"
    h += "<div style='width:" + str(w) + "%;height:6px;"
    h += "border-radius:3px;background:" + col + "'></div></div></div>"
    return h


period = st.radio("Samay", ["Aaj (IST 12 baje se)",
                            "Pichle 24 ghante"], horizontal=True)
tolp = st.slider("Zone chaudai (%)", 0.10, 0.50, 0.20, 0.05)
tol = tolp / 100.0

try:
    end = int(time.time())
    if period.startswith("Aaj"):
        mid = datetime.now(IST).replace(hour=0, minute=0,
                                        second=0, microsecond=0)
        start = int(mid.timestamp())
    else:
        start = end - 86400
    cs = get_candles(start, end)
    price = float(j("tickers/" + SYMBOL)["mark_price"])

    if len(cs) < 2 * K + 2:
        st.warning("Abhi kaafi candles nahi hain. Thoda ruko ya "
                   "'Pichle 24 ghante' chuno.")
    else:
        pts = swings(cs)
        pts.append(max(x["h"] for x in cs))
        pts.append(min(x["l"] for x in cs))
        rows = []
        for lv in cluster(pts, tol):
            n, held, broke = visits(cs, lv, tol)
            if n >= 2:
                rows.append({"lv": lv, "n": n,
                             "held": held, "broke": broke})
        rows.sort(key=lambda r: -r["n"])
        rows = rows[:10]

        day_hi = max(x["h"] for x in cs)
        day_lo = min(x["l"] for x in cs)
        h = "<div style='font-size:15px;color:" + GRAY + "'>"
        h += SYMBOL + " | " + str(len(cs)) + " candles (5m)</div>"
        h += "<div style='font-size:34px;font-weight:700'>"
        h += "{:.4f}</div>".format(price)
        h += "<div style='color:" + GRAY + ";font-size:14px'>"
        h += "Range: {:.4f} se {:.4f}</div>".format(day_lo, day_hi)
        st.markdown(h, unsafe_allow_html=True)

        if not rows:
            st.info("Koi level 2 baar nahi chhua gaya. "
                    "Zone chaudai badhao.")
        else:
            top = rows[0]
            st.markdown("<div style='margin:8px 0;padding:10px;"
                        "border:2px solid " + AMBER + ";"
                        "border-radius:10px'>Sabse zyada chhua gaya "
                        "level: <b>{:.4f}</b> ({} baar)</div>".format(
                            top["lv"], top["n"]),
                        unsafe_allow_html=True)
            mx = max(r["n"] for r in rows)
            rows.sort(key=lambda r: -r["lv"])
            above = [r for r in rows if r["lv"] > price]
            below = [r for r in rows if r["lv"] <= price]
            for r in above:
                st.markdown(row_html(r, price, tol, mx),
                            unsafe_allow_html=True)
            st.markdown("<div style='text-align:center;padding:4px;"
                        "background:#1e293b;border-radius:6px;"
                        "color:" + AMBER + ";font-weight:700'>"
                        "━ Price {:.4f} ━</div>".format(price),
                        unsafe_allow_html=True)
            for r in below:
                st.markdown(row_html(r, price, tol, mx),
                            unsafe_allow_html=True)

        st.caption("30 second me refresh. 'Chhua' = price level ke zone "
                   "me aaya. 'Ruka' = usi taraf laut gaya. "
                   "'Toota' = doosri taraf nikal gaya.")
except Exception as ex:
    st.error("Error: " + str(ex))
