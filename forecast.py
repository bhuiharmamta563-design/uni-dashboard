"""
AI Forecast page - UNIUSD (Delta Exchange India) with Chronos-Bolt-small.
Isse apne repo mein  pages/forecast.py  naam se rakho.
"""
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import torch
from chronos import BaseChronosPipeline

st.set_page_config(page_title="AI Forecast", layout="wide")
st.title("🔮 AI Forecast (Chronos-Bolt)")
st.caption("⚠️ Ye sirf ek input hai, pakka signal nahi. Pehle backtest karo, phir hi bharosa karo.")

BASE_URL = "https://api.india.delta.exchange/v2/history/candles"
RES_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}

# ---------- Sidebar ----------
symbol = st.sidebar.text_input("Symbol", "UNIUSD")
res = st.sidebar.selectbox("Timeframe", list(RES_SECONDS), index=1)
horizon = st.sidebar.slider("Kitni candles aage ka forecast", 4, 48, 12)
context_len = st.sidebar.slider("Model ko kitni purani candles dikhayein", 128, 512, 256, step=64)
n_checks = st.sidebar.slider("Quick check: kitni purani windows par test", 10, 80, 40)


# ---------- Data ----------
@st.cache_data(ttl=60)
def get_candles(sym: str, resolution: str, n: int = 1000) -> pd.DataFrame:
    end = int(time.time())
    start = end - RES_SECONDS[resolution] * n
    r = requests.get(
        BASE_URL,
        params={"symbol": sym, "resolution": resolution, "start": start, "end": end},
        timeout=20,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json().get("result", []))
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.sort_values("time").reset_index(drop=True)


@st.cache_resource(show_spinner="Model load ho raha hai (pehli baar thoda time lagega)...")
def load_model():
    return BaseChronosPipeline.from_pretrained(
        "amazon/chronos-bolt-small",
        device_map="cpu",
        torch_dtype=torch.float32,
    )


def predict(pipe, contexts, horizon):
    """contexts: list of 1D numpy arrays. Return array (batch, horizon, 3) = p10, p50, p90."""
    tensors = [torch.tensor(c, dtype=torch.float32) for c in contexts]
    q, _ = pipe.predict_quantiles(
        context=tensors,
        prediction_length=horizon,
        quantile_levels=[0.1, 0.5, 0.9],
    )
    return q.numpy()


try:
    df = get_candles(symbol, res)
except Exception as e:
    st.error(f"Candles nahi aayi: {e}")
    st.stop()

if df.empty or len(df) < context_len + horizon + 10:
    st.warning("Data kam hai. Symbol ya timeframe check karo.")
    st.stop()

closes = df["close"].astype(float).to_numpy()
last = closes[-1]
pipe = load_model()

# ---------- Forecast ----------
fc = predict(pipe, [closes[-context_len:]], horizon)[0]  # (horizon, 3)
p10, p50, p90 = fc[-1]
move_pct = (p50 - last) / last * 100

c1, c2, c3 = st.columns(3)
c1.metric("Abhi ka price", f"{last:.4f}")
c2.metric(f"{horizon} candles baad (median)", f"{p50:.4f}", f"{move_pct:+.2f}%")
c3.metric("Range (p10 - p90)", f"{p10:.4f} - {p90:.4f}")

if p10 > last:
    st.success("🟢 Model ka rujhan: UP (poori range price se upar hai)")
elif p90 < last:
    st.error("🔴 Model ka rujhan: DOWN (poori range price se neeche hai)")
else:
    st.info("⚪ Model ka rujhan: saaf nahi (price range ke andar hai) - koi trade signal nahi")

# ---------- Chart ----------
show = df.tail(150)
step = pd.Timedelta(seconds=RES_SECONDS[res])
future_t = [df["time"].iloc[-1] + step * (i + 1) for i in range(horizon)]

fig = go.Figure()
fig.add_trace(go.Candlestick(
    x=show["time"], open=show["open"], high=show["high"], low=show["low"], close=show["close"],
    name="Price", increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
))
fig.add_trace(go.Scatter(x=future_t, y=fc[:, 2], line=dict(width=0), showlegend=False, hoverinfo="skip"))
fig.add_trace(go.Scatter(
    x=future_t, y=fc[:, 0], fill="tonexty", line=dict(width=0),
    fillcolor="rgba(59,130,246,0.25)", name="p10-p90 range",
))
fig.add_trace(go.Scatter(x=future_t, y=fc[:, 1], line=dict(color="#2563eb", width=2), name="Forecast (median)"))
fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, use_container_width=True)

# ---------- Quick walk-forward check ----------
st.subheader("Quick check: model pehle kitna sahi tha?")
st.caption("Purani windows par model ne jo direction bataya, wo asli price se mila ya nahi. 50% = sikka uchhalne jaisa.")

if st.button("Check chalao"):
    ends = [len(closes) - horizon - k * horizon for k in range(n_checks)]
    ends = [e for e in ends if e >= context_len]
    contexts = [closes[e - context_len:e] for e in ends]
    preds = predict(pipe, contexts, horizon)[:, -1, 1]  # median at final step
    base = np.array([closes[e - 1] for e in ends])
    actual = np.array([closes[e + horizon - 1] for e in ends])
    pred_dir = np.sign(preds - base)
    real_dir = np.sign(actual - base)
    acc = float((pred_dir == real_dir).mean() * 100)
    st.metric("Direction sahi", f"{acc:.0f}%", f"{len(ends)} windows")
    if acc < 55:
        st.warning("Ye sikka uchhalne ke aas-paas hai. Abhi is par trade ka bharosa mat karo.")
    else:
        st.info("Thoda behtar hai, par itni kam windows se pakka nahi kah sakte. Poore backtester mein fees ke saath test karo.")
