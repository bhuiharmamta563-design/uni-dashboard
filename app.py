"""
Vajra Analytics - Option Chain: Indices + Stocks (single file: app.py)

Install : pip install streamlit pandas numpy requests python-dotenv
Run     : streamlit run app.py

Secrets / .env keys:
  UPSTOX_ACCESS_TOKEN = "..."   (required, roz naya token)
  GEMINI_API_KEY      = "..."   (optional, FREE AI - aistudio.google.com)
  ANTHROPIC_API_KEY   = "..."   (optional, paid AI)
  GEMINI_MODEL        = "..."   (optional, default gemini-flash-latest)
"""
import json
import math
import os
import time
from datetime import date, timedelta
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

st.set_page_config(page_title="Vajra Analytics - Option Chain", layout="wide")

load_dotenv()
try:
    ACCESS_TOKEN = st.secrets["UPSTOX_ACCESS_TOKEN"]
except Exception:
    ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")

if not ACCESS_TOKEN:
    st.error("⚠️ Access Token nahi mila! Kripya Streamlit Secrets ya .env file check karein.")
    st.stop()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

# ---------------- Instrument selection (Index / Stock) ----------------
INDEXES = {
    "Nifty 50": "NSE_INDEX|Nifty 50",
    "Bank Nifty": "NSE_INDEX|Nifty Bank",
    "Fin Nifty": "NSE_INDEX|Nifty Fin Service",
    "Midcap Nifty": "NSE_INDEX|NIFTY MID SELECT",
    "Nifty Next 50": "NSE_INDEX|Nifty Next 50",
    "Sensex": "BSE_INDEX|SENSEX",
    "Bankex": "BSE_INDEX|BANKEX",
}
STOCKS = [
    "RELIANCE", "TATAPOWER", "TATASTEEL", "TMPV", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS",
    "AXISBANK", "KOTAKBANK", "ITC", "LT", "BHARTIARTL", "MARUTI", "BAJFINANCE", "ADANIENT", "ADANIPORTS",
    "WIPRO", "HINDUNILVR", "SUNPHARMA", "M&M", "ONGC", "COALINDIA", "VEDL", "JSWSTEEL", "HINDALCO",
    "NTPC", "POWERGRID", "BANKBARODA", "PNB", "TITAN", "DLF", "HCLTECH", "TECHM", "INDUSINDBK",
]
OTHER_STOCK = "✍️ Doosra stock (khud likhein)"


def enc(key):
    return quote(key, safe="")


def resolve_stock(symbol):
    """Stock symbol -> Upstox instrument_key (Instrument Search API), session mein yaad rakhta hai."""
    cache = st.session_state.setdefault("stock_keys", {})
    if symbol in cache:
        return cache[symbol]
    try:
        r = requests.get(
            "https://api.upstox.com/v2/instruments/search",
            headers=HEADERS,
            params={"query": symbol, "exchanges": "NSE", "segments": "EQ", "records": 10},
            timeout=20,
        ).json()
        items = [i for i in r.get("data", []) if i.get("instrument_type") == "EQ"]
        exact = [i for i in items if str(i.get("trading_symbol", "")).upper() == symbol.upper()]
        hit = (exact or items or [None])[0]
        if hit:
            cache[symbol] = hit["instrument_key"]
            return cache[symbol]
    except Exception:
        pass
    return None


st.sidebar.header("Instrument")
kind = st.sidebar.radio("Type", ["Index", "Stock"], horizontal=True)
if kind == "Index":
    inst_name = st.sidebar.selectbox("Index", list(INDEXES))
    inst_key = INDEXES[inst_name]
else:
    pick = st.sidebar.selectbox("Popular stock", STOCKS + [OTHER_STOCK])
    sym = st.sidebar.text_input("Stock symbol (jaise ITC, DLF)").strip().upper() if pick == OTHER_STOCK else pick
    if not sym:
        st.info("Sidebar mein stock ka symbol likhein (jaise ITC, DLF).")
        st.stop()
    inst_key = resolve_stock(sym)
    if not inst_key:
        st.error(f"'{sym}' ka instrument key nahi mila. Symbol sahi likhein ya Access Token check karein.")
        st.stop()
    inst_name = sym

st.title(f"📊 Live {inst_name} Option Chain & Volume Analytics")


# ---------------- Expiry dates (auto) ----------------
@st.cache_data(ttl=3600)
def fetch_expiries(inst_key):
    url = f"https://api.upstox.com/v2/option/contract?instrument_key={enc(inst_key)}"
    try:
        data = requests.get(url, headers=HEADERS).json()
        if data.get("status") == "success":
            exp = sorted({i["expiry"] for i in data.get("data", []) if i.get("expiry")})
            return [e for e in exp if e >= str(date.today())]
    except Exception as e:
        st.error(f"Expiry fetch error: {e}")
    return []


expiry_options = fetch_expiries(inst_key)
if not expiry_options:
    st.error(f"⚠️ {inst_name} ki expiry dates load nahi hui! Access Token check karein, ya is instrument ke F&O options na hon (ya symbol badal gaya ho, jaise Tata Motors ab TMPV/TMCV).")
    st.stop()

st.sidebar.header("Settings")
expiry_date = st.sidebar.selectbox("Select Expiry Date", expiry_options)
n_strikes = st.sidebar.slider("ATM ke upar/niche strikes", 5, 30, 10)
show_summary = st.sidebar.checkbox("Strike ke baad PCR / Zone dikhao", value=True)
focus_mode = st.sidebar.checkbox("📺 Focus mode (sirf option chain)", value=False)
chart_focus = st.sidebar.checkbox("📈 Charts Focus mode (sirf charts)", value=False)
auto_refresh = st.sidebar.checkbox("🔄 Auto refresh (smooth, screen wahin rahegi)", value=False)
refresh_secs = 10
if auto_refresh:
    refresh_secs = st.sidebar.selectbox("Refresh kitne second mein", [5, 10, 15, 30, 60], index=1)
if st.sidebar.button("🔄 Refresh now"):
    st.cache_data.clear()


# ---------------- Option chain data ----------------
@st.cache_data(ttl=5)
def fetch_option_chain(inst_key, expiry):
    url = (
        "https://api.upstox.com/v2/option/chain"
        f"?instrument_key={enc(inst_key)}&expiry_date={expiry}"
    )
    try:
        data = requests.get(url, headers=HEADERS).json()
        if data.get("status") != "success":
            st.error(f"API Error: {data}")
            return pd.DataFrame(), 0
        rows, spot = [], 0
        for item in data.get("data", []):
            spot = item.get("underlying_spot_price") or spot
            c = item.get("call_options", {}) or {}
            p = item.get("put_options", {}) or {}
            cm, pm = c.get("market_data", {}) or {}, p.get("market_data", {}) or {}
            cg, pg = c.get("option_greeks", {}) or {}, p.get("option_greeks", {}) or {}
            rows.append({
                "Call Vol": cm.get("volume", 0),
                "Call OI Chg": cm.get("oi", 0) - cm.get("prev_oi", 0),
                "Call OI": cm.get("oi", 0),
                "Call IV": cg.get("iv", 0),
                "Call LTP": cm.get("ltp", 0),
                "Strike": item.get("strike_price"),
                "Put LTP": pm.get("ltp", 0),
                "Put IV": pg.get("iv", 0),
                "Put OI": pm.get("oi", 0),
                "Put OI Chg": pm.get("oi", 0) - pm.get("prev_oi", 0),
                "Put Vol": pm.get("volume", 0),
            })
        return pd.DataFrame(rows), spot
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return pd.DataFrame(), 0


# ---------------- Smart analysis engine (Supply / Demand zones) ----------------
def fs(x):
    """Strike ko saaf dikhao: 25000.0 -> 25000, 392.5 -> 392.5"""
    x = float(x)
    return int(x) if x == int(x) else round(x, 2)


def strength_label(score):
    return "Strong 💪" if score >= 70 else "Medium 👍" if score >= 45 else "Weak ⚠️"


def analyze(df, spot):
    d = df.sort_values("Strike").reset_index(drop=True).copy()
    diffs = np.diff(d["Strike"].astype(float).values)
    diffs = diffs[diffs > 0]
    step = float(diffs.min()) if len(diffs) else 50.0          # strike gap (Nifty 50, BankNifty 100, stock 2.5...)

    # Score = 50% OI + 30% Volume + 20% fresh OI addition (0-100)
    for side in ("Call", "Put"):
        oi, vol = d[f"{side} OI"], d[f"{side} Vol"]
        chg = d[f"{side} OI Chg"].clip(lower=0)
        d[f"{side} Score"] = (
            0.5 * oi / max(oi.max(), 1) + 0.3 * vol / max(vol.max(), 1) + 0.2 * chg / max(chg.max(), 1)
        ) * 100

    def zone(side, mask, avoid=None):
        cand = d[mask].sort_values(f"{side} Score", ascending=False)
        if avoid:                                   # Zone 2: pehle zone se 100+ pts door
            cand = cand[(cand["Strike"] < avoid["low"] - 2 * step) | (cand["Strike"] > avoid["high"] + 2 * step)]
        if cand.empty:
            return None
        top = cand.iloc[0]
        if avoid and float(top[f"{side} Score"]) < 20:   # bahut kamzor ho to zone 2 nahi
            return None
        cluster = cand[(cand["Strike"] - top["Strike"]).abs() <= 2 * step].head(3)
        levels = []
        for _, r in cluster.iterrows():
            chg = float(r[f"{side} OI Chg"])
            levels.append({
                "strike": fs(r["Strike"]),
                "score": float(r[f"{side} Score"]),
                "oi": float(r[f"{side} OI"]),
                "vol": float(r[f"{side} Vol"]),
                "chg": chg,
                "fresh": chg > 0,
            })
        return {
            "primary": levels[0],
            "low": min(l["strike"] for l in levels),
            "high": max(l["strike"] for l in levels),
            "levels": levels,
            "score": levels[0]["score"],
        }

    supply = zone("Call", d["Strike"] >= spot)   # Resistance (Call writers)
    demand = zone("Put", d["Strike"] <= spot)    # Support (Put writers)
    supply2 = zone("Call", d["Strike"] >= spot, supply) if supply else None
    demand2 = zone("Put", d["Strike"] <= spot, demand) if demand else None

    # Liquidity strikes: jahan Call+Put dono ka OI aur Volume sabse zyada
    tot_oi, tot_vol = d["Call OI"] + d["Put OI"], d["Call Vol"] + d["Put Vol"]
    liq_score = (0.5 * tot_oi / max(tot_oi.max(), 1) + 0.5 * tot_vol / max(tot_vol.max(), 1)) * 100
    liq = [
        {"strike": fs(d.loc[i, "Strike"]), "score": float(liq_score[i]),
         "oi": float(tot_oi[i]), "vol": float(tot_vol[i])}
        for i in liq_score.sort_values(ascending=False).index[:3]
    ]

    # Max pain
    k = d["Strike"].values
    c_oi, p_oi = d["Call OI"].values, d["Put OI"].values
    pain = [(np.maximum(s - k, 0) * c_oi).sum() + (np.maximum(k - s, 0) * p_oi).sum() for s in k]
    max_pain = fs(k[int(np.argmin(pain))])

    tot_c, tot_p = float(d["Call OI"].sum()), float(d["Put OI"].sum())
    pcr = tot_p / max(tot_c, 1)
    chg_c, chg_p = float(d["Call OI Chg"].sum()), float(d["Put OI Chg"].sum())

    # Bias scoring
    pts, reasons = 0, []
    if pcr >= 1.2:
        pts += 2; reasons.append(f"PCR {pcr:.2f} (>1.2) – Put writing zyada, bullish 🟢")
    elif pcr >= 1.0:
        pts += 1; reasons.append(f"PCR {pcr:.2f} – halka bullish")
    elif pcr <= 0.7:
        pts -= 2; reasons.append(f"PCR {pcr:.2f} (<0.7) – Call writing zyada, bearish 🔴")
    else:
        pts -= 1; reasons.append(f"PCR {pcr:.2f} – halka bearish")

    if chg_p > chg_c:
        pts += 1; reasons.append("Put OI mein addition, Call se zyada – support ban raha hai 🟢")
    else:
        pts -= 1; reasons.append("Call OI mein addition, Put se zyada – resistance ban raha hai 🔴")

    if spot < max_pain:
        pts += 1; reasons.append(f"Spot Max Pain ({max_pain}) se niche – upar kheench sakta hai 🟢")
    elif spot > max_pain:
        pts -= 1; reasons.append(f"Spot Max Pain ({max_pain}) se upar – niche kheench sakta hai 🔴")

    if supply and supply["primary"]["fresh"]:
        pts -= 1; reasons.append(f"Resistance {supply['primary']['strike']} par naya Call writing – mazboot 🔴")
    elif supply:
        pts += 1; reasons.append(f"Resistance {supply['primary']['strike']} par Call unwinding – kamzor 🟢")

    if demand and demand["primary"]["fresh"]:
        pts += 1; reasons.append(f"Support {demand['primary']['strike']} par naya Put writing – mazboot 🟢")
    elif demand:
        pts -= 1; reasons.append(f"Support {demand['primary']['strike']} par Put unwinding – kamzor 🔴")

    if supply and demand:
        d_sup = spot - demand["primary"]["strike"]
        d_res = supply["primary"]["strike"] - spot
        if d_sup < d_res:
            pts += 1; reasons.append("Spot support ke zyada paas hai 🟢")
        elif d_res < d_sup:
            pts -= 1; reasons.append("Spot resistance ke zyada paas hai 🔴")

    if pts >= 3:
        bias = "BULLISH 🟢"
    elif pts <= -3:
        bias = "BEARISH 🔴"
    else:
        bias = "SIDEWAYS / RANGE-BOUND 🟡"

    return {
        "supply": supply, "demand": demand, "supply2": supply2, "demand2": demand2, "liq": liq, "step": step,
        "max_pain": max_pain, "pcr": pcr,
        "chg_c": chg_c, "chg_p": chg_p, "points": pts, "bias": bias, "reasons": reasons,
    }


# ---------------- Colourful HTML table ----------------
SCROLL_JS = """
<script>
(function(){
  var box = document.querySelector('.wrap') || document.querySelector('.sc');
  var s = {};
  try { s = JSON.parse(window.name || '{}') || {}; } catch(e) { s = {}; }
  if (box) { box.scrollLeft = s.l || 0; box.scrollTop = s.bt || 0; }
  window.scrollTo(0, s.t || 0);
  function save(){ try { var o = {}; try { o = JSON.parse(window.name || '{}') || {}; } catch(e) { o = {}; } o.l = box ? box.scrollLeft : 0; o.bt = box ? box.scrollTop : 0; o.t = window.pageYOffset || 0; window.name = JSON.stringify(o); } catch(e){} }
  if (box) box.addEventListener('scroll', save);
  window.addEventListener('scroll', save);
})();
</script>"""


def fmt_vol(v):
    v = v or 0
    if v >= 10000000:
        return f"{v / 10000000:.2f}Cr"
    if v >= 100000:
        return f"{v / 100000:.1f}L"
    if v >= 1000:
        return f"{v / 1000:.1f}K"
    return f"{int(v)}"


def bar_cell(text, pct, bg, color, direction, top_cls):
    grad = f"linear-gradient({direction}, {color} {pct:.0f}%, transparent {pct:.0f}%)"
    cls = "pb top-" + top_cls if pct >= 99.9 else "pb"
    return (
        f'<td style="background-color:{bg};background-image:{grad}">'
        f'<div class="v">{text}</div><div class="{cls}">{pct:.0f}%</div></td>'
    )


def chg_cell(x, bg):
    color = "#16a34a" if x > 0 else "#dc2626" if x < 0 else "#6b7280"
    return f'<td style="background-color:{bg};color:{color};font-weight:600">{x / 1e5:.1f}</td>'


def plain_cell(text, bg, bold=False):
    w = "font-weight:700;" if bold else ""
    return f'<td style="background-color:{bg};{w}">{text}</td>'


def build_table_html(df, spot, n, ana=None, show_summary=True, inst_label="SPOT"):
    mx = {k: max(float(df[k].max()), 1.0) for k in ["Call OI", "Put OI", "Call Vol", "Put Vol"]}
    d = df.sort_values("Strike").reset_index(drop=True)
    atm_idx = int((d["Strike"] - spot).abs().idxmin())
    atm_strike = d.loc[atm_idx, "Strike"]
    view = d.iloc[max(atm_idx - n, 0): atm_idx + n + 1]

    # zone tags per strike
    tags, zone_bg = {}, {}
    if ana:
        for key, name, bg, pre in (
            ("supply", "SUPPLY", "#fecaca", "R"), ("demand", "DEMAND", "#bbf7d0", "S"),
            ("supply2", "SUPPLY 2", "#fee2e2", "R2."), ("demand2", "DEMAND 2", "#dcfce7", "S2."),
        ):
            z = ana.get(key)
            if not z:
                continue
            for i, lv in enumerate(z["levels"]):
                label = pre + str(i + 1)
                cls = "r" if key.startswith("supply") else "s"
                tags.setdefault(lv["strike"], []).append(f'<span class="chip {cls}">{label} {name}</span>')
                zone_bg.setdefault(lv["strike"], bg)
        for lq in ana.get("liq") or []:
            tags.setdefault(lq["strike"], []).append('<span class="chip l">💧 LIQUIDITY</span>')
        tags.setdefault(ana["max_pain"], []).append('<span class="chip m">🎯 MAX PAIN</span>')

    ITM, NORM = "#fff7cc", "#ffffff"
    ncols = 13 if show_summary else 11
    rows, spot_done = [], False
    spot_row = (
        f'<tr class="spotrow"><td colspan="{ncols}">▶▶ {inst_label} SPOT {spot:,.2f} (market yahan chal raha hai) ◀◀</td></tr>'
    )
    for _, r in view.iterrows():
        s = r["Strike"]
        if not spot_done and s > spot:
            rows.append(spot_row)
            spot_done = True
        cbg = ITM if s < spot else NORM
        pbg = ITM if s > spot else NORM
        is_atm = s == atm_strike
        strike_bg = zone_bg.get(fs(s), "#bfdbfe" if is_atm else "#f3f4f6")

        zone_td, pcr_td = "", ""
        if show_summary:
            pcr_s = r["Put OI"] / r["Call OI"] if r["Call OI"] else 0
            pcr_col = "#16a34a" if pcr_s > 1 else "#dc2626"
            sbg = "#faf5ff"
            zone_td = f'<td style="background-color:{sbg}">{"".join(tags.get(fs(s), [])) or "-"}</td>'
            pcr_td = f'<td style="background-color:{sbg};color:{pcr_col};font-weight:700">{pcr_s:.2f}</td>'

        rows.append(
            f'<tr class="{"atm" if is_atm else ""}">'
            # CALLS: IV | LTP | OI Chg | OI | Volume  (Volume strike ke paas)
            + plain_cell(f'{r["Call IV"]:.1f}', cbg)
            + plain_cell(f'{r["Call LTP"]:.2f}', cbg, True)
            + chg_cell(r["Call OI Chg"], cbg)
            + bar_cell(f'{r["Call OI"] / 1e5:.1f}', r["Call OI"] / mx["Call OI"] * 100, cbg, "rgba(248,113,113,.55)", "to left", "red")
            + bar_cell(fmt_vol(r["Call Vol"]), r["Call Vol"] / mx["Call Vol"] * 100, cbg, "rgba(251,146,60,.35)", "to left", "red")
            + f'<td style="background-color:{strike_bg};font-weight:800;font-size:14px">{fs(s)}</td>'
            # PUTS: Volume | OI | OI Chg | LTP | Zone | PCR | IV
            + bar_cell(fmt_vol(r["Put Vol"]), r["Put Vol"] / mx["Put Vol"] * 100, pbg, "rgba(96,165,250,.35)", "to right", "green")
            + bar_cell(f'{r["Put OI"] / 1e5:.1f}', r["Put OI"] / mx["Put OI"] * 100, pbg, "rgba(74,222,128,.6)", "to right", "green")
            + chg_cell(r["Put OI Chg"], pbg)
            + plain_cell(f'{r["Put LTP"]:.2f}', pbg, True)
            + zone_td
            + pcr_td
            + plain_cell(f'{r["Put IV"]:.1f}', pbg)
            + "</tr>"
        )
    if not spot_done:
        rows.append(spot_row)

    css = """
    <meta charset="utf-8"><style>
      body{margin:0;font-family:Arial,Helvetica,sans-serif;}
      .wrap{overflow-x:auto;}
      .bar{display:flex;justify-content:flex-end;padding:4px 2px 6px 2px;}
      #fsbtn{background:#2563eb;color:#fff;border:none;border-radius:8px;padding:7px 14px;font-size:13px;font-weight:700;cursor:pointer;}
      #fsbtn:active{background:#1d4ed8;}
      #oc{background:#fff;}
      #oc:fullscreen{overflow:hidden;padding:6px;}
      #oc:fullscreen .wrap{height:calc(100vh - 52px);overflow:auto;}
      #oc:-webkit-full-screen{overflow:hidden;padding:6px;}
      #oc:-webkit-full-screen .wrap{height:calc(100vh - 52px);overflow:auto;}
      table{width:100%;border-collapse:collapse;font-size:13px;text-align:center;}
      th{position:sticky;top:0;z-index:2;padding:8px 4px;color:#374151;background:#f9fafb;border-bottom:2px solid #e5e7eb;white-space:nowrap;}
      th.calls{background:#fee2e2;color:#b91c1c;letter-spacing:1px;}
      th.puts{background:#dcfce7;color:#15803d;letter-spacing:1px;}
      th.sum{background:#f3e8ff;color:#7e22ce;letter-spacing:1px;}
      td{padding:5px 6px;border-bottom:1px solid #eef0f3;white-space:nowrap;color:#111;}
      .v{font-size:13px;font-weight:600;}
      .pb{display:inline-block;font-size:9px;color:#15803d;border:1px solid #86efac;border-radius:4px;padding:0 3px;margin-top:2px;background:#f0fdf4;}
      .pb.top-red{color:#b91c1c;border-color:#f87171;background:#fef2f2;font-weight:700;}
      .pb.top-green{color:#166534;border-color:#22c55e;background:#dcfce7;font-weight:700;}
      tr.atm td{border-top:2px solid #3b82f6;border-bottom:2px solid #3b82f6;}
      tr.spotrow td{background:linear-gradient(90deg,#f97316,#ef4444,#f97316);color:#fff;font-weight:800;
                    padding:4px;font-size:12px;letter-spacing:1px;border:none;}
      .chip{display:inline-block;font-size:10px;font-weight:700;border-radius:10px;padding:1px 7px;margin:1px;}
      .chip.r{background:#ef4444;color:#fff;} .chip.s{background:#22c55e;color:#fff;} .chip.m{background:#8b5cf6;color:#fff;} .chip.l{background:#0891b2;color:#fff;}
    </style>"""
    put_cols = 7 if show_summary else 5
    head1 = (
        '<tr><th colspan="5" class="calls">CALLS</th><th></th>'
        f'<th colspan="{put_cols}" class="puts">PUTS</th></tr>'
    )
    head2 = "<tr><th>IV</th><th>LTP</th><th>OI Chg (L)</th><th>OI (L)</th><th>Volume</th><th>Strike</th>"
    head2 += "<th>Volume</th><th>OI (L)</th><th>OI Chg (L)</th><th>LTP</th>"
    if show_summary:
        head2 += "<th>Zone</th><th>PCR</th>"
    head2 += "<th>IV</th></tr>"
    minw = 1150 if show_summary else 950
    js = """
    <script>
      var oc = document.getElementById('oc'), btn = document.getElementById('fsbtn');
      function isFS(){ return document.fullscreenElement || document.webkitFullscreenElement; }
      function toggleFS(){
        try {
          if (!isFS()) {
            var f = oc.requestFullscreen || oc.webkitRequestFullscreen;
            var p = f.call(oc);
            if (p && p.catch) p.catch(function(){ alert('Is browser mein Full Screen support nahi. Sidebar ka Focus mode use karein.'); });
          } else {
            (document.exitFullscreen || document.webkitExitFullscreen).call(document);
          }
        } catch(e) { alert('Is browser mein Full Screen support nahi. Sidebar ka Focus mode use karein.'); }
      }
      function upd(){ btn.textContent = isFS() ? '✖ Exit Full Screen' : '⛶ Full Screen'; }
      document.addEventListener('fullscreenchange', upd);
      document.addEventListener('webkitfullscreenchange', upd);
    </script>"""
    table = f'<table style="min-width:{minw}px">{head1}{head2}{"".join(rows)}</table>'
    return (
        f'{css}<div id="oc"><div class="bar"><button id="fsbtn" onclick="toggleFS()">⛶ Full Screen</button></div>'
        f'<div class="wrap">{table}</div></div>{js}{SCROLL_JS}'
    )


# ---------------- Charts with Supply / Demand zones (pure SVG, no extra library) ----------------
def _strength_txt(score):
    return "Strong" if score >= 70 else "Medium" if score >= 45 else "Weak"


def _pfmt(v):
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def zone_items(ana):
    step = float((ana or {}).get("step", 50))
    items = []
    for key, name, short, col, alpha in (
        ("supply", "SUPPLY 1", "SUP1", "#ef4444", 0.17), ("supply2", "SUPPLY 2", "SUP2", "#f87171", 0.09),
        ("demand", "DEMAND 1", "DEM1", "#16a34a", 0.17), ("demand2", "DEMAND 2", "DEM2", "#4ade80", 0.10),
    ):
        z = (ana or {}).get(key)
        if not z:
            continue
        lo, hi = float(z["low"]), float(z["high"])
        rng = f"{z['low']}-{z['high']}" if z["low"] != z["high"] else f"{z['low']}"
        label = f"{name} {rng} ({_strength_txt(z['score'])})"
        single = (hi - lo) < 0.4 * step
        if single:                            # single strike -> thodi moti patti
            lo, hi = lo - 0.2 * step, hi + 0.2 * step
        items.append({"label": label, "short": short, "lo": lo, "hi": hi, "color": col, "alpha": alpha,
                      "level": z["primary"]["strike"], "key": key, "single": single})
    return items


def level_lines(ana, spot):
    out = [(f"SPOT {_pfmt(spot)}", float(spot), "#f97316")]
    for it in zone_items(ana):
        if it["key"] in ("supply", "demand"):
            out.append((("R1 " if it["key"] == "supply" else "S1 ") + str(it["level"]), float(it["level"]), it["color"]))
    if ana:
        out.append((f"MP {ana['max_pain']}", float(ana["max_pain"]), "#8b5cf6"))
        taken = {round(v) for _, v, _ in out}
        n_liq = 0
        for lq in ana.get("liq") or []:
            if lq["strike"] not in taken and n_liq < 2:
                out.append((f"LIQ {lq['strike']}", float(lq["strike"]), "#0891b2"))
                n_liq += 1
    return out


def _nice_step(rng, target=6):
    raw = max(rng, 1e-9) / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _spread(ys, gap=15):
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    res, prev = list(ys), -1e9
    for i in order:
        res[i] = max(res[i], prev + gap)
        prev = res[i]
    return res


def _legend():
    chips = (
        ("SUPPLY ZONE (Resistance)", "#ef4444"), ("DEMAND ZONE (Support)", "#16a34a"),
        ("SPOT", "#f97316"), ("MP = MAX PAIN", "#8b5cf6"), ("LIQ = LIQUIDITY", "#0891b2"),
    )
    return "".join(
        f'<span style="display:inline-block;margin:0 10px 4px 0;font-size:12px;font-weight:700;color:#111">'
        f'<span style="display:inline-block;width:11px;height:11px;border-radius:3px;background:{c};margin-right:5px;vertical-align:-1px"></span>{t}</span>'
        for t, c in chips
    )


FS_JS = """
<script>
  var oc = document.getElementById('oc'), btn = document.getElementById('fsbtn');
  function isFS(){ return document.fullscreenElement || document.webkitFullscreenElement; }
  function toggleFS(){
    try {
      if (!isFS()) {
        var f = oc.requestFullscreen || oc.webkitRequestFullscreen;
        var p = f.call(oc);
        if (p && p.catch) p.catch(function(){ alert('Is browser mein Full Screen support nahi. Sidebar ka Charts Focus mode use karein.'); });
      } else {
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      }
    } catch(e) { alert('Is browser mein Full Screen support nahi. Sidebar ka Charts Focus mode use karein.'); }
  }
  function upd(){ btn.textContent = isFS() ? '✖ Exit Full Screen' : '⛶ Full Screen'; }
  document.addEventListener('fullscreenchange', upd);
  document.addEventListener('webkitfullscreenchange', upd);
</script>"""


ZOOM_JS = r"""
<script>
(function(){
var D = __DATA__;
var svg = document.getElementById('svg'), stage = document.getElementById('stage'), info = document.getElementById('info');
var n = D.n, L = D.L, T = D.T, W = D.W, H = D.H, pw = D.pw, ph = D.ph;
var DEF = {a: D.defA, b: D.defB, lo: D.lo, hi: D.hi};
var view = {a: DEF.a, b: DEF.b, lo: DEF.lo, hi: DEF.hi}, sel = -1, HINT = info.textContent;
try {
  var o0 = JSON.parse(window.name || '{}') || {}, z0 = o0.z;
  if (z0 && z0.sig === D.sig) { view.a = n - z0.ra; view.b = n - z0.rb; view.lo = z0.lo; view.hi = z0.hi; }
} catch(e) {}
if (!(view.b - view.a > 0.5)) { view.a = DEF.a; view.b = DEF.b; view.lo = DEF.lo; view.hi = DEF.hi; }

function persist(clear){
  try {
    var o = {}; try { o = JSON.parse(window.name || '{}') || {}; } catch(e) { o = {}; }
    if (clear) { delete o.z; } else { o.z = {sig: D.sig, ra: n - view.a, rb: n - view.b, lo: view.lo, hi: view.hi}; }
    window.name = JSON.stringify(o);
  } catch(e) {}
}
function clampView(){
  var minSpan = Math.min(5, n), maxSpan = Math.max(n * 3, 10), span = view.b - view.a, c;
  if (span < minSpan) { c = (view.a + view.b) / 2; view.a = c - minSpan / 2; view.b = c + minSpan / 2; }
  if (span > maxSpan) { c = (view.a + view.b) / 2; view.a = c - maxSpan / 2; view.b = c + maxSpan / 2; }
  c = (view.a + view.b) / 2;
  if (c < 0) { view.a -= c; view.b -= c; } else if (c > n) { view.a -= c - n; view.b -= c - n; }
  var minY = (DEF.hi - DEF.lo) * 0.02;
  if (view.hi - view.lo < minY) { var m = (view.hi + view.lo) / 2; view.lo = m - minY / 2; view.hi = m + minY / 2; }
  if (D.anchorBottom) view.lo = 0;
}
function zoomX(f, cx){ view.a = cx - (cx - view.a) * f; view.b = cx + (view.b - cx) * f; }
function zoomY(f, cy){ if (D.anchorBottom) cy = 0; view.lo = cy - (cy - view.lo) * f; view.hi = cy + (view.hi - cy) * f; }
function rect(){ return stage.getBoundingClientRect(); }
function idxAt(vx){ return view.a + (vx - L) / pw * (view.b - view.a); }
function valAt(vy){ return view.hi - (vy - T) / ph * (view.hi - view.lo); }
function niceStep(rng, target){
  var raw = Math.max(rng, 1e-9) / target, mag = Math.pow(10, Math.floor(Math.log10(raw))), ms = [1, 2, 2.5, 5, 10];
  for (var i = 0; i < ms.length; i++) { if (raw <= ms[i] * mag) return ms[i] * mag; }
  return 10 * mag;
}
function pfmt(v){ return Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('en-US') : v.toFixed(2); }
function spread(ys, gap){
  var idx = ys.map(function(_, i){ return i; }).sort(function(p, q){ return ys[p] - ys[q]; }), res = ys.slice(), prev = -1e9;
  idx.forEach(function(i){ res[i] = Math.max(res[i], prev + gap); prev = res[i]; });
  return res;
}
function f1(x){ return x.toFixed(1); }

function drawPrice(){
  var a = view.a, b = view.b, lo = view.lo, hi = view.hi, span = b - a, slot = pw / span, rng = hi - lo;
  function Y(v){ return T + (hi - v) / rng * ph; }
  function X(i){ return L + (i - a + 0.5) * slot; }
  var out = ['<defs><clipPath id="cp"><rect x="' + L + '" y="' + T + '" width="' + pw + '" height="' + ph + '"/></clipPath></defs>'];
  var cl = [], ticks = [], step = niceStep(rng, 6), v = Math.ceil(lo / step) * step, g = 0;
  while (v <= hi && g++ < 80) {
    var y = Y(v);
    cl.push('<line x1="' + L + '" x2="' + (L + pw) + '" y1="' + f1(y) + '" y2="' + f1(y) + '" stroke="#eef0f3"/>');
    ticks.push('<text x="' + (L + pw + 4) + '" y="' + f1(y + 3) + '" font-size="10" fill="#9ca3af">' + pfmt(v) + '</text>');
    v += step;
  }
  var zl = [];
  D.zones.forEach(function(z){
    var y1 = Y(z.hi), y2 = Y(z.lo);
    cl.push('<rect x="' + L + '" y="' + f1(y1) + '" width="' + pw + '" height="' + f1(Math.max(y2 - y1, 2)) + '" fill="' + z.color + '" opacity="' + z.alpha + '"/>');
    [y1, y2].forEach(function(yy){ cl.push('<line x1="' + L + '" x2="' + (L + pw) + '" y1="' + f1(yy) + '" y2="' + f1(yy) + '" stroke="' + z.color + '" stroke-width="1" opacity="0.55"/>'); });
    zl.push([z, y1, y2]);
  });
  var i0 = Math.max(0, Math.floor(a) - 1), i1 = Math.min(n - 1, Math.ceil(b) + 1), cw = Math.min(Math.max(1.2, slot * 0.7), 40);
  for (var i = i0; i <= i1; i++) {
    var c = D.c[i], col = c[3] >= c[0] ? '#16a34a' : '#dc2626', x = X(i), yo = Y(c[0]), yc = Y(c[3]);
    cl.push('<g><title>' + D.tips[i] + '</title><line x1="' + f1(x) + '" x2="' + f1(x) + '" y1="' + f1(Y(c[1])) + '" y2="' + f1(Y(c[2])) + '" stroke="' + col + '"/>' +
            '<rect x="' + f1(x - cw / 2) + '" y="' + f1(Math.min(yo, yc)) + '" width="' + f1(cw) + '" height="' + f1(Math.max(Math.abs(yo - yc), 1)) + '" fill="' + col + '"/></g>');
  }
  if (sel >= 0 && sel < n) { var xs = X(sel); cl.push('<line x1="' + f1(xs) + '" x2="' + f1(xs) + '" y1="' + T + '" y2="' + (T + ph) + '" stroke="#2563eb" stroke-width="1" stroke-dasharray="3 3"/>'); }
  zl.forEach(function(q){
    var z = q[0], y1 = q[1], y2 = q[2];
    if (y2 < T || y1 > T + ph) return;
    var ty = (y2 - y1) >= 20 ? y1 + 13 : y1 - 4;
    ty = Math.min(Math.max(ty, T + 12), T + ph - 4);
    cl.push('<text x="' + (L + 6) + '" y="' + f1(ty) + '" font-size="11" font-weight="700" fill="' + z.color + '" stroke="#fff" stroke-width="3" paint-order="stroke">' + z.label + '</text>');
  });
  D.lines.forEach(function(l){
    var y = Y(l.val);
    cl.push('<line x1="' + L + '" x2="' + (L + pw) + '" y1="' + f1(y) + '" y2="' + f1(y) + '" stroke="' + l.color + '" stroke-width="1.4" stroke-dasharray="' + l.dash + '"/>');
  });
  out.push('<g clip-path="url(#cp)">' + cl.join('') + '</g>', ticks.join(''));
  var tg = D.lines.map(function(l){
    var y = Y(l.val), arrow = '';
    if (y < T) { y = T + 9; arrow = '\u25B2 '; } else if (y > T + ph) { y = T + ph - 9; arrow = '\u25BC '; }
    return {l: l, y: y, t: arrow + l.lab};
  });
  var ys = spread(tg.map(function(o){ return o.y; }), 15);
  tg.forEach(function(o, k){
    out.push('<rect x="' + (L + pw + 2) + '" y="' + f1(ys[k] - 8) + '" width="' + (D.R - 4) + '" height="15" rx="4" fill="' + o.l.color + '"/>' +
             '<text x="' + (L + pw + 2 + (D.R - 4) / 2) + '" y="' + f1(ys[k] + 3) + '" font-size="' + (o.t.length > 11 ? 8.3 : 9.5) + '" font-weight="700" fill="#fff" text-anchor="middle">' + o.t + '</text>');
  });
  for (var k = 0; k < 5; k++) {
    var idx = Math.round(a + span * k / 4 - 0.5);
    if (idx < 0 || idx > n - 1) continue;
    var xx = Math.min(Math.max(X(idx), L + 22), L + pw - 22);
    out.push('<text x="' + f1(xx) + '" y="' + (H - 8) + '" font-size="10" fill="#6b7280" text-anchor="middle">' + D.labels[idx] + '</text>');
  }
  svg.innerHTML = out.join('');
}

function drawOI(){
  var a = view.a, b = view.b, hi = view.hi, span = b - a, slot = pw / span;
  function Y(v){ return T + (1 - v / hi) * ph; }
  function X(i){ return L + (i - a + 0.5) * slot; }
  function XS(s){ return X((s - D.s0) / D.sstep); }
  var out = ['<defs><clipPath id="cp"><rect x="' + L + '" y="' + T + '" width="' + pw + '" height="' + ph + '"/></clipPath></defs>'];
  var cl = [], step = niceStep(hi / 1e5, 5) * 1e5, t = 0, g = 0;
  while (t <= hi && g++ < 80) {
    var y = Y(t);
    cl.push('<line x1="' + L + '" x2="' + (L + pw) + '" y1="' + f1(y) + '" y2="' + f1(y) + '" stroke="#eef0f3"/>');
    out.push('<text x="' + (L - 4) + '" y="' + f1(y + 3) + '" font-size="10" fill="#9ca3af" text-anchor="end">' + Math.round(t / 1e5) + 'L</text>');
    t += step;
  }
  var zt = [];
  D.zones.forEach(function(z){
    var x1 = Math.max(L, XS(z.a) - slot / 2), x2 = Math.min(L + pw, XS(z.b) + slot / 2);
    if (x2 <= x1) return;
    cl.push('<rect x="' + f1(x1) + '" y="' + T + '" width="' + f1(x2 - x1) + '" height="' + ph + '" fill="' + z.color + '" opacity="' + z.alpha + '"/>' +
            '<rect x="' + f1(x1) + '" y="' + T + '" width="' + f1(x2 - x1) + '" height="' + ph + '" fill="none" stroke="' + z.color + '" stroke-width="1" stroke-dasharray="4 3" opacity="0.6"/>');
    var dem = z.key.indexOf('demand') === 0;
    zt.push('<text x="' + f1(dem ? x2 : x1) + '" y="' + (T - 8) + '" font-size="10" font-weight="700" fill="' + z.color + '" text-anchor="' + (dem ? 'end' : 'start') + '">' + z.short + '</text>');
  });
  var i0 = Math.max(0, Math.floor(a) - 1), i1 = Math.min(n - 1, Math.ceil(b) + 1), bw = Math.min(Math.max(2, slot * 0.38), 30);
  for (var i = i0; i <= i1; i++) {
    var x = X(i);
    cl.push('<g><title>' + D.tips[i] + '</title>' +
            '<rect x="' + f1(x - bw - 0.5) + '" y="' + f1(Y(D.call[i])) + '" width="' + f1(bw) + '" height="' + f1(Math.max(Y(0) - Y(D.call[i]), 0)) + '" fill="#ef4444"/>' +
            '<rect x="' + f1(x + 0.5) + '" y="' + f1(Y(D.put[i])) + '" width="' + f1(bw) + '" height="' + f1(Math.max(Y(0) - Y(D.put[i]), 0)) + '" fill="#22c55e"/></g>');
  }
  if (sel >= 0 && sel < n) { var xs = X(sel); cl.push('<line x1="' + f1(xs) + '" x2="' + f1(xs) + '" y1="' + T + '" y2="' + (T + ph) + '" stroke="#2563eb" stroke-width="1" stroke-dasharray="3 3"/>'); }
  var tags = [];
  D.lines.forEach(function(l){
    var x = XS(l.val);
    if (x < L || x > L + pw) return;
    cl.push('<line x1="' + f1(x) + '" x2="' + f1(x) + '" y1="' + (T - 36) + '" y2="' + (T + ph) + '" stroke="' + l.color + '" stroke-width="1.6" stroke-dasharray="' + l.dash + '"/>');
    tags.push([x, l]);
  });
  out.push('<g clip-path="url(#cp)">' + cl.join('') + '</g>', zt.join(''));
  var vis = Math.max(1, i1 - i0 + 1), skip = Math.max(1, Math.ceil(vis / 11));
  for (var j = Math.max(0, Math.floor(a)); j <= Math.min(n - 1, Math.ceil(b)); j++) {
    var isAtm = j === D.atm;
    if (isAtm || (j % skip === 0 && Math.abs(j - D.atm) > Math.max(1, Math.floor(skip / 2)))) {
      var lx = X(j);
      if (lx < L + 6 || lx > L + pw - 6) continue;
      out.push('<text x="' + f1(lx) + '" y="' + (H - 16) + '" font-size="9.5" text-anchor="middle"' + (isAtm ? ' font-weight="800" fill="#2563eb"' : ' fill="#6b7280"') + '>' + D.slabels[j] + '</text>');
    }
  }
  tags.sort(function(p, q){ return p[0] - q[0]; });
  tags.forEach(function(q, k){
    var x = q[0], l = q[1], ty = T - 40 + (k % 2) * 16, w = 6.2 * l.lab.length + 8, bx = Math.min(Math.max(x - w / 2, L), L + pw - w);
    out.push('<rect x="' + f1(bx) + '" y="' + f1(ty - 11) + '" width="' + f1(w) + '" height="15" rx="4" fill="' + l.color + '"/>' +
             '<text x="' + f1(bx + w / 2) + '" y="' + f1(ty) + '" font-size="9.5" font-weight="700" fill="#fff" text-anchor="middle">' + l.lab + '</text>');
  });
  out.push('<text x="' + (L + pw / 2) + '" y="' + (H - 2) + '" font-size="10" fill="#9ca3af" text-anchor="middle">Strike  (laal = Call OI, hara = Put OI)</text>');
  svg.innerHTML = out.join('');
}

function draw(){ if (D.kind === 'price') drawPrice(); else drawOI(); window.__view = view; }
function changed(){ clampView(); persist(false); draw(); }

var P = {};
function handle(e, p, zone){
  var r = rect(), k = W / r.width;
  var ids = Object.keys(P).filter(function(id){ return P[id].z === 'plot'; });
  if (zone === 'plot' && ids.length >= 2) {
    var q1 = P[ids[0]], q2 = P[ids[1]], ox1 = q1.x, oy1 = q1.y, ox2 = q2.x, oy2 = q2.y;
    p.x = e.clientX; p.y = e.clientY; p.moved = true;
    var odx = Math.abs(ox1 - ox2), ody = Math.abs(oy1 - oy2), ndx = Math.abs(q1.x - q2.x), ndy = Math.abs(q1.y - q2.y);
    var mx = ((q1.x + q2.x) / 2 - r.left) * k, my = ((q1.y + q2.y) / 2 - r.top) * k;
    if (odx >= ody) { if (ndx > 4 && odx > 4) zoomX(odx / ndx, idxAt(mx)); }
    else if (ndy > 4 && ody > 4) { zoomY(ody / ndy, valAt(my)); }
    changed(); return;
  }
  var dxv = (e.clientX - p.x) * k, dyv = (e.clientY - p.y) * k;
  p.x = e.clientX; p.y = e.clientY;
  if (Math.abs(e.clientX - p.sx) + Math.abs(e.clientY - p.sy) > 5) p.moved = true;
  if (zone === 'plot') {
    var span = view.b - view.a; view.a -= dxv / pw * span; view.b -= dxv / pw * span;
    if (D.panY) { var yr = view.hi - view.lo; view.lo += dyv / ph * yr; view.hi += dyv / ph * yr; }
  } else if (zone === 'yaxis') { zoomY(Math.exp(dyv * 0.008), (view.hi + view.lo) / 2); }
  else if (zone === 'xaxis') { zoomX(Math.exp(-dxv * 0.008), (view.a + view.b) / 2); }
  changed();
}
function showSel(e){
  var r = rect(), k = W / r.width, i = Math.floor(idxAt((e.clientX - r.left) * k));
  if (i < 0 || i >= n) { sel = -1; info.textContent = HINT; draw(); return; }
  sel = i; info.textContent = D.tips[i]; draw();
}
function bind(id, zone){
  var el = document.getElementById(id);
  el.addEventListener('pointerdown', function(e){
    try { el.setPointerCapture(e.pointerId); } catch(_) {}
    P[e.pointerId] = {x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, z: zone, moved: false};
  });
  el.addEventListener('pointermove', function(e){ var p = P[e.pointerId]; if (p) handle(e, p, zone); });
  el.addEventListener('pointerup', function(e){ var p = P[e.pointerId]; delete P[e.pointerId]; if (p && zone === 'plot' && !p.moved) showSel(e); });
  el.addEventListener('pointercancel', function(e){ delete P[e.pointerId]; });
}
bind('zp', 'plot'); bind('za', 'yaxis'); bind('zt', 'xaxis');

function wheelF(e){ return e.deltaY > 0 ? 1.12 : 1 / 1.12; }
document.getElementById('za').addEventListener('wheel', function(e){ e.preventDefault(); zoomY(wheelF(e), (view.hi + view.lo) / 2); changed(); }, {passive: false});
document.getElementById('zt').addEventListener('wheel', function(e){ e.preventDefault(); zoomX(wheelF(e), (view.a + view.b) / 2); changed(); }, {passive: false});
document.getElementById('zp').addEventListener('wheel', function(e){
  var r = rect(), k = W / r.width;
  if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomX(wheelF(e), idxAt((e.clientX - r.left) * k)); changed(); }
  else if (e.shiftKey) { e.preventDefault(); zoomY(wheelF(e), valAt((e.clientY - r.top) * k)); changed(); }
}, {passive: false});

function reset(){ view.a = DEF.a; view.b = DEF.b; view.lo = DEF.lo; view.hi = DEF.hi; sel = -1; info.textContent = HINT; persist(true); draw(); }
document.getElementById('zrs').addEventListener('click', reset);
var zall = document.getElementById('zall');
if (zall) zall.addEventListener('click', function(){ view.lo = D.flo; view.hi = D.fhi; changed(); });
var zm = document.getElementById('zm'), zp = document.getElementById('zp'), zoomOn = true;
try { var o1 = JSON.parse(window.name || '{}') || {}; if (o1.zm === 0) zoomOn = false; } catch(e) {}
function applyMode(){
  zp.style.touchAction = zoomOn ? 'none' : 'pan-y';
  zm.textContent = zoomOn ? '\u270B Zoom' : '\uD83D\uDCDC Scroll';
  zm.style.background = zoomOn ? '#16a34a' : '#475569';
}
zm.addEventListener('click', function(){
  zoomOn = !zoomOn; applyMode();
  try { var o = JSON.parse(window.name || '{}') || {}; o.zm = zoomOn ? 1 : 0; window.name = JSON.stringify(o); } catch(e) {}
});
applyMode();
stage.addEventListener('dblclick', reset);
document.getElementById('zin').addEventListener('click', function(){ zoomX(0.7, (view.a + view.b) / 2); zoomY(0.7, (view.hi + view.lo) / 2); changed(); });
document.getElementById('zout').addEventListener('click', function(){ zoomX(1 / 0.7, (view.a + view.b) / 2); zoomY(1 / 0.7, (view.hi + view.lo) / 2); changed(); });

clampView(); draw();
})();
</script>"""


def _zoom_page(data):
    W, H, L, T, pw, ph = data["W"], data["H"], data["L"], data["T"], data["pw"], data["ph"]
    ratio = W / H

    def pc(x, tot):
        return f"{x / tot * 100:.3f}%"

    ax0, ax1 = data["yax"]
    css = (
        "<meta charset=\"utf-8\"><style>body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#fff}"
        ".sc{overflow-x:auto;padding:0 2px}"
        ".top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:6px;padding:6px 8px 0 8px}"
        ".btns{display:flex;flex-wrap:wrap;gap:5px}"
        ".btns button{background:#2563eb;color:#fff;border:none;border-radius:8px;padding:6px 10px;font-size:13px;font-weight:700;cursor:pointer}"
        ".btns button.g{background:#475569;padding:6px 10px}"
        ".btns button:active{opacity:.8}"
        "#info{font-size:11px;color:#334155;min-height:16px;padding:4px 10px 4px 10px;line-height:1.35}"
        "#stage{position:relative;line-height:0;user-select:none;-webkit-user-select:none}"
        ".ov{position:absolute}"
        "#zp{touch-action:pan-y;cursor:crosshair}#za,#zt{touch-action:none}#za{cursor:ns-resize}#zt{cursor:ew-resize}"
        "#oc{background:#fff}"
        "#oc:fullscreen{overflow:auto;padding:6px}#oc:-webkit-full-screen{overflow:auto;padding:6px}"
        "#oc:fullscreen .inner,#oc:-webkit-full-screen .inner{max-width:calc((100vh - 140px) * " + f"{ratio:.4f}" + ") !important;min-width:0!important}"
        "</style>"
    )
    hint = "2 ungli pinch = zoom | khinchein = chart khiskao | Axis par khinchein = price/time zoom | tap = data | double tap = reset"
    html = (
        f'{css}<div id="oc"><div class="top"><div>{_legend()}</div>'
        '<div class="btns"><button id="zin" class="g" title="Zoom in">＋</button><button id="zout" class="g" title="Zoom out">－</button>'
        '<button id="zrs" class="g" title="Reset">⟲</button>'
        + ('<button id="zall" class="g" title="Saare zone/levels dikhao">⤢ Sab</button>' if data["kind"] == "price" else "")
        + '<button id="zm" class="g">✋ Zoom</button><button id="fsbtn" onclick="toggleFS()">⛶ Full Screen</button></div></div>'
        f'<div id="info">{hint}</div>'
        '<div class="sc"><div class="inner" style="min-width:360px;max-width:620px;margin:auto"><div id="stage">'
        f'<svg id="svg" viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="background:#fff;display:block"></svg>'
        f'<div id="zp" class="ov" style="left:{pc(L, W)};top:{pc(T, H)};width:{pc(pw, W)};height:{pc(ph, H)}"></div>'
        f'<div id="za" class="ov" style="left:{pc(ax0, W)};top:{pc(T, H)};width:{pc(ax1 - ax0, W)};height:{pc(ph, H)}"></div>'
        f'<div id="zt" class="ov" style="left:{pc(L, W)};top:{pc(T + ph, H)};width:{pc(pw, W)};height:{pc(H - T - ph, H)}"></div>'
        '</div></div></div></div>'
    )
    return html + ZOOM_JS.replace("__DATA__", json.dumps(data)) + FS_JS + SCROLL_JS


def price_chart_html(c, spot, ana, xfmt="%H:%M", sig="", fit_all=False):
    W, H = 440, 330
    L, R, T, B = 6, 74, 10, 26
    pw, ph = W - L - R, H - T - B
    items, lines = zone_items(ana), level_lines(ana, spot)
    vals = [c["low"].min(), c["high"].max()] + [v for _, v, _ in lines] + [i["lo"] for i in items] + [i["hi"] for i in items]
    flo, fhi = float(min(vals)), float(max(vals))
    fpad = (fhi - flo) * 0.04 or 5
    flo, fhi = flo - fpad, fhi + fpad                      # saare zone + levels
    vis = c.tail(90)                                       # default: aakhri 90 candles hi dikhao
    cl, ch = float(vis["low"].min()), float(vis["high"].max())
    crng = max(ch - cl, abs(float(spot)) * 0.002)
    lo, hi = cl - crng * 0.35, ch + crng * 0.35            # candles saaf dikhein
    if fit_all:
        lo, hi = flo, fhi
    rows = list(c.reset_index(drop=True).itertuples())
    data = {
        "kind": "price", "sig": sig, "W": W, "H": H, "L": L, "R": R, "T": T, "pw": pw, "ph": ph, "n": len(rows),
        "c": [[float(r.open), float(r.high), float(r.low), float(r.close)] for r in rows],
        "labels": [f"{r.time:{xfmt}}" for r in rows],
        "tips": [f"{r.time:%d %b %H:%M}  O {r.open:.2f}  H {r.high:.2f}  L {r.low:.2f}  C {r.close:.2f}" for r in rows],
        "zones": [{"label": i["label"], "lo": i["lo"], "hi": i["hi"], "color": i["color"], "alpha": i["alpha"]} for i in items],
        "lines": [{"lab": lab, "val": float(val), "color": col, "dash": "2 3" if lab.startswith("LIQ") else "5 4"} for lab, val, col in lines],
        "lo": lo, "hi": hi, "flo": flo, "fhi": fhi, "defA": max(0, len(rows) - 90), "defB": len(rows) + 1,
        "yax": [L + pw, W], "panY": True, "anchorBottom": False,
    }
    return _zoom_page(data)


def oi_chart_html(df, spot, n, ana, sig=""):
    d = df.sort_values("Strike").reset_index(drop=True)
    ai = int((d["Strike"] - spot).abs().idxmin())
    v = d.iloc[max(ai - n, 0): ai + n + 1].reset_index(drop=True)
    W, H = 440, 340
    L, R, T, B = 32, 6, 56, 34
    pw, ph = W - L - R, H - T - B
    strikes = v["Strike"].astype(float).tolist()
    sstep = min([b - a for a, b in zip(strikes, strikes[1:])] or [50])
    atm = float(d.loc[ai, "Strike"])
    ymax = max(float(v["Call OI"].max()), float(v["Put OI"].max()), 1.0) * 1.1
    zones = [
        {"key": it["key"], "short": it["short"], "a": it["level"] if it["single"] else it["lo"],
         "b": it["level"] if it["single"] else it["hi"], "color": it["color"], "alpha": it["alpha"]}
        for it in zone_items(ana)
    ]
    r1 = float(ana["supply"]["primary"]["strike"]) if ana and ana["supply"] else -1
    s1 = float(ana["demand"]["primary"]["strike"]) if ana and ana["demand"] else -1
    lines = [
        {"lab": lab, "val": float(val), "color": col, "dash": "2 3" if lab.startswith("LIQ") else "5 4"}
        for lab, val, col in level_lines(ana, spot) if val != r1 and val != s1
    ]
    data = {
        "kind": "oi", "sig": "oi|" + sig, "W": W, "H": H, "L": L, "R": R, "T": T, "pw": pw, "ph": ph, "n": len(strikes),
        "s0": strikes[0], "sstep": sstep, "atm": strikes.index(atm) if atm in strikes else -1,
        "slabels": [str(fs(s)) for s in strikes],
        "call": [float(x) for x in v["Call OI"]], "put": [float(x) for x in v["Put OI"]],
        "tips": [f'{fs(r["Strike"])}  Call OI {r["Call OI"] / 1e5:.1f}L  Put OI {r["Put OI"] / 1e5:.1f}L' for _, r in v.iterrows()],
        "zones": zones, "lines": lines, "lo": 0.0, "hi": ymax, "defA": 0, "defB": len(strikes),
        "yax": [0, L], "panY": False, "anchorBottom": True,
    }
    return _zoom_page(data)


def resample_candles(c, minutes):
    if minutes <= 1 or c.empty:
        return c
    o = (c.set_index("time")
          .resample(f"{minutes}min")
          .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
          .dropna().reset_index())
    return o


# ---------------- Optional: Claude AI commentary ----------------
def get_anthropic_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return os.getenv("ANTHROPIC_API_KEY")


def ask_claude(summary_text):
    key = get_anthropic_key()
    if not key:
        return None, "ANTHROPIC_API_KEY nahi mila. Streamlit Secrets ya .env mein add karein."
    prompt = (
        "Tum ek experienced Indian options market analyst ho. Neeche option chain ka analysis data hai. "
        "Hinglish mein short, clear analysis do: market bias, supply zone (resistance), demand zone (support), "
        "breakout/breakdown ke levels, aur risk. Last mein 1 line disclaimer do (financial advice nahi hai).\n\n"
        + summary_text
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-5-5", "max_tokens": 1000, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", [])), None
    except Exception as e:
        return None, str(e)



def get_secret(name):
    try:
        v = st.secrets[name]
    except Exception:
        v = os.getenv(name)
    if isinstance(v, str):                       # galti se aaye space / quotes hata do
        v = v.strip().strip('"').strip("'").strip()
    return v


def ask_gemini(summary_text):
    """FREE option: Google Gemini. Do tarah ki key chalti hai:
       - 'AIza...'  : Google AI Studio key (generativelanguage endpoint)
       - 'AQ.Ab8...': Vertex AI express-mode key (aiplatform endpoint)"""
    key = get_secret("GEMINI_API_KEY")
    if not key:
        return None, "GEMINI_API_KEY nahi mila."
    vertex = key.startswith("AQ.")
    prompt = (
        "Tum ek experienced Indian options market analyst ho. Neeche option chain ka analysis data hai. "
        "Hinglish mein short, clear analysis do: market bias, supply zone (resistance), demand zone (support), "
        "breakout/breakdown ke levels, aur risk. Last mein 1 line disclaimer do (financial advice nahi hai).\n\n"
        + summary_text
    )
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    models = [get_secret("GEMINI_MODEL"), "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]
    last = ""
    for model in dict.fromkeys(m for m in models if m):
        try:
            if vertex:
                r = requests.post(
                    f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
                    params={"key": key}, headers={"Content-Type": "application/json"}, json=body, timeout=60,
                )
            else:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=60,
                )
            data = r.json()
        except Exception as e:
            return None, str(e)
        if "candidates" in data:
            try:
                parts = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts), None
            except Exception:
                last = f"{model}: khali jawab"
                continue
        err = data.get("error", {}) if isinstance(data, dict) else {}
        msg = str(err.get("message", data))[:220]
        last = f"{model}: {msg}"
        if err.get("code") in (400, 401, 403) and ("API key" in msg or "credentials" in msg or "authentication" in msg.lower()):
            break                                # key ka masla hai, doosra model try karna bekaar
    hint = ""
    if "API key" in last or "credentials" in last:
        hint = (" | Key sahi copy karein: AI Studio key 'AIza' se, Vertex express key 'AQ.' se shuru hoti hai. "
                "Secrets mein double quotes ke andar daalein aur app Reboot karein.")
    return None, f"Gemini error: {last}{hint}"


def ask_ai(summary_text):
    """Pehle free Gemini, key na ho to Claude."""
    if get_secret("GEMINI_API_KEY"):
        return ask_gemini(summary_text)
    if get_secret("ANTHROPIC_API_KEY"):
        return ask_claude(summary_text)
    return None, ("Koi AI key nahi mila. Free ke liye GEMINI_API_KEY (aistudio.google.com) "
                  "Streamlit Secrets ya .env mein add karein.")


# ---------------- Candles (price chart ke liye) ----------------
@st.cache_data(ttl=30)
def fetch_candles(inst_key):
    key = enc(inst_key)

    def parse(data):
        cs = (data.get("data") or {}).get("candles") or []
        if not cs:
            return pd.DataFrame()
        c = pd.DataFrame(cs).iloc[:, :6]
        c.columns = ["time", "open", "high", "low", "close", "volume"]
        c["time"] = pd.to_datetime(c["time"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return c.sort_values("time").reset_index(drop=True)

    def why(r):
        try:
            e = r.get("errors") or []
            if e:
                return str(e[0].get("message") or e[0])[:200]
            return str(r.get("message") or r.get("status") or "khali jawab")[:200]
        except Exception:
            return str(r)[:200]

    err = ""
    try:
        r = requests.get(f"https://api.upstox.com/v2/historical-candle/intraday/{key}/1minute", headers=HEADERS, timeout=20).json()
        c = parse(r)
        if not c.empty:
            return c, "intraday", ""
        err = "Intraday: " + why(r)
        to, frm = date.today(), date.today() - timedelta(days=7)
        r = requests.get(f"https://api.upstox.com/v2/historical-candle/{key}/30minute/{to}/{frm}", headers=HEADERS, timeout=20).json()
        c = parse(r)
        if not c.empty:
            return c, "history", ""
        return pd.DataFrame(), "none", err + " | History: " + why(r)
    except Exception as e:
        return pd.DataFrame(), "error", f"{err} {e}".strip()


def render_charts(df, spot, ana, n_strikes, height=440):
    view = st.radio("Chart chuno", ["📈 Price + Zones", "📊 OI + Zones"], horizontal=True)
    if view.startswith("📈"):
        tf = st.radio("Timeframe", [1, 5, 15], index=1, horizontal=True, format_func=lambda m: f"{m} min")
        candles, src, err = fetch_candles(inst_key)
        if candles.empty:
            st.warning(
                "Candle data nahi mila, isliye sirf zones aur levels dikha raha hoon. "
                f"Upstox ka jawab: {err or 'khali'}"
            )
            fake = pd.DataFrame({"time": [pd.Timestamp.now()], "open": [spot], "high": [spot],
                                 "low": [spot], "close": [spot], "volume": [0]})
            components.html(price_chart_html(fake, spot, ana, "%H:%M", f"{inst_name}|fake", fit_all=True), height=height, scrolling=True)
        else:
            if src == "intraday":
                candles, xfmt = resample_candles(candles, tf), "%H:%M"
                st.caption("Aaj ke 1-min candles se bana chart. Gehri patti = Zone 1, halki patti = Zone 2, neeli dotted line = Liquidity.")
            else:
                xfmt = "%d %b %H:%M"
                st.caption("Aaj intraday data nahi, isliye pichle 7 din ke 30-min candles. Laal = Supply, Hara = Demand.")
            components.html(price_chart_html(candles.tail(200), spot, ana, xfmt, f"{inst_name}|{tf}|{src}"), height=height, scrolling=True)
    else:
        components.html(oi_chart_html(df, spot, min(n_strikes, 15), ana, inst_name), height=height, scrolling=True)
        st.caption("Laal bar = Call OI, Hara bar = Put OI. SUP1/SUP2 = Supply zones, DEM1/DEM2 = Demand zones, LIQ = Liquidity strike.")


# ---------------- Page ----------------
def render_page():
    df, spot = fetch_option_chain(inst_key, expiry_date)

    if df.empty:
        st.warning(f"⚠️ {inst_name} ka data load nahi hua! Upstox Access Token check karein. Stock ho to uske F&O options na hon ya symbol badal gaya ho to bhi data nahi aata.")
    else:
        ana = analyze(df, spot)
        supply, demand = ana["supply"], ana["demand"]

        if focus_mode:
            st.caption(f"{inst_name} Spot {spot:,.2f} | Expiry {expiry_date} | PCR {ana['pcr']:.2f}")
            components.html(
                build_table_html(df, spot, n_strikes, ana, show_summary, inst_name),
                height=min(160 + (2 * n_strikes + 2) * 50, 2200),
                scrolling=True,
            )
            return

        if chart_focus:
            st.caption(f"{inst_name} Spot {spot:,.2f} | Expiry {expiry_date} | Bias {ana['bias']}")
            render_charts(df, spot, ana, n_strikes, height=640)
            return

        # ---- Smart Analysis ----
        st.markdown("---")
        st.subheader("🧠 Smart Analysis – Supply / Demand Zone (OI + Volume based)")

        bias_col = "#dcfce7" if "BULL" in ana["bias"] else "#fee2e2" if "BEAR" in ana["bias"] else "#fef9c3"
        st.markdown(
            f'<div style="background:{bias_col};color:#111;padding:14px;border-radius:10px;font-size:18px;">'
            f'<b>Market Bias: {ana["bias"]}</b> &nbsp;|&nbsp; Score: {ana["points"]:+d} &nbsp;|&nbsp; PCR: {ana["pcr"]:.2f}'
            f' &nbsp;|&nbsp; Spot: {spot:,.2f}</div>',
            unsafe_allow_html=True,
        )
        st.write("")

        z1, z2 = st.columns(2)
        if supply:
            p = supply["primary"]
            z1.markdown(
                f'<div style="background:#fef2f2;color:#111;border-left:6px solid #ef4444;padding:12px;border-radius:8px">'
                f'<b>🔴 SUPPLY ZONE (Resistance)</b><br>'
                f'<span style="font-size:24px;font-weight:800">{supply["low"]} – {supply["high"]}</span><br>'
                f'Main level R1: <b>{p["strike"]}</b> • {strength_label(p["score"])}<br>'
                f'Call OI: {p["oi"] / 1e5:.1f}L • Chg: {p["chg"] / 1e5:+.1f}L • Vol: {fmt_vol(p["vol"])}<br>'
                f'{"Naya Call writing ➜ resistance mazboot" if p["fresh"] else "Call unwinding ➜ resistance kamzor"}</div>',
                unsafe_allow_html=True,
            )
        else:
            z1.info("Spot ke upar supply zone nahi mila.")
        if demand:
            p = demand["primary"]
            z2.markdown(
                f'<div style="background:#f0fdf4;color:#111;border-left:6px solid #22c55e;padding:12px;border-radius:8px">'
                f'<b>🟢 DEMAND ZONE (Support)</b><br>'
                f'<span style="font-size:24px;font-weight:800">{demand["low"]} – {demand["high"]}</span><br>'
                f'Main level S1: <b>{p["strike"]}</b> • {strength_label(p["score"])}<br>'
                f'Put OI: {p["oi"] / 1e5:.1f}L • Chg: {p["chg"] / 1e5:+.1f}L • Vol: {fmt_vol(p["vol"])}<br>'
                f'{"Naya Put writing ➜ support mazboot" if p["fresh"] else "Put unwinding ➜ support kamzor"}</div>',
                unsafe_allow_html=True,
            )
        else:
            z2.info("Spot ke niche demand zone nahi mila.")

        sup2, dem2 = ana["supply2"], ana["demand2"]
        if sup2 or dem2:
            y1c, y2c = st.columns(2)
            for colx, z, ttl, bg, bd, nm in (
                (y1c, sup2, "🔴 SUPPLY ZONE 2", "#fef2f2", "#f87171", "Call"),
                (y2c, dem2, "🟢 DEMAND ZONE 2", "#f0fdf4", "#4ade80", "Put"),
            ):
                if z:
                    p2 = z["primary"]
                    colx.markdown(
                        f'<div style="background:{bg};color:#111;border-left:6px solid {bd};padding:10px;border-radius:8px;margin-top:6px">'
                        f'<b>{ttl}</b><br><span style="font-size:20px;font-weight:800">{z["low"]} – {z["high"]}</span><br>'
                        f'Main level: <b>{p2["strike"]}</b> • {strength_label(p2["score"])}<br>'
                        f'{nm} OI: {p2["oi"] / 1e5:.1f}L • Chg: {p2["chg"] / 1e5:+.1f}L</div>',
                        unsafe_allow_html=True,
                    )
        if ana["liq"]:
            st.markdown(
                "💧 **Liquidity strikes** (sabse zyada OI + Volume, jahan price aksar atakta/kheenchta hai): "
                + " | ".join(f"**{q['strike']}** (OI {q['oi'] / 1e5:.0f}L, Vol {fmt_vol(q['vol'])})" for q in ana["liq"])
            )

        with st.expander("📌 Bias ke karan (reasons)", expanded=True):
            for rs in ana["reasons"]:
                st.write("• " + rs)
            if supply and demand:
                st.write(
                    f"• **Trade view:** {demand['primary']['strike']} ke upar tak support, "
                    f"{supply['primary']['strike']} ke niche tak resistance. "
                    f"{supply['primary']['strike']} ke upar mazboot close = breakout, "
                    f"{demand['primary']['strike']} ke niche close = breakdown."
                )

        lv_rows = []
        for name, z, pre in (("Supply 1", supply, "R"), ("Demand 1", demand, "S"),
                             ("Supply 2", ana["supply2"], "R2."), ("Demand 2", ana["demand2"], "S2.")):
            if z:
                for i, lv in enumerate(z["levels"]):
                    lv_rows.append({
                        "Level": f"{pre}{i + 1}", "Type": name, "Strike": lv["strike"],
                        "Score": round(lv["score"], 1), "OI (L)": round(lv["oi"] / 1e5, 1),
                        "OI Chg (L)": round(lv["chg"] / 1e5, 1), "Volume": int(lv["vol"]),
                        "Status": "Fresh writing" if lv["fresh"] else "Unwinding",
                    })
        if lv_rows:
            st.dataframe(pd.DataFrame(lv_rows), use_container_width=True, hide_index=True)

        if st.button("🤖 AI se detailed analysis (Gemini free / Claude)"):
            summary = {
                "instrument": inst_name, "spot": spot, "expiry": expiry_date, "bias": ana["bias"], "pcr": round(ana["pcr"], 2),
                "max_pain": ana["max_pain"], "supply_zone": supply, "demand_zone": demand,
                "supply_zone_2": ana["supply2"], "demand_zone_2": ana["demand2"], "liquidity_strikes": ana["liq"],
                "net_call_oi_change": ana["chg_c"], "net_put_oi_change": ana["chg_p"],
            }
            with st.spinner("AI analysis ho rahi hai..."):
                text, err = ask_ai(json.dumps(summary, default=float))
            st.session_state["ai_out"] = (inst_name, "warn" if err else "ok", err or text)
        ai_out = st.session_state.get("ai_out")
        if ai_out and ai_out[0] == inst_name:
            (st.warning if ai_out[1] == "warn" else st.markdown)(ai_out[2])

        # ---- Table ----
        st.markdown("---")
        st.subheader("📋 Complete Option Chain Matrix")
        st.caption(
            "Peela = ITM | Neela strike = ATM | Laal strike = Supply zone | Hara strike = Demand zone | "
            "Orange line = spot (market yahan chal raha hai)"
        )
        html = build_table_html(df, spot, n_strikes, ana, show_summary, inst_name)
        components.html(html, height=min(140 + (2 * n_strikes + 2) * 50, 1100), scrolling=True)

        # ---- Charts with Supply / Demand zones ----
        st.markdown("---")
        st.subheader("📈 Charts – Supply / Demand Zones")
        render_charts(df, spot, ana, n_strikes)


# Auto refresh: sirf data wala hissa dobara bante hai (fragment), poora page nahi, isliye screen smooth rehti hai
if auto_refresh and hasattr(st, "fragment"):
    st.fragment(run_every=f"{refresh_secs}s")(render_page)()
else:
    render_page()
    if auto_refresh:                      # purane Streamlit version ke liye
        time.sleep(refresh_secs)
        st.rerun()
