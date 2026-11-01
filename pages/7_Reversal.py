import math
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.india.delta.exchange/v2"
IST = timezone(timedelta(hours=5, minutes=30))

st.set_page_config(page_title="Reversal")
st_autorefresh(interval=15000, key="rev")

now = datetime.now(IST)
day = now
if now.hour * 60 + now.minute >= 17 * 60 + 30:
    day = now + timedelta(days=1)
coin = st.selectbox("Coin", ["BTC", "ETH"])
expiry = st.text_input("Expiry (DD-MM-YYYY)",
                       value=day.strftime("%d-%m-%Y"))
rows_n = st.slider("Spot ke aaspaas kitne strike", 4, 15, 8)
use_gamma = st.checkbox("Gamma bhi use karo (2nd order)", value=False)


def num(x):
    try:
        return float(x)
    except Exception:
        return None


def solve(n, den, gam):
    if den == 0:
        return None
    x = -n / den
    if use_gamma and gam and abs(gam) > 1e-12:
        disc = den * den - 2 * gam * n
        if disc >= 0:
            r = math.sqrt(disc)
            x1 = (-den + r) / gam
            x2 = (-den - r) / gam
            x = x1 if abs(x1) < abs(x2) else x2
    return x


try:
    r = requests.get(
        BASE + "/tickers",
        params={"contract_types": "call_options,put_options",
                "underlying_asset_symbols": coin,
                "expiry_date": expiry},
        timeout=10)
    res = r.json()["result"]
    spot = None
    ch = {}
    for t in res:
        k = num(t.get("strike_price"))
        if k is None:
            continue
        sp = num(t.get("spot_price"))
        if sp:
            spot = sp
        gk = t.get("greeks") or {}
        if t.get("contract_type") == "call_options":
            side = "C"
        else:
            side = "P"
        ch.setdefault(k, {})[side] = {
            "mk": num(t.get("mark_price")),
            "dl": num(gk.get("delta")),
            "gm": num(gk.get("gamma")),
        }

    if not res or not spot:
        st.warning("Data ya spot nahi mila. Expiry badal ke dekho.")
    else:
        ks = sorted(ch)
        X = {}
        dbg = []
        for i in range(1, len(ks)):
            k = ks[i]
            kp = ks[i - 1]
            ce = ch[k].get("C")
            pe = ch[kp].get("P")
            if not ce or not pe:
                continue
            vals = [ce["mk"], pe["mk"], ce["dl"], pe["dl"]]
            if None in vals:
                continue
            n = ce["mk"] - pe["mk"]
            den = ce["dl"] - pe["dl"]
            gam = (ce["gm"] or 0) - (pe["gm"] or 0)
            x = solve(n, den, gam)
            if x is not None:
                X[k] = spot + x
                dbg.append({"Strike": k, "CE mark": ce["mk"],
                            "PE neeche": pe["mk"], "gap": round(n, 2),
                            "den": round(den, 4),
                            "reversal": round(spot + x, 2)})

        below = [k for k in ks if k < spot]
        idx = len(below)
        show = ks[max(0, idx - rows_n):idx + rows_n]
        table = []
        for k in show:
            i = ks.index(k)
            nxt = ks[i + 1] if i + 1 < len(ks) else None
            eos = X.get(k)
            eor = X.get(nxt) if nxt is not None else None
            if eor is None:
                ctxt = "-"
            elif spot > eor:
                ctxt = "Break Out"
            else:
                ctxt = "{:,.2f}".format(eor)
            if eos is None:
                ptxt = "-"
            elif spot < eos:
                ptxt = "Break Down"
            else:
                ptxt = "{:,.2f}".format(eos)
            table.append({"Strike": "{:,.0f}".format(k),
                          "CE side (EOR)": ctxt,
                          "PE side (EOS)": ptxt})

        st.write("Spot:", spot)
        st.dataframe(table, hide_index=True)
        st.caption("EOS(K): CE(K) = PE(neeche wala). "
                   "EOR(K): CE(upar wala) = PE(K). "
                   "Ye delta se banaya andaaza hai, AOC ka exact "
                   "formula nahi.")
        with st.expander("Beech ke numbers (compare karne ke liye)"):
            st.dataframe(dbg, hide_index=True)
        with st.expander("Raw data (pehli row)"):
            st.json(res[0])
except Exception as ex:
    st.error("Error: " + str(ex))
