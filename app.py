import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# Page setup
st.set_page_config(layout="wide", page_title="Ultimate Delta Perpetuals Terminal & AI Signal Engine")

# Auto refresh every 5 seconds
st_autorefresh(interval=5000, limit=100, key="delta_perp_refresh")

st.title("⚡ Delta Exchange Perpetuals: AI Support, Resistance & Reversal Engine")

# Fetch all perpetual products from Delta Exchange India API
@st.cache_data(ttl=10)
def fetch_delta_perpetuals():
    url = "https://api.india.delta.exchange/v2/products"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            products = response.json().get('result', [])
            perps = [p for p in products if p.get('contract_type') == 'perpetual']
            return perps
        return []
    except:
        return []

@st.cache_data(ttl=3)
def fetch_tickers():
    url = "https://api.india.delta.exchange/v2/tickers"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json().get('result', [])
        return []
    except:
        return []

perps = fetch_delta_perpetuals()
tickers = fetch_tickers()

if perps and tickers:
    # Extract available symbols (e.g., UNIUSD, AVAXUSD, BTCUSD, ETHUSD)
    perp_symbols = [p.get('symbol') for p in perps if p.get('symbol')]
    perp_symbols = sorted(list(set(perp_symbols)))
    
    # Selection box for Perpetual contracts
    selected_symbol = st.selectbox("Crypto Contract Select Karein (UNIUSD, AVAXUSD, BTCUSD, etc.):", perp_symbols, index=0)

    # Find product details for selected symbol
    selected_perp = next((p for p in perps if p.get('symbol') == selected_symbol), {})
    selected_ticker = next((t for t in tickers if t.get('symbol') == selected_symbol), {})

    # Extract metrics
    mark_price = float(selected_ticker.get('mark_price', 8.31))
    spot_price = float(selected_ticker.get('spot_price', mark_price))
    high_24h = float(selected_ticker.get('high_24h', mark_price * 1.05))
    low_24h = float(selected_ticker.get('low_24h', mark_price * 0.95))
    change_24h = float(selected_ticker.get('price_change_24h', 0.0))
    volume_24h = float(selected_ticker.get('volume', 0.0))
    open_interest = float(selected_ticker.get('open_interest', 0.0))
    funding_rate = float(selected_ticker.get('funding_rate', 0.0001)) * 100

    # ----------------------------------------------------
    # Dynamic Support & Resistance Calculation Engine
    # ----------------------------------------------------
    pivot = (high_24h + low_24h + mark_price) / 3
    r1 = round((2 * pivot) - low_24h, 3)
    r2 = round(pivot + (high_24h - low_24h), 3)
    s1 = round((2 * pivot) - high_24h, 3)
    s2 = round(pivot - (high_24h - low_24h), 3)

    # Reversal Trigger Level
    reversal_trigger = round(pivot, 3)

    # Dynamic Top Metric Cards
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Mark Price", f"${mark_price}", f"{change_24h}%", delta_color="inverse")
    col2.metric("Support 1 (S1)", f"${s1}")
    col3.metric("Resistance 1 (R1)", f"${r1}")
    col4.metric("Pivot / Reversal", f"${reversal_trigger}")
    col5.metric("Funding Rate", f"{funding_rate:.4f}%")

    st.info(f"🛡️ **Support Zones:** `${s1}` | `${s2}`  |  ⚠️ **Resistance Zones:** `${r1}` | `${r2}`  |  🔄 **Pivot Crossover:** `${reversal_trigger}`")

    # 🤖 AI Market Analyst & S/R Breakout Signals
    def generate_perp_ai_insights(symbol, price, change, funding, s1, s2, r1, r2, rev):
        insights = []
        sentiment = "Neutral"
        
        if price >= r1:
            sentiment = "Strong Bullish Breakout 🚀"
            insights.append(f"**{symbol}** is trading near/above Resistance 1 (**${r1}**). High momentum breakout active!")
        elif price <= s1:
            sentiment = "Strong Bearish Breakdown 📉"
            insights.append(f"**{symbol}** is testing Support 1 (**${s1}**). Watch out for potential panic selling or reversal bounce.")
        elif change > 3.0:
            sentiment = "Bullish Bias 🟢"
            insights.append(f"**{symbol}** is displaying upward price action with a **+{change}%** gain in 24h.")
        elif change < -3.0:
            sentiment = "Bearish Bias 🔴"
            insights.append(f"**{symbol}** is showing downward price action with a **{change}%** loss in 24h.")
        else:
            sentiment = "Rangebound Consolidation ⚪"
            insights.append(f"Price is locked inside range **${s1}** - **${r1}**.")

        # Reversal guidance
        insights.append(f"**Reversal Level:** Pivot trigger at **${rev}**. If price holds above this level, expect retest of **${r1}** & **${r2}**.")
        insights.append(f"**Funding Rate Analysis:** `{funding:.4f}%` — indicates market positioning balance.")

        return sentiment, insights

    ai_sentiment, ai_bullets = generate_perp_ai_insights(selected_symbol, mark_price, change_24h, funding_rate, s1, s2, r1, r2, reversal_trigger)

    with st.expander(f"🤖 **AI Support, Resistance & Reversal Analysis — {selected_symbol}**", expanded=True):
        st.markdown(f"### Market Sentiment: **{ai_sentiment}**")
        for bullet in ai_bullets:
            st.markdown(f"- {bullet}")

    # Trading Quantity / Order Simulator
    st.markdown("### 📊 Trade Simulator & Leverage Order")
    q_col1, q_col2, q_col3 = st.columns(3)
    with q_col1:
        lot_qty = st.number_input("Quantity (Lots)", min_value=1, value=10, step=1)
    with q_col2:
        st.info(f"Estimated Position Size: **${(lot_qty * mark_price):,.2f}**")
    with q_col3:
        leverage = st.slider("Leverage", min_value=1, max_value=50, value=10)

    trade_col1, trade_col2 = st.columns(2)
    with trade_col1:
        if st.button("🟢 Place LONG Order (Buy)", use_container_width=True):
            st.success(f"LONG Order placed for {lot_qty} lots of {selected_symbol} at ${mark_price} with {leverage}x leverage!")
    with trade_col2:
        if st.button("🔴 Place SHORT Order (Sell)", use_container_width=True):
            st.error(f"SHORT Order placed for {lot_qty} lots of {selected_symbol} at ${mark_price} with {leverage}x leverage!")

    # Interactive Chart with Support, Resistance & Pivot Lines
    st.markdown("### 📈 Live Chart with Support & Resistance Lines")
    fig = go.Figure()
    
    # Mocking price path
    base_p = mark_price * (1 - (change_24h/100)) if change_24h != 0 else mark_price * 0.98
    prices = [base_p + (mark_price - base_p) * (i / 10) for i in range(11)]
    times = [f"10:{i*5:02d}" for i in range(11)]
    
    fig.add_trace(go.Scatter(x=times, y=prices, mode='lines+markers', name=f'{selected_symbol} Price', line_color='#38bdf8', line_width=3))

    # Add S/R horizontal lines
    fig.add_hline(y=r1, line_dash="dash", line_color="#ff7675", annotation_text=f"Resistance 1 (${r1})")
    fig.add_hline(y=r2, line_dash="dot", line_color="#d63031", annotation_text=f"Resistance 2 (${r2})")
    fig.add_hline(y=s1, line_dash="dash", line_color="#55efc4", annotation_text=f"Support 1 (${s1})")
    fig.add_hline(y=s2, line_dash="dot", line_color="#00b894", annotation_text=f"Support 2 (${s2})")
    fig.add_hline(y=reversal_trigger, line_dash="solid", line_color="#fdcb6e", line_width=2, annotation_text=f"Pivot Reversal (${reversal_trigger})")

    fig.update_layout(title=f"{selected_symbol} Price with S/R Levels", xaxis_title="Time", yaxis_title="Price ($)", template="plotly_dark", height=550)
    st.plotly_chart(fig, use_container_width=True)

else:
    st.warning("Delta Exchange Perpetuals data fetch nahi ho pa raha hai.")
    
