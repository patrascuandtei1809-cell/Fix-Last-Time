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
from binance_client import public_klines, public_price, public_24h

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

* { box-sizing: border-box; }
html, body {
    background: #0a0c10 !important;
    color: #d1d4dc !important;
    font-family: 'Inter', -apple-system, sans-serif !important;
}
[data-testid="stAppViewContainer"] { background: #0a0c10 !important; }
[data-testid="stHeader"]           { display: none !important; }
[data-testid="stToolbar"]          { display: none !important; }
[data-testid="stDecoration"]       { display: none !important; }
footer                             { display: none !important; }
#MainMenu                          { display: none !important; }
.block-container { padding: 0 !important; max-width: 100% !important; }

section[data-testid="stSidebar"] {
    background: #0d1117 !important;
    border-right: 1px solid #1e2736 !important;
}
section[data-testid="stSidebar"] * { color: #c9d1d9 !important; }

/* ── Header ── */
.at-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    background: #0d1117;
    border-bottom: 1px solid #1e2736;
    padding: 0 20px;
    height: 54px;
}
.at-logo {
    font-size: 18px; font-weight: 700;
    color: #f0f6fc; letter-spacing: -0.3px;
    display: flex; align-items: center; gap: 8px;
}
.at-logo span.acc { color: #2962ff; }

.at-ticker-wrap {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}
.at-sym   { font-size:12px; color:#6e7681; font-weight:600; letter-spacing:.08em; }
.at-price { font-size:22px; font-weight:700; color:#f0f6fc; font-family:'JetBrains Mono',monospace; }
.at-chg   { font-size:12px; font-weight:600; padding:2px 7px; border-radius:4px; }
.chg-up   { background:rgba(38,166,154,.15); color:#26a69a; }
.chg-dn   { background:rgba(239,83,80,.15);  color:#ef5350; }
.at-stat  { font-size:11px; color:#6e7681; }

.pills { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.pill {
    display:inline-flex; align-items:center; gap:5px;
    padding:3px 10px; border-radius:20px;
    font-size:11px; font-weight:600; border:1px solid; white-space:nowrap;
}
.p-green { background:rgba(38,166,154,.1); border-color:#26a69a44; color:#26a69a; }
.p-gray  { background:rgba(110,118,129,.1); border-color:#6e768144; color:#8b949e; }
.p-blue  { background:rgba(41,98,255,.12);  border-color:#2962ff44; color:#79b0ff; }
.p-red   { background:rgba(239,83,80,.1);   border-color:#ef535044; color:#ef5350; }
.p-gold  { background:rgba(227,179,65,.1);  border-color:#e3b34144; color:#e3b341; }
.dot { width:6px;height:6px;border-radius:50%;display:inline-block; }
.dot-g { background:#26a69a; animation:blink 2s infinite; }
.dot-r { background:#ef5350; }
.dot-y { background:#e3b341; animation:blink 1.5s infinite; }
.dot-x { background:#6e7681; }
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}

/* ── Cards ── */
.cards {
    display:grid;
    grid-template-columns:repeat(5,1fr);
    gap:10px; margin-bottom:14px;
}
@media(max-width:900px){ .cards{grid-template-columns:repeat(2,1fr);} }
.card {
    background:#0d1117; border:1px solid #1e2736;
    border-radius:8px; padding:14px 16px;
    transition:border-color .18s;
}
.card:hover { border-color:#2962ff44; }
.c-lbl {
    font-size:10px; color:#6e7681; text-transform:uppercase;
    letter-spacing:.12em; margin-bottom:8px; font-weight:500;
}
.c-val {
    font-size:20px; font-weight:700; color:#f0f6fc;
    font-family:'JetBrains Mono',monospace; line-height:1;
}
.c-val.up  { color:#26a69a; }
.c-val.dn  { color:#ef5350; }
.c-sub { font-size:10px; color:#484f58; margin-top:5px; }

/* ── Section label ── */
.sec-lbl {
    font-size:10px; font-weight:600; color:#6e7681;
    text-transform:uppercase; letter-spacing:.12em;
    margin:18px 0 8px; padding-bottom:6px;
    border-bottom:1px solid #1e2736;
}

/* ── Chart header ── */
.chart-bar {
    display:flex; align-items:center; justify-content:space-between;
    margin-bottom:6px; flex-wrap:wrap; gap:6px;
}
.chart-title {
    display:flex; align-items:center; gap:8px;
    font-size:13px; font-weight:700; color:#f0f6fc;
}
.cbadge {
    background:#1e2736; border-radius:4px;
    padding:2px 7px; font-size:11px; font-family:'JetBrains Mono',monospace;
}
.cbadge.blue  { color:#79b0ff; }
.cbadge.gold  { color:#e3b341; }
.cbadge.green { background:#23422a; color:#3fb950; }
.cbadge.red   { background:#4a1010; color:#ef5350; }

/* ── Open positions ── */
.pos-card {
    display:flex; justify-content:space-between; align-items:center;
    background:#0d1117; border:1px solid #1e273680;
    border-radius:6px; padding:10px 14px; margin-bottom:8px;
    font-family:'JetBrains Mono',monospace; font-size:12px;
    flex-wrap:wrap; gap:8px;
}
.pos-buy  { border-left:3px solid #26a69a; }
.pos-sell { border-left:3px solid #ef5350; }

/* ── Activity log ── */
.log-wrap {
    background:#0d1117; border:1px solid #1e2736;
    border-radius:6px; height:340px; overflow-y:auto;
    font-family:'JetBrains Mono',monospace; font-size:11.5px;
    padding:4px 0;
}
.log-line {
    display:flex; gap:10px; align-items:flex-start;
    padding:4px 12px; border-bottom:1px solid #1e273618;
    line-height:1.55;
}
.log-line:hover { background:#1e273625; }
.l-ts  { color:#3d444d; min-width:75px; flex-shrink:0; }
.l-lvl { min-width:56px; font-size:10px; font-weight:700; flex-shrink:0; }
.l-msg { flex:1; }
.lINFO    { color:#6e7681; }
.lSIGNAL  { color:#79b0ff; }
.lORDER   { color:#26a69a; }
.lWARNING { color:#e3b341; }
.lERROR   { color:#ef5350; }

/* ── Sidebar controls ── */
.s-div { border:none; border-top:1px solid #1e2736; margin:10px 0; }
.stButton>button { border-radius:6px !important; font-weight:600 !important; font-size:13px !important; }
.stTextInput input,.stNumberInput input {
    background:#1c2128 !important; border-color:#30363d !important;
    color:#f0f6fc !important; border-radius:6px !important;
    font-family:'JetBrains Mono',monospace !important;
}
[data-testid="stSlider"] [role="slider"] { background:#2962ff !important; }

::-webkit-scrollbar { width:4px; height:4px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:#30363d; border-radius:2px; }
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
def _init():
    defaults = {
        "client":           None,
        "connected":        False,
        "paper_mode":       True,
        "symbol":           "BTCUSDT",
        "strategy":         "EMA Crossover",
        "interval":         "5m",
        "check_every":      30,
        "threshold":        0.30,
        "risk":             RiskSettings(),
        "risk_manager":     RiskManager(),
        "initial_balance":  1000.0,
        "manual_amount":    100.0,
        "testnet":          True,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

SYMBOLS    = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT"]
INTERVALS  = ["1m","3m","5m","15m","30m","1h","4h","1d"]
STRATEGIES = ["EMA Crossover","Price Movement","Momentum (RSI)"]


def _cl():
    return st.session_state.get("client")

def _fmt_p(v, d=4): return f"${v:,.{d}f}" if v is not None else "—"
def _fmt_pnl(v):
    if v is None: return "—"
    return f"+${v:.4f}" if v >= 0 else f"-${abs(v):.4f}"
def _fmt_pct(v):
    if v is None: return "—"
    return f"{v:+.2f}%"


# ─────────────────────────────────────────────────────────────────────────────
# LIVE MARKET DATA — public API (no key needed)
# ─────────────────────────────────────────────────────────────────────────────
sym       = st.session_state.symbol
testnet   = st.session_state.testnet
interval  = st.session_state.interval

live_price    = None
change_pct    = 0.0
high_24h      = None
low_24h       = None
df_chart      = None
chart_source  = ""

# Always try public API first (no auth needed)
try:
    stats       = public_24h(sym, testnet=False)   # always mainnet for public data
    live_price  = stats["price"]
    change_pct  = stats["change_pct"]
    high_24h    = stats["high"]
    low_24h     = stats["low"]
except Exception:
    pass

try:
    df_raw   = public_klines(sym, interval, limit=200, testnet=False)
    df_chart = get_indicators(df_raw)
    chart_source = "public"
except Exception:
    pass

# If authenticated, upgrade to authenticated data (testnet may have different prices)
if st.session_state.connected and _cl():
    try:
        live_price = _cl().get_symbol_price(sym)
    except Exception:
        pass
    try:
        df_raw2  = _cl().get_klines(sym, interval, limit=200)
        df_chart = get_indicators(df_raw2)
        chart_source = "auth"
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────
all_trades    = load_trades()
open_trades   = get_open_trades()
closed_trades = [t for t in all_trades if t.get("status") == "closed"]
total_pnl     = sum((t.get("profit_loss") or 0) for t in closed_trades)
today_str     = datetime.now().strftime("%Y-%m-%d")
daily_pnl     = sum(
    (t.get("profit_loss") or 0) for t in closed_trades
    if (t.get("close_time") or "").startswith(today_str)
)
wins     = sum(1 for t in closed_trades if (t.get("profit_loss") or 0) >= 0)
win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

balance = st.session_state.initial_balance + total_pnl
if not st.session_state.paper_mode and st.session_state.connected and _cl():
    try:
        balance = _cl().get_account_balance("USDT")
    except Exception:
        pass

equity = st.session_state.initial_balance + total_pnl
roi    = (total_pnl / st.session_state.initial_balance * 100) if st.session_state.initial_balance else 0.0

bot_inst    = bot_module.get_bot()
bot_running = bot_inst.is_running() if bot_inst else False


# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────
price_str = _fmt_p(live_price, 2) if live_price else "Loading…"
chg_cls   = "chg-up" if change_pct >= 0 else "chg-dn"
chg_str   = f"{'▲' if change_pct >= 0 else '▼'} {abs(change_pct):.2f}%"
h_str     = _fmt_p(high_24h, 2) if high_24h else "—"
l_str     = _fmt_p(low_24h, 2)  if low_24h  else "—"

conn_pill = ('<span class="pill p-green"><span class="dot dot-g"></span>CONNECTED</span>'
             if st.session_state.connected
             else '<span class="pill p-gray"><span class="dot dot-x"></span>NO AUTH</span>')
bot_pill  = ('<span class="pill p-blue"><span class="dot dot-y"></span>BOT ON</span>'
             if bot_running
             else '<span class="pill p-gray">BOT OFF</span>')
mode_pill = ('<span class="pill p-gray">📋 PAPER</span>'
             if st.session_state.paper_mode
             else '<span class="pill p-red"><span class="dot dot-r"></span>⚡ LIVE</span>')
net_pill  = ('<span class="pill p-blue">TESTNET</span>'
             if testnet
             else '<span class="pill p-red">MAINNET</span>')

st.markdown(f"""
<div class="at-header">
  <div class="at-logo">⚡ Alpha<span class="acc">Trade</span></div>

  <div class="at-ticker-wrap">
    <span class="at-sym">{sym}</span>
    <span class="at-price">{price_str}</span>
    <span class="at-chg {chg_cls}">{chg_str}</span>
    <span class="at-stat">H&nbsp;{h_str}&nbsp;&nbsp;L&nbsp;{l_str}</span>
  </div>

  <div class="pills">
    {net_pill}{mode_pill}{conn_pill}{bot_pill}
  </div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ AlphaTrade")

    # Connection
    st.markdown('<div class="sec-lbl">API Connection</div>', unsafe_allow_html=True)
    testnet_tog = st.toggle("Binance Testnet", value=st.session_state.testnet,
                             help="Use testnet.binance.vision for safe testing")
    st.session_state.testnet = testnet_tog
    if not testnet_tog:
        st.warning("⚠️ LIVE — real money at risk!")
    else:
        st.info("🧪 Testnet — get keys at testnet.binance.vision")

    api_key    = st.text_input("API Key",    type="password",
                                placeholder="Optional — needed for trading only")
    api_secret = st.text_input("API Secret", type="password",
                                placeholder="Optional — needed for trading only")

    if st.button("🔌 Connect", use_container_width=True, type="primary"):
        if api_key and api_secret:
            with st.spinner("Connecting…"):
                try:
                    from binance_client import BinanceClient
                    c   = BinanceClient(api_key, api_secret, testnet=testnet_tog)
                    ok, msg = c.test_connection()
                    if ok:
                        st.session_state.client    = c
                        st.session_state.connected = True
                        log_activity("INFO", f"🔌 Connected {'Testnet' if testnet_tog else 'LIVE'} — {msg}")
                        st.success("✅ Connected!")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
                except Exception as e:
                    st.error(f"Error: {e}")
        else:
            st.info("API keys are only needed for live trading & balance.\nChart works without them.")

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Market
    st.markdown('<div class="sec-lbl">Market</div>', unsafe_allow_html=True)
    sym_sel = st.selectbox("Symbol", SYMBOLS,
                            index=SYMBOLS.index(st.session_state.symbol)
                            if st.session_state.symbol in SYMBOLS else 0)
    st.session_state.symbol = sym_sel

    intv_sel = st.selectbox("Interval", INTERVALS,
                             index=INTERVALS.index(st.session_state.interval)
                             if st.session_state.interval in INTERVALS else 2)
    st.session_state.interval = intv_sel

    strat_sel = st.selectbox("Strategy", STRATEGIES,
                              index=STRATEGIES.index(st.session_state.strategy)
                              if st.session_state.strategy in STRATEGIES else 0)
    st.session_state.strategy = strat_sel

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Bot
    st.markdown('<div class="sec-lbl">Bot</div>', unsafe_allow_html=True)
    paper_tog = st.toggle("Paper mode (no real orders)", value=st.session_state.paper_mode)
    st.session_state.paper_mode = paper_tog

    ck_val = st.slider("Check interval (s)", 10, 300, st.session_state.check_every, 10)
    st.session_state.check_every = ck_val

    thr_val = st.slider("Price threshold %", 0.01, 2.0, st.session_state.threshold, 0.01,
                         help="Trigger % for Price Movement strategy")
    st.session_state.threshold = thr_val

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("▶ Start", use_container_width=True, disabled=bot_running):
            c = _cl()
            if c is None and not paper_tog:
                st.error("Connect to Binance first — live trading requires API keys.")
            else:
                # client=None is fine; bot falls back to public Binance API
                b = bot_module.create_bot(
                    client=c,
                    symbol=st.session_state.symbol,
                    strategy=st.session_state.strategy,
                    risk_manager=st.session_state.risk_manager,
                    interval=intv_sel,
                    check_every=ck_val,
                    paper_mode=True if c is None else paper_tog,
                    threshold=thr_val / 100,
                )
                b.start()
                if c is None:
                    st.info("🔓 Running in paper mode with public Binance data — no API key needed.")
                st.rerun()
    with bc2:
        if st.button("⏹ Stop", use_container_width=True, disabled=not bot_running):
            bot_module.stop_bot()
            st.rerun()

    if st.button("🚨 Emergency Stop", use_container_width=True, type="secondary"):
        st.session_state.risk.emergency_stop = True
        bot_module.stop_bot()
        log_activity("WARNING", "🚨 EMERGENCY STOP activated — all trading halted")
        st.rerun()

    if st.session_state.risk.emergency_stop:
        st.error("🚨 Emergency stop ACTIVE")
        if st.button("✅ Clear Emergency Stop", use_container_width=True):
            st.session_state.risk.emergency_stop = False
            log_activity("INFO", "✅ Emergency stop cleared — trading resumed")
            st.rerun()

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Investment
    st.markdown('<div class="sec-lbl">Investment</div>', unsafe_allow_html=True)
    if paper_tog:
        ib = st.number_input("Simulated balance (USDT)", 100.0, 1_000_000.0,
                              st.session_state.initial_balance, 100.0)
        st.session_state.initial_balance = ib

    ma = st.number_input("Manual order (USDT)", 10.0, 100_000.0,
                          st.session_state.manual_amount, 10.0)
    st.session_state.manual_amount = ma

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Risk
    st.markdown('<div class="sec-lbl">Risk Management</div>', unsafe_allow_html=True)
    r = st.session_state.risk
    r.risk_per_trade_pct = st.slider("Risk/trade %",    0.5, 20.0, r.risk_per_trade_pct, 0.5)
    r.stop_loss_pct      = st.slider("Stop loss %",     0.5, 20.0, r.stop_loss_pct,      0.5)
    r.take_profit_pct    = st.slider("Take profit %",   0.5, 50.0, r.take_profit_pct,    0.5)
    r.max_daily_loss_pct = st.slider("Max daily loss %",1.0, 30.0, r.max_daily_loss_pct, 0.5)
    r.max_open_trades    = st.slider("Max open trades", 1,   20,   r.max_open_trades,    1)
    st.session_state.risk_manager.settings = r

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Data
    st.markdown('<div class="sec-lbl">Data & Refresh</div>', unsafe_allow_html=True)
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if st.button("↺ Refresh Now", use_container_width=True):
        st.rerun()
    if st.button("🗑 Reset All Data", use_container_width=True):
        reset_all_data()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — fund cards
# ─────────────────────────────────────────────────────────────────────────────
with st.container():
    _, col_main, _ = st.columns([0.005, 99.99, 0.005])
    with col_main:

        roi_cls  = "up" if roi >= 0  else "dn"
        dpnl_cls = "up" if daily_pnl >= 0 else "dn"

        st.markdown(f"""
<div class="cards">
  <div class="card">
    <div class="c-lbl">Total Equity</div>
    <div class="c-val">${equity:,.2f}</div>
    <div class="c-sub">Initial ${st.session_state.initial_balance:,.0f}</div>
  </div>
  <div class="card">
    <div class="c-lbl">Balance</div>
    <div class="c-val">${balance:,.2f}</div>
    <div class="c-sub">{'Simulated' if st.session_state.paper_mode else 'Live USDT'}</div>
  </div>
  <div class="card">
    <div class="c-lbl">ROI</div>
    <div class="c-val {roi_cls}">{roi:+.2f}%</div>
    <div class="c-sub">P&L {_fmt_pnl(total_pnl)}</div>
  </div>
  <div class="card">
    <div class="c-lbl">Open Positions</div>
    <div class="c-val">{len(open_trades)}</div>
    <div class="c-sub">Win rate {win_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="c-lbl">Daily P&L</div>
    <div class="c-val {dpnl_cls}">{_fmt_pnl(daily_pnl)}</div>
    <div class="c-sub">{len([t for t in closed_trades if (t.get('close_time') or '').startswith(today_str)])} trades today</div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── Chart toolbar ─────────────────────────────────────────────────────
        tb1, tb2, tb3, tb4, tb5, tb6 = st.columns([4, 1, 1, 1, 1, 1])
        with tb1:
            mode_badge = ('<span class="cbadge green">📋 PAPER</span>'
                          if st.session_state.paper_mode
                          else '<span class="cbadge red">⚡ LIVE</span>')
            src_note = "live • public API" if chart_source == "public" else "live • auth"
            st.markdown(f"""
<div class="chart-bar">
  <div class="chart-title">
    <span>{st.session_state.symbol}</span>
    <span class="cbadge blue">{st.session_state.interval}</span>
    <span class="cbadge gold">{st.session_state.strategy}</span>
    {mode_badge}
    <span style="font-size:10px;color:#484f58;font-weight:400;">{src_note}</span>
  </div>
</div>
""", unsafe_allow_html=True)
        with tb2:
            buy_btn  = st.button("▲ BUY",  use_container_width=True,
                                  help="Manual BUY at current market price")
        with tb3:
            sell_btn = st.button("▼ SELL", use_container_width=True,
                                  help="Manual SELL at current market price")
        with tb4:
            emg_btn  = st.button("🚨 STOP", use_container_width=True, type="secondary")
        with tb5:
            bot_label = "⏹ Bot OFF" if bot_running else "⏩ Bot ON"
            if st.button(bot_label, use_container_width=True,
                          help="Toggle bot on/off"):
                if bot_running:
                    bot_module.stop_bot()
                else:
                    c = _cl()
                    if c is None and not st.session_state.paper_mode:
                        st.error("Connect first for live trading.")
                    else:
                        b = bot_module.create_bot(
                            client=c,
                            symbol=st.session_state.symbol,
                            strategy=st.session_state.strategy,
                            risk_manager=st.session_state.risk_manager,
                            interval=st.session_state.interval,
                            check_every=st.session_state.check_every,
                            paper_mode=True if c is None else st.session_state.paper_mode,
                            threshold=st.session_state.threshold / 100,
                        )
                        b.start()
                st.rerun()
        with tb6:
            if st.button("↺", use_container_width=True, help="Refresh"):
                st.rerun()

        # Manual trade execution
        if buy_btn or sell_btn:
            side = "BUY" if buy_btn else "SELL"
            # Use live price from public API if not authenticated
            price = live_price
            if price is None:
                st.error("No price available — chart not loaded yet.")
            else:
                c = _cl()
                invested = st.session_state.manual_amount
                qty = invested / price

                if c is not None:
                    try:
                        qty = c.round_quantity(st.session_state.symbol, qty)
                    except Exception:
                        qty = round(qty, 6)
                    if not st.session_state.paper_mode:
                        try:
                            order = c.place_market_order(st.session_state.symbol, side, qty)
                            price = float(order.get("fills", [{}])[0].get("price", price))
                        except Exception as e:
                            st.error(f"Order failed: {e}")
                            price = None
                else:
                    qty = round(qty, 6)

                if price is not None:
                    rm = st.session_state.risk_manager
                    trade = {
                        "coin":            st.session_state.symbol,
                        "exchange":        "Binance Testnet" if st.session_state.testnet else "Binance Live",
                        "type":            "manual",
                        "strategy":        "Manual",
                        "side":            side,
                        "entry_price":     price,
                        "exit_price":      None,
                        "quantity":        qty,
                        "invested":        invested,
                        "profit_loss":     None,
                        "profit_loss_pct": None,
                        "open_time":       datetime.now().isoformat(),
                        "close_time":      None,
                        "reason":          f"Manual {side} — ${invested:.2f} USDT @ ${price:.4f}",
                        "close_reason":    None,
                        "stop_loss":       rm.stop_loss_price(price, side),
                        "take_profit":     rm.take_profit_price(price, side),
                        "status":          "open",
                        "paper":           st.session_state.paper_mode,
                    }
                    added = add_trade(trade)
                    log_activity("ORDER",
                        f"👤 Manual {side} | {qty:.6f} {st.session_state.symbol} "
                        f"@ ${price:.4f} | ${invested:.2f} invested | ID:{added['id']}")
                    st.success(f"✅ {side} @ ${price:.4f} — ID: {added['id']}")

        if emg_btn:
            st.session_state.risk.emergency_stop = True
            bot_module.stop_bot()
            log_activity("WARNING", "🚨 EMERGENCY STOP activated")
            st.rerun()

        # ── Chart ─────────────────────────────────────────────────────────────
        if df_chart is not None and len(df_chart) > 5:
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                row_heights=[0.72, 0.28],
                vertical_spacing=0.02,
            )

            # Candlestick
            fig.add_trace(go.Candlestick(
                x=df_chart["open_time"],
                open=df_chart["open"], high=df_chart["high"],
                low=df_chart["low"],   close=df_chart["close"],
                name=st.session_state.symbol,
                increasing_line_color="#26a69a", increasing_fillcolor="#26a69a",
                decreasing_line_color="#ef5350", decreasing_fillcolor="#ef5350",
                line=dict(width=1), whiskerwidth=0,
            ), row=1, col=1)

            # EMA 9
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["ema9"],
                line=dict(color="#ef5350", width=1.5),
                name="EMA 9",
                hovertemplate="EMA9: %{y:.4f}<extra></extra>",
            ), row=1, col=1)

            # EMA 21
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["ema21"],
                line=dict(color="#e3b341", width=1.5),
                name="EMA 21",
                hovertemplate="EMA21: %{y:.4f}<extra></extra>",
            ), row=1, col=1)

            # Current price annotation
            if live_price:
                p_color = "#26a69a" if change_pct >= 0 else "#ef5350"
                fig.add_hline(
                    y=live_price, row=1, col=1,
                    line=dict(color=p_color, width=1, dash="dot"),
                    annotation_text=f"  {_fmt_p(live_price, 2)}",
                    annotation_position="right",
                    annotation_font_color=p_color,
                    annotation_font_size=10,
                )

            # Trade markers
            buckets = {
                "mb": ([], [], "triangle-up",   "#58a6ff", 14, "Manual BUY"),
                "ms": ([], [], "triangle-down", "#bc8cff", 14, "Manual SELL"),
                "bb": ([], [], "triangle-up",   "#26a69a", 11, "Bot BUY"),
                "bs": ([], [], "triangle-down", "#ef5350", 11, "Bot SELL"),
                "mx": ([], [], "x-thin",        "#58a6ff", 10, "Manual Exit"),
                "bx": ([], [], "x-thin",        "#e3b341", 10, "Bot Exit"),
            }

            for t in all_trades:
                try:
                    ts    = pd.to_datetime(t.get("open_time"))
                    ep    = t.get("entry_price")
                    ttype = t.get("type", "manual")
                    side  = t.get("side", "BUY")
                    if ep is None:
                        continue
                    if ttype == "manual":
                        k = "mb" if side == "BUY" else "ms"
                    else:
                        k = "bb" if side == "BUY" else "bs"
                    buckets[k][0].append(ts)
                    buckets[k][1].append(ep)

                    if t.get("exit_price") and t.get("close_time"):
                        ts_x = pd.to_datetime(t["close_time"])
                        xp   = t["exit_price"]
                        xk   = "mx" if ttype == "manual" else "bx"
                        buckets[xk][0].append(ts_x)
                        buckets[xk][1].append(xp)
                except Exception:
                    continue

            for bk, (bx, by, bsym, bcol, bsz, blbl) in buckets.items():
                if bx:
                    fig.add_trace(go.Scatter(
                        x=bx, y=by, mode="markers",
                        marker=dict(symbol=bsym, size=bsz, color=bcol,
                                    line=dict(color="rgba(255,255,255,0.5)", width=1)),
                        name=blbl,
                        hovertemplate=f"{blbl}<br>%{{x}}<br>%{{y:.4f}}<extra></extra>",
                    ), row=1, col=1)

            # Stochastic
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["stoch_k"],
                line=dict(color="#79b0ff", width=1.5), name="%K",
            ), row=2, col=1)
            fig.add_trace(go.Scatter(
                x=df_chart["open_time"], y=df_chart["stoch_d"],
                line=dict(color="#c9d1d9", width=1.2, dash="dot"), name="%D",
            ), row=2, col=1)
            fig.add_hline(y=80, line=dict(color="#ef535050", width=1, dash="dash"), row=2, col=1)
            fig.add_hline(y=20, line=dict(color="#26a69a50", width=1, dash="dash"), row=2, col=1)
            fig.add_hrect(y0=80, y1=100, fillcolor="#ef535008", line_width=0, row=2, col=1)
            fig.add_hrect(y0=0,  y1=20,  fillcolor="#26a69a08", line_width=0, row=2, col=1)

            G = "#1a2030"
            fig.update_layout(
                paper_bgcolor="#0a0c10",
                plot_bgcolor="#0a0c10",
                font=dict(color="#6e7681", family="'JetBrains Mono',monospace", size=10),
                xaxis_rangeslider_visible=False,
                height=590,
                margin=dict(l=0, r=64, t=6, b=0),
                showlegend=True,
                legend=dict(
                    bgcolor="rgba(13,17,23,0.88)",
                    bordercolor="#1e2736", borderwidth=1,
                    font=dict(size=10),
                    orientation="v",
                    yanchor="top", y=0.99,
                    xanchor="right", x=0.99,
                ),
                hovermode="x unified",
                hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d",
                                font_color="#c9d1d9", font_size=11),
            )
            fig.update_xaxes(
                gridcolor=G, gridwidth=1, zerolinecolor=G,
                showspikes=True, spikecolor="#484f58", spikewidth=1,
                tickfont=dict(size=10),
            )
            fig.update_yaxes(
                gridcolor=G, gridwidth=1, zerolinecolor=G,
                showspikes=True, spikecolor="#484f58",
                tickfont=dict(size=10),
                tickprefix="$",
                row=1, col=1,
            )
            fig.update_yaxes(
                gridcolor=G, gridwidth=1,
                range=[0, 100], ticksuffix="",
                tickfont=dict(size=10),
                row=2, col=1,
            )
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": True, "displaylogo": False,
                                    "modeBarButtonsToRemove": ["select2d", "lasso2d", "toImage"]})
        else:
            with st.spinner("Loading chart data from Binance…"):
                st.info("Chart will appear here once data loads. No API key required.")

        # ── Open Positions ────────────────────────────────────────────────────
        if open_trades:
            st.markdown('<div class="sec-lbl">Open Positions</div>', unsafe_allow_html=True)
            for ot in open_trades:
                ep   = ot.get("entry_price", 0)
                sl   = ot.get("stop_loss")   or st.session_state.risk_manager.stop_loss_price(ep, ot.get("side","BUY"))
                tp   = ot.get("take_profit") or st.session_state.risk_manager.take_profit_price(ep, ot.get("side","BUY"))
                side = ot.get("side", "BUY")
                pos_cls = "pos-buy" if side == "BUY" else "pos-sell"

                upnl_html = ""
                if live_price and ep:
                    inv  = ot.get("invested", 0) or 0
                    upnl = (live_price - ep) / ep * inv if side == "BUY" else (ep - live_price) / ep * inv
                    uc   = "#26a69a" if upnl >= 0 else "#ef5350"
                    upnl_html = f'<span style="color:{uc};font-weight:700;">{_fmt_pnl(upnl)}</span>'

                pc1, pc2 = st.columns([9, 1])
                with pc1:
                    st.markdown(f"""
<div class="pos-card {pos_cls}">
  <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;">
    <span style="color:#f0f6fc;font-weight:700;">{ot.get('coin','?')}</span>
    <span style="color:#{'26a69a' if side=='BUY' else 'ef5350'};font-weight:600;">{side}</span>
    <span style="color:#6e7681;">{'🤖 Bot' if ot.get('type')=='bot' else '👤 Manual'}</span>
    <span style="color:#484f58;">ID:{ot.get('id','?')}</span>
    <span style="color:#6e7681;font-size:10px;">{(ot.get('open_time') or '')[:16].replace('T',' ')}</span>
  </div>
  <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;">
    <div><div style="font-size:9px;color:#484f58;">ENTRY</div>${ep:.4f}</div>
    <div><div style="font-size:9px;color:#484f58;">SL</div><span style="color:#ef5350;">${sl:.4f}</span></div>
    <div><div style="font-size:9px;color:#484f58;">TP</div><span style="color:#26a69a;">${tp:.4f}</span></div>
    <div><div style="font-size:9px;color:#484f58;">UNREALIZED</div>{upnl_html}</div>
    <div><div style="font-size:9px;color:#484f58;">INVESTED</div>${ot.get('invested',0):.2f}</div>
  </div>
</div>
""", unsafe_allow_html=True)
                with pc2:
                    if st.button(f"✕ Close", key=f"cl_{ot.get('id')}",
                                 use_container_width=True):
                        c   = _cl()
                        xp  = c.get_symbol_price(ot["coin"]) if c else (live_price or ep)
                        close_trade(ot["id"], xp, "Manual close via dashboard")
                        log_activity("ORDER", f"👤 Closed {ot['id']} @ ${xp:.4f}")
                        st.rerun()

        # ── Tabs: History + Activity ──────────────────────────────────────────
        tab_h, tab_a, tab_s = st.tabs(["📋  Trade History", "📟  Activity Log", "📊  Stats"])

        with tab_h:
            if not all_trades:
                st.markdown('<div style="background:#0d1117;border:1px solid #1e2736;border-radius:6px;padding:18px;color:#484f58;text-align:center;">No trades yet — use Manual BUY/SELL or start the bot.</div>', unsafe_allow_html=True)
            else:
                rows = []
                for t in reversed(all_trades):
                    pnl = t.get("profit_loss")
                    pct = t.get("profit_loss_pct")
                    rows.append({
                        "ID":       t.get("id","—"),
                        "Coin":     t.get("coin","—"),
                        "Type":     "🤖 Bot" if t.get("type")=="bot" else "👤 Manual",
                        "Side":     t.get("side","—"),
                        "Strategy": t.get("strategy","—"),
                        "Entry":    f"${t.get('entry_price',0):.4f}",
                        "Exit":     f"${t.get('exit_price',0):.4f}" if t.get("exit_price") else "open",
                        "Invested": f"${t.get('invested',0):.2f}",
                        "P&L $":    _fmt_pnl(pnl),
                        "P&L %":    _fmt_pct(pct),
                        "Status":   t.get("status","—"),
                        "Opened":   (t.get("open_time") or "")[:16].replace("T"," "),
                        "Closed":   (t.get("close_time") or "")[:16].replace("T"," ") if t.get("close_time") else "—",
                        "Reason":   (t.get("reason") or "")[:90],
                    })
                st.dataframe(
                    pd.DataFrame(rows),
                    use_container_width=True, hide_index=True, height=280,
                    column_config={
                        "Reason": st.column_config.TextColumn("Reason", width="large"),
                    },
                )

        with tab_a:
            ac1, ac2 = st.columns([8, 1])
            with ac2:
                if st.button("🗑 Clear", use_container_width=True):
                    clear_activity(); st.rerun()

            activity = load_activity()
            if not activity:
                st.markdown('<div style="background:#0d1117;border:1px solid #1e2736;border-radius:6px;padding:18px;color:#484f58;text-align:center;">No activity yet.</div>', unsafe_allow_html=True)
            else:
                lines = []
                for entry in reversed(activity[-300:]):
                    ts  = (entry.get("time") or "")[:19].replace("T"," ")
                    lvl = entry.get("level","INFO")
                    msg = (entry.get("message","")
                           .replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
                    lines.append(
                        f'<div class="log-line">'
                        f'<span class="l-ts">{ts}</span>'
                        f'<span class="l-lvl l{lvl}">[{lvl}]</span>'
                        f'<span class="l-msg l{lvl}">{msg}</span>'
                        f'</div>'
                    )
                st.markdown(
                    '<div class="log-wrap">' + "".join(lines) + "</div>",
                    unsafe_allow_html=True,
                )

        with tab_s:
            s1, s2, s3, s4, s5 = st.columns(5)
            def _sc(col, lbl, val, cls=""):
                col.markdown(f'<div class="card" style="text-align:center;"><div class="c-lbl">{lbl}</div><div class="c-val {cls}">{val}</div></div>', unsafe_allow_html=True)
            _sc(s1, "Total Trades",  len(all_trades))
            _sc(s2, "Closed",        len(closed_trades))
            _sc(s3, "Wins",          wins, "up")
            _sc(s4, "Win Rate",      f"{win_rate:.1f}%", "up" if win_rate >= 50 else "dn")
            _sc(s5, "Total P&L",     _fmt_pnl(total_pnl), "up" if total_pnl >= 0 else "dn")

            if closed_trades:
                st.markdown('<div class="sec-lbl" style="margin-top:16px;">P&L per Closed Trade</div>', unsafe_allow_html=True)
                pnl_data = [
                    {"Trade": f"{t.get('id','?')} {t.get('side','')} {t.get('coin','')}",
                     "P&L $": round(t.get("profit_loss") or 0, 4),
                     "P&L %": round(t.get("profit_loss_pct") or 0, 2),
                     "Strategy": t.get("strategy","—"),
                     "Type": "🤖" if t.get("type")=="bot" else "👤",
                     "Closed": (t.get("close_time") or "")[:16].replace("T"," ")}
                    for t in closed_trades
                ]
                st.dataframe(pd.DataFrame(pnl_data), use_container_width=True,
                             hide_index=True, height=220)

        st.markdown("<div style='height:48px'></div>", unsafe_allow_html=True)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(30)
    st.rerun()
