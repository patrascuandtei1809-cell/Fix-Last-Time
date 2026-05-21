import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import time
import os
import sys

# ── Path ──────────────────────────────────────────────────────────────────────
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
    initial_sidebar_state="collapsed",
)

# ── Premium CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Reset & base ── */
* { box-sizing: border-box; }
html, body {
    background: #0a0c10 !important;
    color: #d1d4dc !important;
    font-family: 'Inter', -apple-system, sans-serif !important;
}
[data-testid="stAppViewContainer"] { background: #0a0c10 !important; }
[data-testid="stHeader"] { background: transparent !important; display: none; }
[data-testid="stToolbar"] { display: none; }
.block-container { padding: 0 !important; max-width: 100% !important; }
section[data-testid="stSidebar"] { background: #0d1117 !important; border-right: 1px solid #1e2736 !important; }
section[data-testid="stSidebar"] * { color: #c9d1d9 !important; }
[data-testid="stSidebar"] .stButton > button { width: 100%; }

/* ── Hide Streamlit chrome ── */
footer { display: none !important; }
#MainMenu { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }

/* ── Header bar ── */
.at-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #0d1117;
    border-bottom: 1px solid #1e2736;
    padding: 0 24px;
    height: 52px;
    position: sticky;
    top: 0;
    z-index: 100;
}
.at-logo {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 18px;
    font-weight: 700;
    color: #f0f6fc;
    letter-spacing: -0.3px;
}
.at-logo-accent { color: #2962ff; }
.at-ticker {
    display: flex;
    align-items: center;
    gap: 20px;
    font-family: 'JetBrains Mono', monospace;
}
.at-price {
    font-size: 22px;
    font-weight: 600;
    color: #f0f6fc;
}
.at-change-up   { font-size: 13px; color: #26a69a; font-weight: 500; }
.at-change-down { font-size: 13px; color: #ef5350; font-weight: 500; }
.at-status-row {
    display: flex;
    align-items: center;
    gap: 14px;
}
.pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    border: 1px solid;
}
.pill-green  { background: rgba(38,166,154,0.1); border-color: #26a69a; color: #26a69a; }
.pill-gray   { background: rgba(110,118,129,0.15); border-color: #6e7681; color: #6e7681; }
.pill-blue   { background: rgba(41,98,255,0.15);  border-color: #2962ff; color: #79b0ff; }
.pill-red    { background: rgba(239,83,80,0.12);  border-color: #ef5350; color: #ef5350; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot-green { background: #26a69a; animation: blink 2s infinite; }
.dot-gray  { background: #6e7681; }
.dot-red   { background: #ef5350; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* ── Main layout ── */
.at-body {
    display: flex;
    height: calc(100vh - 52px);
    overflow: hidden;
}
.at-left {
    width: 240px;
    min-width: 240px;
    background: #0d1117;
    border-right: 1px solid #1e2736;
    overflow-y: auto;
    padding: 16px 12px;
    flex-shrink: 0;
}
.at-main {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
    background: #0a0c10;
}

/* ── Section labels ── */
.at-section {
    font-size: 10px;
    font-weight: 600;
    color: #6e7681;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin: 16px 0 8px 4px;
}
.at-section:first-child { margin-top: 4px; }

/* ── Metric cards ── */
.cards-row {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin-bottom: 14px;
}
.card {
    background: #0d1117;
    border: 1px solid #1e2736;
    border-radius: 8px;
    padding: 14px 16px;
    transition: border-color 0.2s;
}
.card:hover { border-color: #2962ff44; }
.card-label {
    font-size: 10px;
    color: #6e7681;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 8px;
    font-weight: 500;
}
.card-value {
    font-size: 20px;
    font-weight: 700;
    color: #f0f6fc;
    font-family: 'JetBrains Mono', monospace;
    line-height: 1;
}
.card-value.up   { color: #26a69a; }
.card-value.down { color: #ef5350; }
.card-sub {
    font-size: 10px;
    color: #8b949e;
    margin-top: 6px;
}

/* ── Chart container ── */
.chart-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}
.chart-title {
    font-size: 13px;
    font-weight: 600;
    color: #8b949e;
    display: flex;
    align-items: center;
    gap: 8px;
}
.chart-badge {
    background: #1e2736;
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 11px;
    color: #79b0ff;
    font-family: 'JetBrains Mono', monospace;
}

/* ── Tabs (custom) ── */
.at-tabs {
    display: flex;
    gap: 2px;
    border-bottom: 1px solid #1e2736;
    margin-bottom: 12px;
}
.at-tab {
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
    color: #6e7681;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
}
.at-tab.active {
    color: #f0f6fc;
    border-bottom-color: #2962ff;
}

/* ── Trade table ── */
.trade-row {
    display: grid;
    grid-template-columns: 60px 80px 70px 70px 90px 90px 90px 80px 80px 1fr;
    gap: 0;
    padding: 8px 12px;
    border-bottom: 1px solid #1e2736;
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
    align-items: center;
}
.trade-row:hover { background: #0d1117; }
.trade-header {
    background: #0d1117;
    border: 1px solid #1e2736;
    border-radius: 6px 6px 0 0;
    color: #6e7681;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}

/* ── Activity log ── */
.log-container {
    background: #0d1117;
    border: 1px solid #1e2736;
    border-radius: 6px;
    height: 320px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11.5px;
    padding: 8px 0;
}
.log-line {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 4px 14px;
    border-bottom: 1px solid #1e273610;
    line-height: 1.5;
}
.log-line:hover { background: #1e273630; }
.log-ts    { color: #484f58; min-width: 78px; flex-shrink: 0; }
.log-level { min-width: 52px; flex-shrink: 0; font-weight: 600; font-size: 10px; }
.log-msg   { flex: 1; }
.lv-INFO    { color: #6e7681; }
.lv-SIGNAL  { color: #79b0ff; }
.lv-ORDER   { color: #26a69a; }
.lv-WARNING { color: #e3b341; }
.lv-ERROR   { color: #ef5350; }

/* ── Open positions table ── */
.pos-card {
    background: #0d1117;
    border: 1px solid #2962ff33;
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
}
.pos-buy  { border-left: 3px solid #26a69a; }
.pos-sell { border-left: 3px solid #ef5350; }

/* ── Control group (sidebar) ── */
.ctrl-divider {
    border: none;
    border-top: 1px solid #1e2736;
    margin: 12px 0;
}

/* ── Buttons ── */
.stButton > button {
    border-radius: 6px !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    transition: all 0.15s !important;
    letter-spacing: 0.01em !important;
}
.stButton > button:hover { opacity: 0.85 !important; }

/* ── Form inputs ── */
.stTextInput input, .stNumberInput input, .stSelectbox select {
    background: #1c2128 !important;
    border-color: #30363d !important;
    color: #f0f6fc !important;
    border-radius: 6px !important;
}
.stSlider > div > div { background: #1e2736 !important; }
[data-testid="stSlider"] [role="slider"] { background: #2962ff !important; }

/* ── Scrollbars ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }

/* ── Responsive ── */
@media (max-width: 768px) {
    .cards-row { grid-template-columns: repeat(2, 1fr) !important; }
    .at-price { font-size: 16px !important; }
}
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
def _init():
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
        "active_tab": "trades",
        "api_key_input": "",
        "api_secret_input": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

SYMBOLS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
           "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT"]
INTERVALS = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}
STRATEGIES = ["EMA Crossover","Price Movement","Momentum (RSI)"]


# ── Helpers ───────────────────────────────────────────────────────────────────
def client():
    return st.session_state.get("client")

def _pnl_class(v):
    if v is None: return ""
    return "up" if v >= 0 else "down"

def _fmt_price(v, dec=4):
    if v is None: return "—"
    return f"${v:,.{dec}f}"

def _fmt_pnl(v):
    if v is None: return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:.4f}"

def _fmt_pct(v):
    if v is None: return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


# ── Live data fetch ───────────────────────────────────────────────────────────
live_price = None
price_change_pct = 0.0
df_chart = None

if st.session_state.connected and client():
    try:
        live_price = client().get_symbol_price(st.session_state.symbol)
    except Exception:
        pass
    try:
        df_raw = client().get_klines(st.session_state.symbol, st.session_state.interval, limit=200)
        df_chart = get_indicators(df_raw)
        if df_chart is not None and len(df_chart) > 1:
            prev_c = df_chart["close"].iloc[-2]
            curr_c = df_chart["close"].iloc[-1]
            price_change_pct = (curr_c - prev_c) / prev_c * 100
    except Exception:
        pass


# ── Metrics computation ───────────────────────────────────────────────────────
all_trades   = load_trades()
open_trades  = get_open_trades()
closed_trades = [t for t in all_trades if t.get("status") == "closed"]
total_pnl    = sum((t.get("profit_loss") or 0) for t in closed_trades)
today_str    = datetime.now().strftime("%Y-%m-%d")
daily_pnl    = sum(
    (t.get("profit_loss") or 0) for t in closed_trades
    if (t.get("close_time") or "").startswith(today_str)
)
wins = sum(1 for t in closed_trades if (t.get("profit_loss") or 0) >= 0)
win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

if not st.session_state.paper_mode and st.session_state.connected and client():
    try:
        balance = client().get_account_balance("USDT")
    except Exception:
        balance = st.session_state.initial_balance + total_pnl
else:
    balance = st.session_state.initial_balance + total_pnl

equity = st.session_state.initial_balance + total_pnl
roi    = (total_pnl / st.session_state.initial_balance * 100) if st.session_state.initial_balance else 0.0


# ── Bot state ─────────────────────────────────────────────────────────────────
bot_inst    = bot_module.get_bot()
bot_running = bot_inst.is_running() if bot_inst else False


# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────
sym_label = st.session_state.symbol
price_str = f"${live_price:,.2f}" if live_price else "—"
chg_cls   = "at-change-up" if price_change_pct >= 0 else "at-change-down"
chg_str   = f"{'▲' if price_change_pct >= 0 else '▼'} {abs(price_change_pct):.2f}%"

conn_pill  = '<span class="pill pill-green"><span class="dot dot-green"></span>CONNECTED</span>' if st.session_state.connected else '<span class="pill pill-gray"><span class="dot dot-gray"></span>DISCONNECTED</span>'
bot_pill   = '<span class="pill pill-blue"><span class="dot dot-green"></span>BOT RUNNING</span>' if bot_running else '<span class="pill pill-gray">BOT IDLE</span>'
mode_pill  = '<span class="pill pill-gray">PAPER</span>' if st.session_state.paper_mode else '<span class="pill pill-red">⚠ LIVE</span>'
net_pill   = '<span class="pill pill-blue">TESTNET</span>' if st.session_state.testnet else '<span class="pill pill-red">MAINNET</span>'

st.markdown(f"""
<div class="at-header">
  <div class="at-logo">
    <span>⚡</span>
    <span>Alpha<span class="at-logo-accent">Trade</span></span>
  </div>
  <div class="at-ticker">
    <div>
      <span style="font-size:11px;color:#6e7681;margin-right:6px;">{sym_label}</span>
      <span class="at-price">{price_str}</span>
      <span class="{chg_cls}" style="margin-left:8px;">{chg_str}</span>
    </div>
  </div>
  <div class="at-status-row">
    {net_pill}
    {mode_pill}
    {conn_pill}
    {bot_pill}
  </div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚡ AlphaTrade")

    # ── Connection ────────────────────────────────────────────────────────────
    st.markdown('<div class="at-section">Connection</div>', unsafe_allow_html=True)
    testnet = st.toggle("Binance Testnet", value=st.session_state.testnet)
    st.session_state.testnet = testnet
    if not testnet:
        st.warning("⚠️ LIVE — real money!")

    api_key    = st.text_input("API Key",    type="password", placeholder="API key")
    api_secret = st.text_input("API Secret", type="password", placeholder="API secret")

    if st.button("🔌 Connect to Binance", use_container_width=True, type="primary"):
        if api_key and api_secret:
            with st.spinner("Connecting…"):
                try:
                    from binance_client import BinanceClient
                    c = BinanceClient(api_key, api_secret, testnet=testnet)
                    ok, msg = c.test_connection()
                    if ok:
                        st.session_state.client    = c
                        st.session_state.connected = True
                        log_activity("INFO", f"🔌 Connected to {'Testnet' if testnet else 'LIVE'} Binance")
                        st.success("Connected!")
                        st.rerun()
                    else:
                        st.error(msg)
                except Exception as e:
                    st.error(str(e))
        else:
            st.error("Enter API key and secret.")

    st.markdown('<hr class="ctrl-divider"/>', unsafe_allow_html=True)

    # ── Market ────────────────────────────────────────────────────────────────
    st.markdown('<div class="at-section">Market</div>', unsafe_allow_html=True)
    sym = st.selectbox("Symbol", SYMBOLS,
                        index=SYMBOLS.index(st.session_state.symbol) if st.session_state.symbol in SYMBOLS else 0)
    st.session_state.symbol = sym

    intv = st.selectbox("Interval", list(INTERVALS.keys()),
                         index=list(INTERVALS.keys()).index(st.session_state.interval)
                         if st.session_state.interval in INTERVALS else 2)
    st.session_state.interval = intv

    strat = st.selectbox("Strategy", STRATEGIES,
                          index=STRATEGIES.index(st.session_state.strategy)
                          if st.session_state.strategy in STRATEGIES else 0)
    st.session_state.strategy = strat

    st.markdown('<hr class="ctrl-divider"/>', unsafe_allow_html=True)

    # ── Bot controls ──────────────────────────────────────────────────────────
    st.markdown('<div class="at-section">Bot</div>', unsafe_allow_html=True)
    paper = st.toggle("Paper mode (no real orders)", value=st.session_state.paper_mode)
    st.session_state.paper_mode = paper

    ck = st.slider("Check every (s)", 10, 300, st.session_state.check_every, 10)
    st.session_state.check_every = ck

    thr = st.slider("Price threshold %", 0.01, 2.0, st.session_state.threshold, 0.01)
    st.session_state.threshold = thr

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("▶ Start", use_container_width=True,
                     disabled=bot_running or not st.session_state.connected):
            c = client()
            if c:
                b = bot_module.create_bot(
                    client=c,
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
    with bc2:
        if st.button("⏹ Stop", use_container_width=True, disabled=not bot_running):
            bot_module.stop_bot()
            st.rerun()

    if st.button("🚨 Emergency Stop", use_container_width=True, type="secondary"):
        st.session_state.risk.emergency_stop = True
        bot_module.stop_bot()
        log_activity("WARNING", "🚨 EMERGENCY STOP — all trading halted immediately")
        st.rerun()

    if st.session_state.risk.emergency_stop:
        st.error("🚨 Emergency stop ACTIVE")
        if st.button("✅ Clear Emergency Stop"):
            st.session_state.risk.emergency_stop = False
            log_activity("INFO", "✅ Emergency stop cleared")
            st.rerun()

    st.markdown('<hr class="ctrl-divider"/>', unsafe_allow_html=True)

    # ── Investment ────────────────────────────────────────────────────────────
    st.markdown('<div class="at-section">Investment</div>', unsafe_allow_html=True)
    if paper:
        init_bal = st.number_input("Simulated balance (USDT)", 100.0, 1_000_000.0,
                                   st.session_state.initial_balance, 100.0)
        st.session_state.initial_balance = init_bal

    manual_amt = st.number_input("Manual order amount (USDT)", 10.0, 100_000.0,
                                  st.session_state.manual_amount, 10.0)
    st.session_state.manual_amount = manual_amt

    st.markdown('<hr class="ctrl-divider"/>', unsafe_allow_html=True)

    # ── Risk ──────────────────────────────────────────────────────────────────
    st.markdown('<div class="at-section">Risk Management</div>', unsafe_allow_html=True)
    r = st.session_state.risk
    r.risk_per_trade_pct = st.slider("Risk per trade %",    0.5, 20.0, r.risk_per_trade_pct, 0.5)
    r.stop_loss_pct      = st.slider("Stop loss %",         0.5, 20.0, r.stop_loss_pct,      0.5)
    r.take_profit_pct    = st.slider("Take profit %",       0.5, 50.0, r.take_profit_pct,    0.5)
    r.max_daily_loss_pct = st.slider("Max daily loss %",    1.0, 30.0, r.max_daily_loss_pct, 0.5)
    r.max_open_trades    = st.slider("Max open trades",     1,   20,   r.max_open_trades,    1)
    st.session_state.risk_manager.settings = r

    st.markdown('<hr class="ctrl-divider"/>', unsafe_allow_html=True)

    # ── Data ──────────────────────────────────────────────────────────────────
    st.markdown('<div class="at-section">Data</div>', unsafe_allow_html=True)
    auto_refresh = st.checkbox("Auto-refresh (30s)")
    if st.button("🗑️ Reset All Data", use_container_width=True):
        reset_all_data()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CONTENT
# ─────────────────────────────────────────────────────────────────────────────
main = st.container()
with main:
    pad_l, center, pad_r = st.columns([0.01, 99.98, 0.01])
    with center:

        # ── Fund Overview ─────────────────────────────────────────────────────
        roi_cls   = "up"   if roi >= 0   else "down"
        dpnl_cls  = "up"   if daily_pnl >= 0 else "down"
        tpnl_cls  = "up"   if total_pnl >= 0 else "down"

        st.markdown(f"""
<div class="cards-row">
  <div class="card">
    <div class="card-label">Total Equity</div>
    <div class="card-value">${equity:,.2f}</div>
    <div class="card-sub">Initial: ${st.session_state.initial_balance:,.0f}</div>
  </div>
  <div class="card">
    <div class="card-label">Balance</div>
    <div class="card-value">${balance:,.2f}</div>
    <div class="card-sub">{'Simulated' if st.session_state.paper_mode else 'Live USDT'}</div>
  </div>
  <div class="card">
    <div class="card-label">ROI</div>
    <div class="card-value {roi_cls}">{roi:+.2f}%</div>
    <div class="card-sub">Total P&L: {_fmt_pnl(total_pnl)}</div>
  </div>
  <div class="card">
    <div class="card-label">Open Positions</div>
    <div class="card-value">{len(open_trades)}</div>
    <div class="card-sub">Win rate: {win_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="card-label">Daily P&L</div>
    <div class="card-value {dpnl_cls}">{_fmt_pnl(daily_pnl)}</div>
    <div class="card-sub">{len([t for t in closed_trades if (t.get('close_time') or '').startswith(today_str)])} trades today</div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── Manual trading + chart header row ─────────────────────────────────
        hdr_c1, hdr_c2, hdr_c3, hdr_c4, hdr_c5 = st.columns([3, 1, 1, 1, 1])
        with hdr_c1:
            interval_display = st.session_state.interval
            strategy_display = st.session_state.strategy
            st.markdown(f"""
<div class="chart-header">
  <div class="chart-title">
    <span style="color:#f0f6fc;font-weight:700;">{st.session_state.symbol}</span>
    <span class="chart-badge">{interval_display}</span>
    <span class="chart-badge" style="color:#e3b341;">{strategy_display}</span>
    {'<span class="chart-badge" style="background:#238636;color:#3fb950;">📋 PAPER</span>' if st.session_state.paper_mode else '<span class="chart-badge" style="background:#6e0001;color:#ef5350;">⚡ LIVE</span>'}
  </div>
</div>
""", unsafe_allow_html=True)
        with hdr_c2:
            buy_btn = st.button("▲ BUY", use_container_width=True,
                                disabled=not st.session_state.connected,
                                help=f"Market buy {st.session_state.symbol}")
        with hdr_c3:
            sell_btn = st.button("▼ SELL", use_container_width=True,
                                 disabled=not st.session_state.connected,
                                 help=f"Market sell {st.session_state.symbol}")
        with hdr_c4:
            emg_btn = st.button("🚨 STOP", use_container_width=True, type="secondary")
        with hdr_c5:
            refresh_btn = st.button("↺ Refresh", use_container_width=True)

        if buy_btn or sell_btn:
            side = "BUY" if buy_btn else "SELL"
            c = client()
            if c:
                try:
                    price = c.get_symbol_price(st.session_state.symbol)
                    invested = st.session_state.manual_amount
                    qty = c.round_quantity(st.session_state.symbol, invested / price)
                    if not st.session_state.paper_mode:
                        order = c.place_market_order(st.session_state.symbol, side, qty)
                        price = float(order.get("fills", [{}])[0].get("price", price))
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
                        "reason": f"Manual {side} — {st.session_state.manual_amount} USDT",
                        "close_reason": None,
                        "stop_loss": rm.stop_loss_price(price, side),
                        "take_profit": rm.take_profit_price(price, side),
                        "status": "open",
                        "paper": st.session_state.paper_mode,
                    }
                    added = add_trade(trade)
                    log_activity("ORDER", f"👤 Manual {side} | {qty:.6f} {st.session_state.symbol} @ ${price:.4f} | ID: {added['id']}")
                    st.success(f"✅ {side} @ ${price:.4f} | ID: {added['id']}")
                except Exception as e:
                    st.error(str(e))

        if emg_btn:
            st.session_state.risk.emergency_stop = True
            bot_module.stop_bot()
            log_activity("WARNING", "🚨 EMERGENCY STOP activated")
            st.rerun()

        if refresh_btn:
            st.rerun()

        # ── Chart ─────────────────────────────────────────────────────────────
        if df_chart is not None and len(df_chart) > 0:
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                row_heights=[0.72, 0.28],
                vertical_spacing=0.02,
            )

            # Candlestick
            fig.add_trace(go.Candlestick(
                x=df_chart["open_time"],
                open=df_chart["open"],
                high=df_chart["high"],
                low=df_chart["low"],
                close=df_chart["close"],
                name=st.session_state.symbol,
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
                increasing_fillcolor="#26a69a",
                decreasing_fillcolor="#ef5350",
                line=dict(width=1),
                whiskerwidth=0,
            ), row=1, col=1)

            # EMA 9
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["ema9"],
                line=dict(color="#ef5350", width=1.5),
                name="EMA 9",
                hovertemplate="%{y:.4f}",
            ), row=1, col=1)

            # EMA 21
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["ema21"],
                line=dict(color="#e3b341", width=1.5),
                name="EMA 21",
                hovertemplate="%{y:.4f}",
            ), row=1, col=1)

            # Current price line
            if live_price:
                fig.add_hline(
                    y=live_price,
                    line=dict(color="#2962ff", width=1, dash="dot"),
                    row=1, col=1,
                    annotation_text=f"  ${live_price:,.4f}",
                    annotation_position="right",
                    annotation_font_color="#2962ff",
                    annotation_font_size=10,
                )

            # ── Trade markers ──
            m_buy_x, m_buy_y   = [], []
            m_sell_x, m_sell_y = [], []
            b_buy_x, b_buy_y   = [], []
            b_sell_x, b_sell_y = [], []
            m_close_x, m_close_y = [], []
            b_close_x, b_close_y = [], []

            for t in all_trades:
                try:
                    ts    = pd.to_datetime(t.get("open_time"))
                    ep    = t.get("entry_price")
                    ttype = t.get("type", "manual")
                    side  = t.get("side", "BUY")
                    if ep is None:
                        continue
                    if ttype == "manual":
                        (m_buy_x if side == "BUY" else m_sell_x).append(ts)
                        (m_buy_y if side == "BUY" else m_sell_y).append(ep)
                    else:
                        (b_buy_x if side == "BUY" else b_sell_x).append(ts)
                        (b_buy_y if side == "BUY" else b_sell_y).append(ep)

                    if t.get("exit_price") and t.get("close_time"):
                        ts_ex = pd.to_datetime(t["close_time"])
                        xp    = t["exit_price"]
                        if ttype == "manual":
                            m_close_x.append(ts_ex); m_close_y.append(xp)
                        else:
                            b_close_x.append(ts_ex); b_close_y.append(xp)
                except Exception:
                    continue

            marker_traces = [
                (m_buy_x,  m_buy_y,   "triangle-up",   "#79b0ff", 13, "Manual BUY"),
                (m_sell_x, m_sell_y,  "triangle-down", "#d2a8ff", 13, "Manual SELL"),
                (b_buy_x,  b_buy_y,   "triangle-up",   "#26a69a", 11, "Bot BUY"),
                (b_sell_x, b_sell_y,  "triangle-down", "#ef5350", 11, "Bot SELL"),
                (m_close_x,m_close_y, "x",             "#79b0ff", 10, "Manual Exit"),
                (b_close_x,b_close_y, "x",             "#e3b341", 10, "Bot Exit"),
            ]
            for mx, my, sym_m, col, sz, lbl in marker_traces:
                if mx:
                    fig.add_trace(go.Scatter(
                        x=mx, y=my, mode="markers",
                        marker=dict(symbol=sym_m, size=sz, color=col,
                                    line=dict(color="rgba(255,255,255,0.6)", width=1)),
                        name=lbl,
                        hovertemplate=f"{lbl}: %{{y:.4f}}<extra></extra>",
                    ), row=1, col=1)

            # ── Stochastic ──
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["stoch_k"],
                line=dict(color="#79b0ff", width=1.5),
                name="%K",
            ), row=2, col=1)
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["stoch_d"],
                line=dict(color="#c9d1d9", width=1.2, dash="dot"),
                name="%D",
            ), row=2, col=1)
            fig.add_hline(y=80, line=dict(color="#ef535055", width=1, dash="dash"), row=2, col=1)
            fig.add_hline(y=20, line=dict(color="#26a69a55", width=1, dash="dash"), row=2, col=1)
            fig.add_hrect(y0=80, y1=100, fillcolor="#ef535008", line_width=0, row=2, col=1)
            fig.add_hrect(y0=0,  y1=20,  fillcolor="#26a69a08", line_width=0, row=2, col=1)

            GRID = "#1e2736"
            BG   = "#0a0c10"
            fig.update_layout(
                paper_bgcolor=BG,
                plot_bgcolor=BG,
                font=dict(color="#8b949e", family="'JetBrains Mono',monospace", size=10),
                xaxis_rangeslider_visible=False,
                height=580,
                margin=dict(l=0, r=56, t=8, b=0),
                showlegend=True,
                legend=dict(
                    bgcolor="rgba(13,17,23,0.85)",
                    bordercolor=GRID,
                    borderwidth=1,
                    font=dict(size=10),
                    orientation="v",
                    yanchor="top", y=0.99,
                    xanchor="right", x=0.99,
                ),
                hovermode="x unified",
                hoverlabel=dict(
                    bgcolor="#161b22",
                    bordercolor="#30363d",
                    font_color="#c9d1d9",
                    font_size=11,
                ),
            )
            fig.update_xaxes(
                gridcolor=GRID, gridwidth=1,
                zerolinecolor=GRID,
                showspikes=True, spikecolor="#484f58", spikewidth=1, spikesnap="cursor",
                tickfont=dict(size=10),
            )
            fig.update_yaxes(
                gridcolor=GRID, gridwidth=1,
                zerolinecolor=GRID,
                showspikes=True, spikecolor="#484f58",
                tickfont=dict(size=10),
                tickprefix="$",
                row=1, col=1,
            )
            fig.update_yaxes(
                gridcolor=GRID, gridwidth=1,
                range=[0, 100],
                ticksuffix="",
                row=2, col=1,
            )
            st.plotly_chart(fig, use_container_width=True, config={
                "displayModeBar": True,
                "displaylogo": False,
                "modeBarButtonsToRemove": ["select2d","lasso2d","toImage"],
            })
        else:
            st.markdown("""
<div style="background:#0d1117;border:1px solid #1e2736;border-radius:8px;height:400px;
display:flex;align-items:center;justify-content:center;color:#484f58;flex-direction:column;gap:12px;">
  <span style="font-size:32px;">📊</span>
  <span style="font-size:14px;">Connect to Binance to load the chart</span>
</div>
""", unsafe_allow_html=True)

        # ── Open Positions ────────────────────────────────────────────────────
        if open_trades:
            st.markdown("""
<div style="font-size:11px;font-weight:600;color:#6e7681;text-transform:uppercase;
letter-spacing:0.12em;margin:20px 0 8px 0;">Open Positions</div>
""", unsafe_allow_html=True)

            for ot in open_trades:
                ep     = ot.get("entry_price", 0)
                sl     = ot.get("stop_loss") or st.session_state.risk_manager.stop_loss_price(ep, ot.get("side","BUY"))
                tp     = ot.get("take_profit") or st.session_state.risk_manager.take_profit_price(ep, ot.get("side","BUY"))
                side_c = "pos-buy" if ot.get("side") == "BUY" else "pos-sell"
                unreal_pnl = ""
                if live_price and ep:
                    side_dir = ot.get("side","BUY")
                    invested = ot.get("invested", 0) or 0
                    if side_dir == "BUY":
                        upnl = (live_price - ep) / ep * invested
                    else:
                        upnl = (ep - live_price) / ep * invested
                    color = "#26a69a" if upnl >= 0 else "#ef5350"
                    unreal_pnl = f'<span style="color:{color};font-weight:700;">{_fmt_pnl(upnl)}</span>'

                pos_col1, pos_col2 = st.columns([8, 2])
                with pos_col1:
                    st.markdown(f"""
<div class="pos-card {side_c}">
  <div>
    <span style="color:#f0f6fc;font-weight:700;">{ot.get('coin','?')}</span>
    <span style="color:#6e7681;margin:0 8px;">{'🤖 Bot' if ot.get('type')=='bot' else '👤 Manual'}</span>
    <span style="color:#{'26a69a' if ot.get('side')=='BUY' else 'ef5350'};font-weight:600;">{ot.get('side','?')}</span>
    <span style="color:#8b949e;margin:0 8px;">ID: {ot.get('id','?')}</span>
  </div>
  <div style="display:flex;gap:20px;align-items:center;">
    <div><span style="color:#6e7681;font-size:10px;">ENTRY</span><br/>${ep:.4f}</div>
    <div><span style="color:#6e7681;font-size:10px;">SL</span><br/><span style="color:#ef5350;">${sl:.4f}</span></div>
    <div><span style="color:#6e7681;font-size:10px;">TP</span><br/><span style="color:#26a69a;">${tp:.4f}</span></div>
    <div><span style="color:#6e7681;font-size:10px;">UNREAL. P&L</span><br/>{unreal_pnl}</div>
  </div>
</div>
""", unsafe_allow_html=True)
                with pos_col2:
                    if st.button(f"Close {ot.get('id','?')}", key=f"pos_{ot.get('id')}",
                                 use_container_width=True):
                        c = client()
                        ex_p = c.get_symbol_price(ot["coin"]) if c else ep
                        close_trade(ot["id"], ex_p, "Manual close via dashboard")
                        log_activity("ORDER", f"👤 Closed trade {ot['id']} @ ${ex_p:.4f}")
                        st.rerun()

        # ── Bottom tabs ───────────────────────────────────────────────────────
        st.markdown("""
<div style="margin-top:24px;border-bottom:1px solid #1e2736;padding-bottom:0;">
  <span style="font-size:13px;font-weight:600;color:#f0f6fc;padding-bottom:8px;
  display:inline-block;border-bottom:2px solid #2962ff;margin-right:24px;">
    Trade History
  </span>
</div>
""", unsafe_allow_html=True)

        # ── Trade History ─────────────────────────────────────────────────────
        tab1, tab2 = st.tabs(["📋  Trade History", "📟  Activity Log"])

        with tab1:
            if not all_trades:
                st.markdown("""
<div style="background:#0d1117;border:1px solid #1e2736;border-radius:6px;
padding:20px;color:#6e7681;text-align:center;">No trades yet.</div>
""", unsafe_allow_html=True)
            else:
                rows = []
                for t in reversed(all_trades):
                    pnl = t.get("profit_loss")
                    pct = t.get("profit_loss_pct")
                    rows.append({
                        "ID":        t.get("id", "—"),
                        "Coin":      t.get("coin", "—"),
                        "Type":      "🤖 Bot" if t.get("type") == "bot" else "👤 Manual",
                        "Side":      t.get("side", "—"),
                        "Strategy":  t.get("strategy", "—"),
                        "Entry":     f"${t.get('entry_price', 0):.4f}",
                        "Exit":      f"${t.get('exit_price', 0):.4f}" if t.get("exit_price") else "open",
                        "Invested":  f"${t.get('invested', 0):.2f}",
                        "P&L $":     _fmt_pnl(pnl),
                        "P&L %":     _fmt_pct(pct),
                        "Status":    t.get("status", "—"),
                        "Opened":    (t.get("open_time") or "")[:16].replace("T", " "),
                        "Closed":    (t.get("close_time") or "")[:16].replace("T", " ") if t.get("close_time") else "—",
                        "Reason":    (t.get("reason") or "")[:80],
                    })
                df_t = pd.DataFrame(rows)
                st.dataframe(
                    df_t,
                    use_container_width=True,
                    hide_index=True,
                    height=300,
                    column_config={
                        "Reason": st.column_config.TextColumn("Reason", width="large"),
                        "P&L $":  st.column_config.TextColumn("P&L $"),
                        "P&L %":  st.column_config.TextColumn("P&L %"),
                    },
                )

        # ── Activity Log ──────────────────────────────────────────────────────
        with tab2:
            act_col1, act_col2 = st.columns([6, 1])
            with act_col2:
                if st.button("🗑 Clear", use_container_width=True):
                    clear_activity()
                    st.rerun()

            activity = load_activity()
            if not activity:
                st.markdown("""
<div style="background:#0d1117;border:1px solid #1e2736;border-radius:6px;
padding:20px;color:#6e7681;text-align:center;">No activity yet.</div>
""", unsafe_allow_html=True)
            else:
                lines = []
                for entry in reversed(activity[-300:]):
                    ts  = (entry.get("time") or "")[:19].replace("T", " ")
                    lvl = entry.get("level", "INFO")
                    msg = entry.get("message", "").replace("<", "&lt;").replace(">", "&gt;")
                    lines.append(
                        f'<div class="log-line">'
                        f'<span class="log-ts">{ts}</span>'
                        f'<span class="log-level lv-{lvl}">[{lvl}]</span>'
                        f'<span class="log-msg lv-{lvl}">{msg}</span>'
                        f'</div>'
                    )
                st.markdown(
                    '<div class="log-container">' + "".join(lines) + "</div>",
                    unsafe_allow_html=True,
                )

        # ── Stats footer ──────────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        fs1, fs2, fs3, fs4 = st.columns(4)
        with fs1:
            st.markdown(f"""
<div class="card" style="text-align:center;">
  <div class="card-label">Total Trades</div>
  <div class="card-value">{len(all_trades)}</div>
</div>""", unsafe_allow_html=True)
        with fs2:
            st.markdown(f"""
<div class="card" style="text-align:center;">
  <div class="card-label">Closed Trades</div>
  <div class="card-value">{len(closed_trades)}</div>
</div>""", unsafe_allow_html=True)
        with fs3:
            st.markdown(f"""
<div class="card" style="text-align:center;">
  <div class="card-label">Win Rate</div>
  <div class="card-value {'up' if win_rate >= 50 else 'down'}">{win_rate:.1f}%</div>
</div>""", unsafe_allow_html=True)
        with fs4:
            st.markdown(f"""
<div class="card" style="text-align:center;">
  <div class="card-label">Total P&L</div>
  <div class="card-value {tpnl_cls}">{_fmt_pnl(total_pnl)}</div>
</div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:40px'></div>", unsafe_allow_html=True)


# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(30)
    st.rerun()
