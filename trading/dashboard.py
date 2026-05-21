import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone
import time
import os
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bot as bot_module
from bot import (
    load_trades, load_activity, get_open_trades,
    add_trade, close_trade, reset_all_data, clear_activity, log_activity,
)
from strategy import get_indicators
from risk import RiskManager, RiskSettings

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AlphaTrade",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Inline CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── base ── */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #0d1117;
    color: #c9d1d9;
}
[data-testid="stSidebar"] {
    background-color: #161b22;
    border-right: 1px solid #21262d;
}
[data-testid="stSidebar"] * { color: #c9d1d9 !important; }

/* ── cards ── */
.metric-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 16px 20px;
}
.metric-card .label {
    font-size: 11px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
}
.metric-card .value {
    font-size: 24px;
    font-weight: 700;
    color: #f0f6fc;
    line-height: 1;
}
.metric-card .value.green { color: #3fb950; }
.metric-card .value.red   { color: #f85149; }

/* ── section headers ── */
.section-header {
    font-size: 18px;
    font-weight: 600;
    color: #f0f6fc;
    border-bottom: 1px solid #21262d;
    padding-bottom: 8px;
    margin: 24px 0 16px 0;
}

/* ── status pill ── */
.status-running {
    display: inline-block;
    background: #238636;
    color: #fff;
    border-radius: 20px;
    padding: 4px 14px;
    font-weight: 700;
    font-size: 13px;
}
.status-stopped {
    display: inline-block;
    background: #6e7681;
    color: #fff;
    border-radius: 20px;
    padding: 4px 14px;
    font-weight: 700;
    font-size: 13px;
}

/* ── live price banner ── */
.price-banner {
    background: #0d2137;
    border: 1px solid #1f4f7a;
    border-radius: 6px;
    padding: 10px 16px;
    font-size: 14px;
    color: #58a6ff;
    margin-bottom: 12px;
}

/* ── activity log entries ── */
.log-entry { font-size: 12px; line-height: 1.6; padding: 3px 0; border-bottom: 1px solid #21262d; }
.log-INFO    { color: #c9d1d9; }
.log-SIGNAL  { color: #58a6ff; font-weight: 600; }
.log-ORDER   { color: #3fb950; font-weight: 600; }
.log-WARNING { color: #d29922; }
.log-ERROR   { color: #f85149; font-weight: 600; }

/* ── trade table ── */
[data-testid="stDataFrame"] { border: 1px solid #21262d; border-radius: 6px; }

/* ── buttons ── */
.stButton > button {
    border-radius: 6px;
    font-weight: 600;
    transition: all 0.15s;
}
</style>
""", unsafe_allow_html=True)


# ── Session state defaults ────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "client": None,
        "connected": False,
        "paper_mode": True,
        "symbol": "BTCUSDT",
        "strategy": "EMA Crossover",
        "interval": "5m",
        "check_every": 30,
        "threshold": 0.30,
        "risk": RiskSettings(),
        "risk_manager": RiskManager(),
        "initial_balance": 1000.0,
        "manual_amount": 100.0,
        "testnet": True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client():
    return st.session_state.get("client")

def _fmt_pnl(val):
    if val is None:
        return "—"
    color = "green" if val >= 0 else "red"
    return f'<span style="color:{"#3fb950" if val>=0 else "#f85149"}">${val:+.4f}</span>'

def _fmt_pct(val):
    if val is None:
        return "—"
    return f'<span style="color:{"#3fb950" if val>=0 else "#f85149"}">{val:+.2f}%</span>'

def _pnl_color(val):
    if val is None or val == 0:
        return ""
    return "green" if val >= 0 else "red"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
           "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"]
INTERVALS = {"1m": "1 min", "3m": "3 min", "5m": "5 min", "15m": "15 min",
             "30m": "30 min", "1h": "1 hour", "4h": "4 hour", "1d": "1 day"}
STRATEGIES = ["EMA Crossover", "Price Movement", "Momentum (RSI)"]


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🔑 API Connection")

    testnet = st.toggle("Use Binance Testnet", value=st.session_state.testnet)
    st.session_state.testnet = testnet

    if testnet:
        st.info("🧪 Testnet mode — get keys at testnet.binance.vision")
    else:
        st.warning("⚠️ LIVE mode — real money at risk!")

    api_key = st.text_input("API Key", type="password", placeholder="Paste your API key")
    api_secret = st.text_input("API Secret", type="password", placeholder="Paste your API secret")

    if st.button("🔌 Connect", use_container_width=True):
        if not api_key or not api_secret:
            st.error("Both API key and secret are required.")
        else:
            with st.spinner("Connecting…"):
                try:
                    from binance_client import BinanceClient
                    c = BinanceClient(api_key, api_secret, testnet=testnet)
                    ok, msg = c.test_connection()
                    if ok:
                        st.session_state.client = c
                        st.session_state.connected = True
                        st.success(f"✅ {msg}")
                        log_activity("INFO", f"Connected to {'Testnet' if testnet else 'LIVE'} Binance")
                    else:
                        st.error(f"❌ {msg}")
                except Exception as e:
                    st.error(f"Error: {e}")

    if st.session_state.connected:
        st.success("🟢 Connected")
    else:
        st.warning("🔴 Not connected")

    st.divider()
    st.markdown("## ⚙️ Trading Settings")

    sym = st.selectbox("Symbol", SYMBOLS,
                        index=SYMBOLS.index(st.session_state.symbol)
                        if st.session_state.symbol in SYMBOLS else 0)
    st.session_state.symbol = sym

    strat = st.selectbox("Strategy", STRATEGIES,
                          index=STRATEGIES.index(st.session_state.strategy)
                          if st.session_state.strategy in STRATEGIES else 0)
    st.session_state.strategy = strat

    intv = st.selectbox("Chart interval", list(INTERVALS.keys()),
                         index=list(INTERVALS.keys()).index(st.session_state.interval)
                         if st.session_state.interval in INTERVALS else 2,
                         format_func=lambda k: INTERVALS[k])
    st.session_state.interval = intv

    paper = st.toggle("Paper mode (no real orders)", value=st.session_state.paper_mode)
    st.session_state.paper_mode = paper

    if paper:
        init_bal = st.number_input(
            "Simulated balance (USDT)",
            min_value=100.0, max_value=1_000_000.0,
            value=st.session_state.initial_balance, step=100.0
        )
        st.session_state.initial_balance = init_bal

    st.divider()
    st.markdown("## 🛡️ Risk Management")

    r = st.session_state.risk
    r.risk_per_trade_pct = st.slider("Risk per trade (%)", 0.5, 20.0, r.risk_per_trade_pct, 0.5)
    r.stop_loss_pct      = st.slider("Stop loss (%)",      0.5, 20.0, r.stop_loss_pct, 0.5)
    r.take_profit_pct    = st.slider("Take profit (%)",    0.5, 50.0, r.take_profit_pct, 0.5)
    r.max_daily_loss_pct = st.slider("Max daily loss (%)", 1.0, 30.0, r.max_daily_loss_pct, 0.5)
    r.max_open_trades    = st.slider("Max open trades",    1,   20,   r.max_open_trades, 1)

    st.session_state.risk_manager.settings = r

    if st.button("⚡ Apply Risk Settings", use_container_width=True):
        st.success("Risk settings applied")

    st.divider()
    if st.button("🗑️ Clear All Data", use_container_width=True, type="secondary"):
        reset_all_data()
        st.rerun()

    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)


# ── Main area ─────────────────────────────────────────────────────────────────

bot_inst = bot_module.get_bot()
bot_running = bot_inst.is_running() if bot_inst else False

# ── Title row ─────────────────────────────────────────────────────────────────
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("# AlphaTrade")
    exchange_label = ("Binance Testnet" if st.session_state.testnet else "Binance Live")
    mode_label = "Paper simulation" if st.session_state.paper_mode else "Live trading"
    st.caption(f"{exchange_label} · {mode_label}")
with col_status:
    st.markdown("<br>", unsafe_allow_html=True)
    if bot_running:
        st.markdown('<span class="status-running">● RUNNING</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="status-stopped">● STOPPED</span>', unsafe_allow_html=True)

st.divider()

# ── Controls row ──────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Controls</div>', unsafe_allow_html=True)

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    if st.button("▶ Start Bot", use_container_width=True, type="primary",
                 disabled=bot_running or not st.session_state.connected):
        if not st.session_state.connected and not st.session_state.paper_mode:
            st.error("Connect to Binance first.")
        else:
            from binance_client import BinanceClient
            client = _client()
            if client is None and st.session_state.paper_mode:
                st.warning("Connect to Binance even for paper mode (to get live prices).")
            elif client:
                b = bot_module.create_bot(
                    client=client,
                    symbol=st.session_state.symbol,
                    strategy=st.session_state.strategy,
                    risk_manager=st.session_state.risk_manager,
                    interval=st.session_state.interval,
                    check_every=st.session_state.check_every,
                    paper_mode=st.session_state.paper_mode,
                    threshold=st.session_state.threshold / 100,
                )
                b.start()
                st.rerun()

with c2:
    if st.button("⏹ Stop Bot", use_container_width=True, disabled=not bot_running):
        bot_module.stop_bot()
        st.rerun()

with c3:
    if st.button("🚨 Emergency Stop", use_container_width=True, type="secondary"):
        st.session_state.risk.emergency_stop = True
        bot_module.stop_bot()
        log_activity("WARNING", "🚨 EMERGENCY STOP activated — all trading halted")
        st.rerun()

with c4:
    if st.session_state.risk.emergency_stop:
        if st.button("✅ Resume Trading", use_container_width=True):
            st.session_state.risk.emergency_stop = False
            log_activity("INFO", "✅ Emergency stop cleared — trading resumed")
            st.rerun()
    else:
        auto_mode = st.toggle("Auto Mode", value=bot_running)

with c5:
    buy_clicked = st.button("📈 Manual BUY", use_container_width=True,
                             disabled=not st.session_state.connected)
with c6:
    sell_clicked = st.button("📉 Manual SELL", use_container_width=True,
                              disabled=not st.session_state.connected)

# ── Strategy tuning ───────────────────────────────────────────────────────────
with st.expander("⚙️ Strategy Parameters", expanded=False):
    exp_c1, exp_c2, exp_c3 = st.columns(3)
    with exp_c1:
        thr = st.slider(
            "Price threshold % (for Price Movement strategy)",
            0.01, 2.0, st.session_state.threshold, 0.01,
            help="Triggers BUY/SELL when price moves ± this % in one candle"
        )
        st.session_state.threshold = thr
    with exp_c2:
        ck = st.slider("Bot check interval (seconds)", 10, 300, st.session_state.check_every, 10)
        st.session_state.check_every = ck
    with exp_c3:
        manual_amount = st.number_input(
            "Manual order amount (USDT)",
            min_value=10.0, max_value=100_000.0,
            value=st.session_state.manual_amount, step=10.0
        )
        st.session_state.manual_amount = manual_amount

    if st.button("Apply Strategy Settings", use_container_width=True):
        if bot_inst:
            bot_inst.update_settings(
                strategy=st.session_state.strategy,
                interval=st.session_state.interval,
                check_every=st.session_state.check_every,
                threshold=st.session_state.threshold / 100,
            )
        st.success("✅ Strategy settings applied")

# ── Manual trade execution ────────────────────────────────────────────────────
if buy_clicked or sell_clicked:
    side = "BUY" if buy_clicked else "SELL"
    client = _client()
    if client is None:
        st.error("Not connected to Binance.")
    else:
        try:
            price = client.get_symbol_price(st.session_state.symbol)
            invested = st.session_state.manual_amount
            qty = invested / price
            qty = client.round_quantity(st.session_state.symbol, qty)

            if not st.session_state.paper_mode:
                order = client.place_market_order(st.session_state.symbol, side, qty)
                fill = float(order.get("fills", [{}])[0].get("price", price))
                price = fill
                log_activity("ORDER", f"✅ LIVE MANUAL {side} | {qty} {st.session_state.symbol} @ ${price:.4f}")
            else:
                log_activity("ORDER", f"📋 PAPER MANUAL {side} | {qty:.6f} {st.session_state.symbol} @ ${price:.4f}")

            rm = st.session_state.risk_manager
            trade = {
                "coin": st.session_state.symbol,
                "exchange": "Binance Testnet" if st.session_state.testnet else "Binance Live",
                "type": "manual",
                "strategy": "Manual",
                "side": side,
                "entry_price": price,
                "exit_price": None,
                "quantity": qty,
                "invested": invested,
                "profit_loss": None,
                "profit_loss_pct": None,
                "open_time": datetime.now().isoformat(),
                "close_time": None,
                "reason": f"Manual {side} via dashboard",
                "close_reason": None,
                "stop_loss": rm.stop_loss_price(price, side),
                "take_profit": rm.take_profit_price(price, side),
                "status": "open",
                "paper": st.session_state.paper_mode,
            }
            added = add_trade(trade)
            st.success(f"✅ {side} recorded | ID: {added['id']} | {qty:.6f} {st.session_state.symbol} @ ${price:.4f}")
        except Exception as e:
            st.error(f"Order error: {e}")

# ── Open positions close buttons ──────────────────────────────────────────────
open_trades = get_open_trades()
if open_trades:
    st.markdown('<div class="section-header">Open Positions</div>', unsafe_allow_html=True)
    for ot in open_trades:
        pc1, pc2, pc3, pc4, pc5 = st.columns([2, 2, 2, 2, 2])
        pc1.write(f"**{ot.get('coin','?')}**")
        pc2.write(f"{ot.get('side','?')} @ ${ot.get('entry_price',0):.4f}")
        pc3.write(f"{'🤖 Bot' if ot.get('type')=='bot' else '👤 Manual'}")
        pc4.write(f"SL: ${ot.get('stop_loss',0):.4f} | TP: ${ot.get('take_profit',0):.4f}")
        if pc5.button(f"Close {ot.get('id','?')}", key=f"close_{ot.get('id')}"):
            client = _client()
            price = client.get_symbol_price(ot["coin"]) if client else ot["entry_price"]
            close_trade(ot["id"], price, "Closed manually via dashboard")
            log_activity("ORDER", f"👤 Manual close | Trade {ot['id']} @ ${price:.4f}")
            st.rerun()

st.divider()

# ── Fund Overview ─────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Fund Overview</div>', unsafe_allow_html=True)

all_trades = load_trades()
closed = [t for t in all_trades if t.get("status") == "closed"]
total_pnl = sum((t.get("profit_loss") or 0) for t in closed)
today_str = datetime.now().strftime("%Y-%m-%d")
daily_pnl = sum(
    (t.get("profit_loss") or 0) for t in closed
    if (t.get("close_time") or "").startswith(today_str)
)
wins = sum(1 for t in closed if (t.get("profit_loss") or 0) >= 0)
win_rate = (wins / len(closed) * 100) if closed else 0.0

# Get live balance
balance_display = "—"
live_price = None
if st.session_state.connected and _client():
    try:
        if not st.session_state.paper_mode:
            bal = _client().get_account_balance("USDT")
            balance_display = f"${bal:,.2f}"
        else:
            sim_bal = st.session_state.initial_balance + total_pnl
            balance_display = f"${sim_bal:,.2f}"
        live_price = _client().get_symbol_price(st.session_state.symbol)
    except Exception:
        pass
else:
    sim_bal = st.session_state.initial_balance + total_pnl
    balance_display = f"${sim_bal:,.2f}"

equity = st.session_state.initial_balance + total_pnl
roi = (total_pnl / st.session_state.initial_balance * 100) if st.session_state.initial_balance else 0

mc1, mc2, mc3, mc4, mc5 = st.columns(5)
with mc1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="label">Total Equity</div>
        <div class="value">${equity:,.2f}</div>
    </div>""", unsafe_allow_html=True)
with mc2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="label">Balance</div>
        <div class="value">{balance_display}</div>
    </div>""", unsafe_allow_html=True)
with mc3:
    roi_cls = "green" if roi >= 0 else "red"
    st.markdown(f"""
    <div class="metric-card">
        <div class="label">ROI</div>
        <div class="value {roi_cls}">{roi:+.2f}%</div>
    </div>""", unsafe_allow_html=True)
with mc4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="label">Open Positions</div>
        <div class="value">{len(open_trades)}</div>
    </div>""", unsafe_allow_html=True)
with mc5:
    dpnl_cls = "green" if daily_pnl >= 0 else "red"
    st.markdown(f"""
    <div class="metric-card">
        <div class="label">Daily P&L</div>
        <div class="value {dpnl_cls}">${daily_pnl:+.2f}</div>
    </div>""", unsafe_allow_html=True)

# ── Live price banner ─────────────────────────────────────────────────────────
if live_price:
    st.markdown(
        f'<div class="price-banner">💹 {st.session_state.symbol} live price: '
        f'<strong>${live_price:,.4f}</strong> &nbsp;|&nbsp; '
        f'Win rate: <strong>{win_rate:.1f}%</strong> ({wins}/{len(closed)}) &nbsp;|&nbsp; '
        f'Total P&L: <strong>${total_pnl:+.4f}</strong></div>',
        unsafe_allow_html=True
    )

st.divider()

# ── Chart ─────────────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Chart</div>', unsafe_allow_html=True)

df_chart = None
if st.session_state.connected and _client():
    try:
        df_raw = _client().get_klines(
            st.session_state.symbol, st.session_state.interval, limit=200
        )
        df_chart = get_indicators(df_raw)
    except Exception as e:
        st.warning(f"Chart data unavailable: {e}")
else:
    st.info("Connect to Binance to see the live chart.")

if df_chart is not None:
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.04,
        subplot_titles=("", "Stochastic Oscillator"),
    )

    # ── Candlestick ──
    fig.add_trace(go.Candlestick(
        x=df_chart["open_time"],
        open=df_chart["open"],
        high=df_chart["high"],
        low=df_chart["low"],
        close=df_chart["close"],
        name=st.session_state.symbol,
        increasing_line_color="#3fb950",
        decreasing_line_color="#f85149",
        increasing_fillcolor="#3fb950",
        decreasing_fillcolor="#f85149",
    ), row=1, col=1)

    # ── EMAs ──
    fig.add_trace(go.Scatter(
        x=df_chart["open_time"], y=df_chart["ema9"],
        line=dict(color="#ff7b72", width=1.5),
        name="EMA 9",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df_chart["open_time"], y=df_chart["ema21"],
        line=dict(color="#e3b341", width=1.5),
        name="EMA 21",
    ), row=1, col=1)

    # ── Trade markers ──
    all_chart_trades = load_trades()
    manual_buy_x, manual_buy_y = [], []
    manual_sell_x, manual_sell_y = [], []
    bot_buy_x, bot_buy_y = [], []
    bot_sell_x, bot_sell_y = [], []

    for t in all_chart_trades:
        try:
            ts = pd.to_datetime(t.get("open_time"))
            price_m = t.get("entry_price")
            if price_m is None:
                continue
            trade_type = t.get("type", "manual")
            side = t.get("side", "BUY")
            if trade_type == "manual":
                if side == "BUY":
                    manual_buy_x.append(ts); manual_buy_y.append(price_m)
                else:
                    manual_sell_x.append(ts); manual_sell_y.append(price_m)
            else:
                if side == "BUY":
                    bot_buy_x.append(ts); bot_buy_y.append(price_m)
                else:
                    bot_sell_x.append(ts); bot_sell_y.append(price_m)
            # Exit marker
            if t.get("exit_price") and t.get("close_time"):
                ts_exit = pd.to_datetime(t["close_time"])
                ex_price = t["exit_price"]
                if trade_type == "manual":
                    if side == "BUY":
                        manual_sell_x.append(ts_exit); manual_sell_y.append(ex_price)
                    else:
                        manual_buy_x.append(ts_exit); manual_buy_y.append(ex_price)
                else:
                    if side == "BUY":
                        bot_sell_x.append(ts_exit); bot_sell_y.append(ex_price)
                    else:
                        bot_buy_x.append(ts_exit); bot_buy_y.append(ex_price)
        except Exception:
            continue

    if manual_buy_x:
        fig.add_trace(go.Scatter(
            x=manual_buy_x, y=manual_buy_y, mode="markers",
            marker=dict(symbol="triangle-up", size=14, color="#58a6ff", line=dict(color="#fff", width=1)),
            name="Manual BUY",
        ), row=1, col=1)
    if manual_sell_x:
        fig.add_trace(go.Scatter(
            x=manual_sell_x, y=manual_sell_y, mode="markers",
            marker=dict(symbol="triangle-down", size=14, color="#d2a8ff", line=dict(color="#fff", width=1)),
            name="Manual SELL",
        ), row=1, col=1)
    if bot_buy_x:
        fig.add_trace(go.Scatter(
            x=bot_buy_x, y=bot_buy_y, mode="markers",
            marker=dict(symbol="triangle-up", size=12, color="#3fb950", line=dict(color="#fff", width=1)),
            name="Bot BUY",
        ), row=1, col=1)
    if bot_sell_x:
        fig.add_trace(go.Scatter(
            x=bot_sell_x, y=bot_sell_y, mode="markers",
            marker=dict(symbol="triangle-down", size=12, color="#f85149", line=dict(color="#fff", width=1)),
            name="Bot SELL",
        ), row=1, col=1)

    # ── Stochastic ──
    fig.add_trace(go.Scatter(
        x=df_chart["open_time"], y=df_chart["stoch_k"],
        line=dict(color="#58a6ff", width=1.5),
        name="%K",
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=df_chart["open_time"], y=df_chart["stoch_d"],
        line=dict(color="#f0f6fc", width=1.2),
        name="%D",
    ), row=2, col=1)
    fig.add_hline(y=80, line=dict(color="#f85149", width=1, dash="dash"), row=2, col=1)
    fig.add_hline(y=20, line=dict(color="#3fb950", width=1, dash="dash"), row=2, col=1)

    fig.update_layout(
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", family="monospace", size=11),
        xaxis_rangeslider_visible=False,
        height=560,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(22,27,34,0.9)",
            bordercolor="#21262d",
            borderwidth=1,
            font=dict(size=10),
        ),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#21262d", zerolinecolor="#21262d", showspikes=True)
    fig.update_yaxes(gridcolor="#21262d", zerolinecolor="#21262d", showspikes=True)
    fig.update_yaxes(range=[0, 100], row=2, col=1)

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

st.divider()

# ── Trade History ─────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Trade History</div>', unsafe_allow_html=True)

all_trades_display = load_trades()
if not all_trades_display:
    st.markdown('<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:14px;color:#8b949e;">No trades yet.</div>', unsafe_allow_html=True)
else:
    rows = []
    for t in reversed(all_trades_display):
        pnl = t.get("profit_loss")
        pct = t.get("profit_loss_pct")
        rows.append({
            "ID": t.get("id", "—"),
            "Coin": t.get("coin", "—"),
            "Type": "🤖 Bot" if t.get("type") == "bot" else "👤 Manual",
            "Side": t.get("side", "—"),
            "Strategy": t.get("strategy", "—"),
            "Entry $": f"{t.get('entry_price', 0):.4f}",
            "Exit $": f"{t.get('exit_price', 0):.4f}" if t.get("exit_price") else "open",
            "Invested": f"${t.get('invested', 0):.2f}",
            "P&L $": f"{pnl:+.4f}" if pnl is not None else "—",
            "P&L %": f"{pct:+.2f}%" if pct is not None else "—",
            "Status": t.get("status", "—"),
            "Open": (t.get("open_time") or "")[:16].replace("T", " "),
            "Close": (t.get("close_time") or "")[:16].replace("T", " ") if t.get("close_time") else "—",
            "Reason": (t.get("reason") or "")[:60],
        })
    df_trades = pd.DataFrame(rows)

    def _color_row(val, col):
        try:
            v = float(val.replace("$", "").replace("%", "").replace("+", ""))
            if col in ("P&L $", "P&L %"):
                return "color: #3fb950" if v >= 0 else "color: #f85149"
        except Exception:
            pass
        return ""

    st.dataframe(
        df_trades,
        use_container_width=True,
        hide_index=True,
        column_config={
            "P&L $": st.column_config.TextColumn("P&L $"),
            "P&L %": st.column_config.TextColumn("P&L %"),
            "Reason": st.column_config.TextColumn("Reason", width="large"),
        },
    )

st.divider()

# ── Activity Log ──────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Activity Log</div>', unsafe_allow_html=True)

col_log_hdr, col_log_clear = st.columns([5, 1])
with col_log_clear:
    if st.button("🗑️ Clear Log", use_container_width=True):
        clear_activity()
        st.rerun()

activity = load_activity()
if not activity:
    st.markdown('<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:14px;color:#8b949e;">No activity yet.</div>', unsafe_allow_html=True)
else:
    lines_html = []
    for entry in reversed(activity[-200:]):
        ts = (entry.get("time") or "")[:19].replace("T", " ")
        lvl = entry.get("level", "INFO")
        msg = entry.get("message", "")
        lines_html.append(
            f'<div class="log-entry log-{lvl}">'
            f'<span style="color:#6e7681;margin-right:8px;">{ts}</span>'
            f'<span style="color:#8b949e;margin-right:8px;font-size:10px;">[{lvl}]</span>'
            f'{msg}</div>'
        )
    st.markdown(
        f'<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;'
        f'padding:14px;max-height:400px;overflow-y:auto;font-family:monospace;">'
        + "".join(lines_html)
        + "</div>",
        unsafe_allow_html=True,
    )

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(30)
    st.rerun()
