import streamlit as st
import streamlit.components.v1 as st_html
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import os
import sys

_TZ = ZoneInfo("Europe/London")

# ── Path ──────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bot as bot_module
from bot import (
    load_trades, load_activity, get_open_trades,
    add_trade, close_trade, reset_all_data, clear_activity, log_activity,
    get_shared_df, get_shared_price, get_shared_updated_at, get_shared_last_tick,
    get_bot_session_trades, get_bot_last_signal, get_bot_signal_meta, force_paper_trade,
)
from strategy import get_indicators
from risk import RiskManager, RiskSettings
import telegram_notifier as tg
from binance_client import public_klines, public_price, public_24h

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AlphaTrade",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
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
[data-testid="stAppViewContainer"] { background: #0a0c10 !important; overflow-x: hidden; }
[data-testid="stHeader"]           { display: none !important; }
[data-testid="stToolbar"]          { display: none !important; }
[data-testid="stDecoration"]       { display: none !important; }
footer                             { display: none !important; }
#MainMenu                          { display: none !important; }
.block-container {
    padding: 0 !important; max-width: 100% !important;
}
/* Prevent ghost/duplicate elements during Streamlit rerenders on mobile */
body { overscroll-behavior-y: none; }
[data-testid="stAppViewContainer"] > .main > .block-container { min-height: 0 !important; }
iframe[height="0"] { display: none !important; }

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

/* ── Modern crypto/trading polish (neon + glass) ── */
.at-logo {
    font-size: 19px; font-weight: 800; letter-spacing: -0.4px;
    background: linear-gradient(135deg, #f0f6fc 0%, #79b0ff 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
}
.at-logo span.acc {
    background: linear-gradient(135deg, #2962ff 0%, #79b0ff 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    color: transparent;
}
/* NOTE: backdrop-filter intentionally REMOVED — it caused ghost/duplicate
   layers during scroll inside Streamlit's iframe (GPU compositing artifact).
   Visual depth is now achieved via solid gradients + box-shadows only. */
.card {
    background: linear-gradient(180deg, #161b22 0%, #0d1117 100%);
    box-shadow: 0 1px 0 rgba(255,255,255,0.02) inset;
}
.card:hover {
    border-color: #2962ff66;
    box-shadow: 0 0 0 1px rgba(41,98,255,0.18), 0 4px 18px -8px rgba(41,98,255,0.35);
}
.c-val.up { text-shadow: 0 0 14px rgba(38,166,154,0.45); }
.c-val.dn { text-shadow: 0 0 14px rgba(239,83,80,0.45); }
.p-green { box-shadow: 0 0 12px -2px rgba(38,166,154,0.30); }
.p-red   { box-shadow: 0 0 12px -2px rgba(239,83,80,0.30); }
.p-gold  { box-shadow: 0 0 12px -2px rgba(227,179,65,0.25); }
.cbadge.green { box-shadow: 0 0 10px -3px rgba(63,185,80,0.45); }
.cbadge.red   { box-shadow: 0 0 10px -3px rgba(239,83,80,0.45); }
.stButton>button {
    transition: transform .08s ease, box-shadow .15s ease, border-color .15s ease !important;
}
.stButton>button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 14px -6px rgba(41,98,255,0.55) !important;
    border-color: #2962ff88 !important;
}
.stButton>button:active { transform: translateY(0); }
.mkt-tile.mkt-active {
    box-shadow: 0 0 0 1px #2962ff88, 0 0 18px -6px rgba(41,98,255,0.5);
}
.chart-title span:first-child {
    font-size: 14px; letter-spacing: -0.2px;
}
.sec-lbl {
    background: linear-gradient(90deg, #6e7681 0%, #484f58 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
}

/* ── Market overview strip ── */
.mkt-strip {
    display:flex; gap:6px; overflow-x:auto; padding:8px 16px;
    background:#0d1117; border-bottom:1px solid #1e2736;
    scrollbar-width:none;
}
.mkt-strip::-webkit-scrollbar { display:none; }
.mkt-tile {
    display:flex; flex-direction:column; gap:2px;
    background:#161b22; border:1px solid #1e2736;
    border-radius:6px; padding:7px 12px; min-width:108px; flex-shrink:0;
    transition:border-color .15s, background .15s;
}
.mkt-tile:hover { background:#1c2128; border-color:#2962ff55; }
.mkt-tile.mkt-active { border-color:#2962ff99; background:#16203a; }
.mt-sym   { font-size:9px; color:#6e7681; font-weight:700; letter-spacing:.12em; }
.mt-price { font-size:13px; font-weight:700; color:#f0f6fc; font-family:'JetBrains Mono',monospace; line-height:1.2; }
.mt-chg   { font-size:11px; font-weight:600; }
.mt-up    { color:#26a69a; }
.mt-dn    { color:#ef5350; }
.mt-vol   { font-size:9px; color:#3d444d; margin-top:1px; }

/* ── Bot performance stat grid (sidebar) ── */
.bot-stat-grid {
    display:grid; grid-template-columns:1fr 1fr; gap:5px; margin-top:6px;
}
.bot-stat-cell {
    background:#161b22; border:1px solid #1e2736;
    border-radius:6px; padding:7px 9px;
}
.bsc-lbl { font-size:9px; color:#484f58; text-transform:uppercase; letter-spacing:.1em; margin-bottom:3px; }
.bsc-val { font-size:14px; font-weight:700; color:#f0f6fc; font-family:'JetBrains Mono',monospace; }
.bsc-val.up { color:#26a69a; }
.bsc-val.dn { color:#ef5350; }

/* ── Next-check countdown ── */
.cd-row { display:flex; justify-content:space-between; font-size:9px; color:#484f58; margin:8px 0 3px; }
.cd-bar  { height:3px; background:#1e2736; border-radius:2px; overflow:hidden; }
.cd-fill { height:100%; background:#2962ff; border-radius:2px; transition:width .8s linear; }
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
        "refresh_secs":     5,
        "alert_open_ids":      [],
        "alert_closed_ids":    [],
        "pending_live_trade":  None,   # dict stored between reruns for live confirmation
        "tg_enabled":          False,
        "tg_token":            "",
        "tg_chat_id":          "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # ── Migrate: ensure new RiskSettings fields exist on old sessions ─────────
    _r = st.session_state.get("risk")
    if _r is not None:
        if not hasattr(_r, "invest_per_trade"):      _r.invest_per_trade      = 50.0
        if not hasattr(_r, "max_trade_usdt"):        _r.max_trade_usdt        = 100.0
        if not hasattr(_r, "max_trades_per_session"):_r.max_trades_per_session= 0

    # ── Re-apply Telegram config on every cold-start ─────────────────────────
    tg.configure(
        token   = st.session_state.get("tg_token",   ""),
        chat_id = st.session_state.get("tg_chat_id", ""),
        enabled = st.session_state.get("tg_enabled", False),
    )

_init()

SYMBOLS    = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT"]
INTERVALS  = ["1m","3m","5m","15m","30m","1h","4h","1d"]
STRATEGIES = ["EMA Crossover","Price Movement","Momentum (RSI)"]


def _cl():
    return st.session_state.get("client")

def _fmt_p(v, d=4): return f"${v:,.{d}f}" if v is not None else "—"

@st.cache_data(ttl=30, show_spinner=False)
def _market_overview():
    """Fetch 24h stats for all symbols — cached 30s to avoid hammering on every 5s rerun."""
    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
    out = {}
    def _fetch(s):
        try: return s, public_24h(s, testnet=False)
        except: return s, None
    try:
        with ThreadPoolExecutor(max_workers=10) as ex:
            for f in _asc({ex.submit(_fetch, s): s for s in SYMBOLS}, timeout=7):
                try:
                    s, d = f.result()
                    if d: out[s] = d
                except: pass
    except: pass
    return out


def _fmt_pnl(v):
    if v is None: return "—"
    return f"+${v:.4f}" if v >= 0 else f"-${abs(v):.4f}"
def _fmt_pct(v):
    if v is None: return "—"
    return f"{v:+.2f}%"


# ─────────────────────────────────────────────────────────────────────────────
# LIVE MARKET DATA — always fetched fresh on every rerun
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

# 1. Always fetch 24h stats (public, no auth needed)
try:
    stats      = public_24h(sym, testnet=False)
    live_price = stats["price"]
    change_pct = stats["change_pct"]
    high_24h   = stats["high"]
    low_24h    = stats["low"]
except Exception:
    pass

# 2. Chart data — prefer bot's continuously-updated shared df when bot is running
bot_inst    = bot_module.get_bot()
bot_running = bot_inst.is_running() if bot_inst else False

_bot_df = get_shared_df() if bot_running else None
if _bot_df is not None and len(_bot_df) > 5:
    df_chart     = _bot_df
    chart_source = "bot-live"
    # Also use bot's price if available
    _bot_price = get_shared_price()
    if _bot_price:
        live_price = _bot_price
else:
    # Fall back: fetch fresh from public API every rerun
    try:
        df_raw   = public_klines(sym, interval, limit=200, testnet=False)
        df_chart = get_indicators(df_raw)
        chart_source = "public"
    except Exception:
        pass

    # Authenticated client overrides (testnet prices differ)
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
all_trades     = load_trades()
open_trades    = get_open_trades()
closed_trades  = [t for t in all_trades if t.get("status") == "closed"]
realized_pnl   = sum((t.get("profit_loss") or 0) for t in closed_trades)
today_str      = datetime.now(_TZ).strftime("%Y-%m-%d")
daily_realized = sum(
    (t.get("profit_loss") or 0) for t in closed_trades
    if (t.get("close_time") or "").startswith(today_str)
)
wins     = sum(1 for t in closed_trades if (t.get("profit_loss") or 0) >= 0)
win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0.0

# ── Unrealized PnL: mark-to-market on every open position ─────────────────────
# Uses live_price for trades on the active symbol; falls back to public_price
# for any open position on a different symbol. Per-symbol price cached per render.
_price_cache: dict[str, float] = {}
if live_price:
    _price_cache[st.session_state.symbol] = live_price

def _cur_price_for(sym: str) -> float | None:
    if sym in _price_cache:
        return _price_cache[sym]
    try:
        p = public_price(sym, testnet=st.session_state.testnet)
        _price_cache[sym] = p
        return p
    except Exception:
        return None

unrealized_pnl = 0.0
for _ot in open_trades:
    _ep   = _ot.get("entry_price")
    _inv  = _ot.get("invested") or 0
    _side = _ot.get("side", "BUY")
    _cp   = _cur_price_for(_ot.get("coin", st.session_state.symbol))
    if not (_ep and _cp and _inv):
        continue
    _u = ((_cp - _ep) / _ep * _inv) if _side == "BUY" else ((_ep - _cp) / _ep * _inv)
    unrealized_pnl += _u
    # Cache per-position unrealized for re-use in the open-positions list
    _ot["_unrealized"] = _u
    _ot["_cur_price"]  = _cp

total_pnl = realized_pnl + unrealized_pnl
daily_pnl = daily_realized + unrealized_pnl  # today's realized + currently-open marks

# Equity = starting capital + realized PnL + unrealized PnL
equity  = st.session_state.initial_balance + realized_pnl + unrealized_pnl

# Paper balance mirrors equity (no margin in paper mode); LIVE overrides with API
balance = equity
binance_total_usdt = 0.0     # free + locked, real Binance balance
binance_free_usdt  = 0.0     # available USDT for new orders
_binance_connected = (st.session_state.connected and _cl() is not None)
if _binance_connected:
    try:
        _bal = _cl().get_asset_balance_full("USDT")
        binance_total_usdt = _bal["total"]
        binance_free_usdt  = _bal["free"]
        if not st.session_state.paper_mode:
            balance = binance_total_usdt   # LIVE/TESTNET equity = real Binance balance
    except Exception:
        pass

roi = (total_pnl / st.session_state.initial_balance * 100) if st.session_state.initial_balance else 0.0

# ─────────────────────────────────────────────────────────────────────────────
# ALERT DETECTION — diff against last known trade IDs
# ─────────────────────────────────────────────────────────────────────────────
_known_open   = set(st.session_state.alert_open_ids)
_known_closed = set(st.session_state.alert_closed_ids)

_cur_open_ids   = {t["id"] for t in open_trades   if t.get("id")}
_cur_closed_ids = {t["id"] for t in closed_trades if t.get("id")}

_new_opens  = _cur_open_ids   - _known_open
_new_closes = _cur_closed_ids - _known_closed

# Build alert payloads
_alert_events = []
for _tid in _new_opens:
    _tr = next((t for t in open_trades if t.get("id") == _tid), None)
    if _tr:
        _side = _tr.get("side", "BUY")
        _coin = _tr.get("coin", "")
        _ep   = _tr.get("entry_price", 0)
        _typ  = "🤖 Bot" if _tr.get("type") == "bot" else "👤 Manual"
        _alert_events.append({
            "kind":  "open",
            "side":  _side,
            "title": f"{_typ} {_side} opened",
            "body":  f"{_coin} @ ${_ep:,.4f}",
            "color": "#26a69a" if _side == "BUY" else "#ef5350",
            "sound": "buy" if _side == "BUY" else "sell",
        })

for _tid in _new_closes:
    _tr = next((t for t in closed_trades if t.get("id") == _tid), None)
    if _tr:
        _pnl  = _tr.get("profit_loss") or 0
        _pct  = _tr.get("profit_loss_pct") or 0
        _coin = _tr.get("coin", "")
        _typ  = "🤖 Bot" if _tr.get("type") == "bot" else "👤 Manual"
        _win  = _pnl >= 0
        _alert_events.append({
            "kind":  "close",
            "side":  "WIN" if _win else "LOSS",
            "title": f"{_typ} {'WIN' if _win else 'LOSS'} — trade closed",
            "body":  f"{_coin}  {'+' if _win else ''}${abs(_pnl):,.4f} ({_pct:+.2f}%)",
            "color": "#26a69a" if _win else "#ef5350",
            "sound": "win" if _win else "loss",
        })

# Update known IDs
st.session_state.alert_open_ids   = list(_cur_open_ids)
st.session_state.alert_closed_ids = list(_cur_closed_ids)

# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────
price_str = _fmt_p(live_price, 2) if live_price else "Loading…"
chg_cls   = "chg-up" if change_pct >= 0 else "chg-dn"
chg_str   = f"{'▲' if change_pct >= 0 else '▼'} {abs(change_pct):.2f}%"
h_str     = _fmt_p(high_24h, 2) if high_24h else "—"
l_str     = _fmt_p(low_24h, 2)  if low_24h  else "—"

_upd_at  = get_shared_updated_at() if bot_running else None
_now_tz  = datetime.now(_TZ)
_upd_str = _upd_at.strftime("%H:%M:%S") if _upd_at else _now_tz.strftime("%H:%M:%S")
_src_label = {"bot-live": "bot-live", "auth": "auth-live", "public": "public"}.get(chart_source, "—")

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
_ref_secs = st.session_state.get("refresh_secs", 5)
live_pill = f'<span class="pill p-gold"><span class="dot dot-y"></span>LIVE {_ref_secs}s · {_upd_str} <span style="font-size:9px;opacity:.6;">LON</span></span>'

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
    {live_pill}{net_pill}{mode_pill}{conn_pill}{bot_pill}
  </div>
</div>
""", unsafe_allow_html=True)

# ── Alert toasts + audio (injected into browser, height=0 so invisible) ───────
if _alert_events:
    import json as _json
    _events_json = _json.dumps(_alert_events)
    _alert_html = f"""
<style>
.at-toast-wrap {{
    position:fixed; top:64px; right:18px; z-index:99999;
    display:flex; flex-direction:column; gap:10px; pointer-events:none;
}}
.at-toast {{
    display:flex; align-items:flex-start; gap:12px;
    background:#161b22; border:1px solid var(--tc);
    border-radius:8px; padding:12px 16px;
    box-shadow:0 8px 32px rgba(0,0,0,.55);
    min-width:260px; max-width:340px;
    animation: atSlideIn .3s ease forwards;
    pointer-events:all;
    font-family:'Inter',-apple-system,sans-serif;
}}
.at-toast.fade-out {{ animation: atFadeOut .4s ease forwards; }}
.at-toast-icon {{ font-size:22px; line-height:1; margin-top:1px; }}
.at-toast-body {{ flex:1; }}
.at-toast-title {{ font-size:13px; font-weight:700; color:#f0f6fc; margin-bottom:3px; }}
.at-toast-msg   {{ font-size:12px; color:#8b949e; font-family:'JetBrains Mono',monospace; }}
.at-toast-bar   {{
    height:3px; border-radius:0 0 8px 8px;
    margin:-12px -16px -12px; margin-top:10px;
    background:var(--tc); opacity:.6;
    animation: atBar linear forwards;
}}
@keyframes atSlideIn {{
    from {{ opacity:0; transform:translateX(40px); }}
    to   {{ opacity:1; transform:translateX(0); }}
}}
@keyframes atFadeOut {{
    from {{ opacity:1; transform:translateX(0); }}
    to   {{ opacity:0; transform:translateX(40px); }}
}}
@keyframes atBar {{
    from {{ width:100%; }}
    to   {{ width:0%; }}
}}
</style>
<div class="at-toast-wrap" id="atToastWrap"></div>
<script>
(function(){{
  var events = {_events_json};
  var wrap = document.getElementById('atToastWrap');
  if (!wrap) return;

  function playSound(kind) {{
    try {{
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      function tone(freq, start, dur, vol) {{
        var osc  = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, ctx.currentTime + start);
        gain.gain.setValueAtTime(vol, ctx.currentTime + start);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + dur);
        osc.start(ctx.currentTime + start);
        osc.stop(ctx.currentTime + start + dur + 0.05);
      }}
      if (kind === 'buy')  {{ tone(520,0,.12,.18); tone(660,.11,.18,.14); }}
      if (kind === 'sell') {{ tone(440,0,.12,.18); tone(330,.11,.18,.14); }}
      if (kind === 'win')  {{ tone(520,0,.10,.15); tone(660,.09,.10,.12); tone(780,.18,.20,.10); }}
      if (kind === 'loss') {{ tone(440,0,.15,.15); tone(330,.14,.20,.12); }}
    }} catch(e) {{}}
  }}

  function showToast(ev) {{
    var icon = ev.kind==='open'
      ? (ev.side==='BUY' ? '▲' : '▼')
      : (ev.side==='WIN' ? '✦' : '✕');

    var el = document.createElement('div');
    el.className = 'at-toast';
    el.style.setProperty('--tc', ev.color);
    el.innerHTML =
      '<div class="at-toast-icon" style="color:'+ev.color+'">'+icon+'</div>'+
      '<div class="at-toast-body">'+
        '<div class="at-toast-title">'+ev.title+'</div>'+
        '<div class="at-toast-msg">'+ev.body+'</div>'+
      '</div>'+
      '<div class="at-toast-bar" style="animation-duration:4s;"></div>';

    wrap.appendChild(el);
    playSound(ev.sound);

    setTimeout(function() {{
      el.classList.add('fade-out');
      setTimeout(function() {{ if(el.parentNode) el.parentNode.removeChild(el); }}, 420);
    }}, 4000);
  }}

  events.forEach(function(ev) {{ showToast(ev); }});
}})();
</script>
"""
    st_html.html(_alert_html, height=0)

# ── Market overview strip ──────────────────────────────────────────────────────
_mkt = _market_overview()
_cur_sym = st.session_state.symbol
_tiles_html = ""
for _s in SYMBOLS:
    _d = _mkt.get(_s, {})
    _mp   = float(_d.get("price",      0)) if _d else 0
    _mc   = float(_d.get("change_pct", 0)) if _d else 0
    _mv   = float(_d.get("volume",     0)) if _d else 0
    _active_cls = " mkt-active" if _s == _cur_sym else ""
    _chg_cls    = "mt-up" if _mc >= 0 else "mt-dn"
    _chg_sign   = "+" if _mc >= 0 else ""
    _price_str  = f"${_mp:,.2f}" if _mp >= 1 else f"${_mp:.4f}"
    _vol_str    = f"Vol {_mv/1e6:.1f}M" if _mv >= 1e6 else f"Vol {_mv/1e3:.0f}K"
    _sym_short  = _s.replace("USDT","")
    _tiles_html += f"""
  <div class="mkt-tile{_active_cls}">
    <div class="mt-sym">{_sym_short}</div>
    <div class="mt-price">{_price_str if _mp else "—"}</div>
    <div class="mt-chg {_chg_cls}">{_chg_sign}{_mc:.2f}%</div>
    <div class="mt-vol">{_vol_str if _mp else ""}</div>
  </div>"""
st.markdown(f'<div class="mkt-strip">{_tiles_html}</div>', unsafe_allow_html=True)

# ── Paper-verification gate ───────────────────────────────────────────────────
# LIVE trading is locked until ≥3 paper trades have been opened AND closed.
def paper_verified() -> tuple[bool, int, int]:
    """Returns (verified, paper_open_count, paper_closed_count)."""
    _trs = load_trades()
    _p   = [t for t in _trs if t.get("paper") is True]
    _po  = sum(1 for t in _p if t.get("status") == "open")
    _pc  = sum(1 for t in _p if t.get("status") == "closed")
    return (_pc >= 3, _po, _pc)   # require 3 closed paper trades before LIVE unlock


# ── Verification Panel (always visible in main area) ──────────────────────────
_conn_banner_parts = []
# Left: connection status
if st.session_state.connected and _cl():
    _net_lbl  = "TESTNET" if st.session_state.testnet else "🔴 LIVE MAINNET"
    _net_col  = "#26a69a" if st.session_state.testnet else "#ef5350"
    try:
        _b_usdt = _cl().get_account_balance("USDT")
        _bal_str = f"${_b_usdt:,.2f} USDT"
    except Exception:
        _bal_str = "Balance unavailable"
    _conn_banner_parts.append(
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span class="dot dot-g"></span>'
        f'<span style="font-size:11px;font-weight:700;color:{_net_col};">{_net_lbl}</span>'
        f'<span style="font-size:11px;color:#6e7681;">·</span>'
        f'<span style="font-size:12px;font-weight:600;color:#f0f6fc;font-family:\'JetBrains Mono\',monospace;">{_bal_str}</span>'
        f'</div>'
    )
else:
    _conn_banner_parts.append(
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span class="dot dot-x"></span>'
        f'<span style="font-size:11px;color:#6e7681;">Not connected · Paper mode · open sidebar to connect</span>'
        f'</div>'
    )

# Right: bot status + last signal + confidence + last check
# Prefer the structured meta from shared state (set by every bot tick);
# fall back to log-message parsing if the bot hasn't ticked yet this process.
_sig_meta = get_bot_signal_meta()
_sig_dir  = _sig_meta.get("signal") or "—"
_sig_conf = int(_sig_meta.get("confidence") or 0)
_sig_reason = (_sig_meta.get("reason") or "")[:110]
if _sig_dir == "—":
    _sig_entry = get_bot_last_signal()
    _sig_msg   = (_sig_entry.get("message","") or "") if _sig_entry else ""
    for _d in ("BUY", "SELL", "HOLD"):
        if f"→ {_d}" in _sig_msg:
            _sig_dir = _d
            break
    if not _sig_reason and "|" in _sig_msg:
        _sig_reason = _sig_msg.split("|", 1)[1].strip()[:110]
_sig_col_map = {"BUY":"#26a69a","SELL":"#ef5350","HOLD":"#6e7681","—":"#484f58"}
_sig_col = _sig_col_map.get(_sig_dir, "#484f58")
# Confidence bar colour: green >=70, amber 40-69, gray <40
if _sig_conf >= 70:   _conf_col = "#26a69a"
elif _sig_conf >= 40: _conf_col = "#e3b341"
else:                 _conf_col = "#6e7681"

# Last bot check time (London) + seconds elapsed
_last_tick = get_shared_last_tick()
if _last_tick:
    _tick_str = _last_tick.strftime("%H:%M:%S")
    _elapsed  = int((datetime.now() - _last_tick).total_seconds())
    _tick_disp = f"{_tick_str} <span style=\"opacity:.55;\">· {_elapsed}s ago</span>"
else:
    _tick_disp = "—"

# Paper verification status
_pv_ok, _pv_open, _pv_closed = paper_verified()
if _pv_ok:
    _gate_html = (f'<span class="pill p-green"><span class="dot dot-g"></span>'
                  f'✅ LIVE UNLOCKED · {_pv_closed} paper closed</span>')
else:
    _gate_html = (f'<span class="pill p-gold"><span class="dot dot-y"></span>'
                  f'🔒 LIVE LOCKED · {_pv_open} open / {_pv_closed} closed paper</span>')

if bot_running:
    _bot_dot = '<span class="dot dot-y"></span>'
    _bot_lbl = '<span style="font-size:11px;font-weight:700;color:#e3b341;">BOT ON</span>'
else:
    _bot_dot = '<span class="dot dot-x"></span>'
    _bot_lbl = '<span style="font-size:11px;font-weight:700;color:#6e7681;">BOT OFF</span>'

_strat_html = (f'<span style="font-size:10px;color:#484f58;">STRATEGY</span> '
               f'<span style="font-size:11px;font-weight:700;color:#79b0ff;font-family:\'JetBrains Mono\',monospace;">'
               f'{st.session_state.strategy}</span>')

_bot_status_html = (
    f'<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">'
    f'<div style="display:flex;align-items:center;gap:6px;">{_bot_dot}{_bot_lbl}</div>'
    f'<div>{_strat_html}</div>'
    f'<div style="display:flex;align-items:center;gap:6px;">'
    f'<span style="font-size:10px;color:#484f58;">SIGNAL</span>'
    f'<span style="font-size:11px;font-weight:800;color:{_sig_col};font-family:\'JetBrains Mono\',monospace;">{_sig_dir}</span>'
    f'</div>'
    f'<div style="display:flex;align-items:center;gap:6px;">'
    f'<span style="font-size:10px;color:#484f58;">CONFIDENCE</span>'
    f'<span style="display:inline-block;width:60px;height:6px;background:#1e2736;border-radius:3px;overflow:hidden;vertical-align:middle;">'
    f'<span style="display:block;width:{max(0,min(100,_sig_conf))}%;height:100%;background:{_conf_col};"></span>'
    f'</span>'
    f'<span style="font-size:11px;font-weight:700;color:{_conf_col};font-family:\'JetBrains Mono\',monospace;">{_sig_conf}</span>'
    f'</div>'
    f'<div style="display:flex;align-items:center;gap:6px;">'
    f'<span style="font-size:10px;color:#484f58;">LAST CHECK</span>'
    f'<span style="font-size:11px;color:#c9d1d9;font-family:\'JetBrains Mono\',monospace;">{_tick_disp}</span>'
    f'</div>'
    f'<div>{_gate_html}</div>'
    f'</div>'
)

_reason_html = (
    f'<div style="padding:5px 20px;background:#0a0d12;border-bottom:1px solid #1e2736;'
    f'font-size:10px;color:#6e7681;font-family:\'JetBrains Mono\',monospace;'
    f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
    f'<span style="color:#484f58;">REASON ›</span> {_sig_reason or "Waiting for first bot check…"}'
    f'</div>'
) if bot_running else ""

st.markdown(
    f'<div style="display:flex;align-items:center;justify-content:space-between;'
    f'flex-wrap:wrap;gap:10px;padding:8px 20px;background:#0d1117;border-bottom:1px solid #1e2736;">'
    f'{"".join(_conn_banner_parts)}'
    f'{_bot_status_html}'
    f'</div>'
    f'{_reason_html}',
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ AlphaTrade")

    # Connection
    st.markdown('<div class="sec-lbl">API Connection</div>', unsafe_allow_html=True)
    testnet_tog = st.toggle("🧪 Use Binance Testnet", value=st.session_state.testnet,
                             help="Use testnet.binance.vision for safe testing")
    st.session_state.testnet = testnet_tog
    if not testnet_tog:
        st.warning("⚠️ MAINNET — real money at risk! Double-check before trading.")
    else:
        st.info("Testnet mode — get keys at testnet.binance.vision")

    if st.session_state.connected and _cl():
        _conn_c = _cl()
        try:
            _live_usdt = _conn_c.get_account_balance("USDT")
            _net_label = "Testnet" if st.session_state.testnet else "🔴 LIVE"
            st.markdown(f"""
<div style="background:#0d2a1a;border:1px solid #26a69a44;border-radius:8px;padding:10px 12px;margin-bottom:8px;">
  <div style="font-size:9px;color:#26a69a;font-weight:700;letter-spacing:.1em;margin-bottom:4px;">CONNECTED · {_net_label}</div>
  <div style="font-size:18px;font-weight:700;color:#f0f6fc;font-family:'JetBrains Mono',monospace;">${_live_usdt:,.2f} <span style="font-size:11px;color:#6e7681;">USDT</span></div>
</div>""", unsafe_allow_html=True)
        except Exception:
            st.success("✅ Connected to Binance")
        if st.button("🔌 Disconnect", use_container_width=True):
            st.session_state.client    = None
            st.session_state.connected = False
            st.rerun()
    else:
        api_key    = st.text_input("API Key",    type="password",
                                    placeholder="Paste your Binance API key")
        api_secret = st.text_input("API Secret", type="password",
                                    placeholder="Paste your Binance API secret")
        if st.button("🔌 Connect to Binance", use_container_width=True, type="primary"):
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
                        st.error(f"Connection error: {e}")
            else:
                st.info("API keys needed for real balance & live trading.\nChart + paper mode work without keys.")

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
    _pv_ok_sb, _pv_open_sb, _pv_closed_sb = paper_verified()
    if not _pv_ok_sb:
        st.session_state.paper_mode = True
        paper_tog = st.toggle(
            "Paper mode (LIVE locked until 3 paper trades close)",
            value=True, disabled=True,
            help=f"Close ≥3 paper trades to unlock LIVE. Open: {_pv_open_sb} · Closed: {_pv_closed_sb}",
        )
    else:
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
            _eff_paper = True if c is None else paper_tog
            if c is None and not paper_tog:
                st.error("Connect to Binance first — live trading requires API keys.")
            elif not _eff_paper and not paper_verified()[0]:
                st.error("🔒 LIVE trading locked — close ≥3 paper trades first to unlock LIVE.")
                st.session_state.paper_mode = True
                st.rerun()
            else:
                # client=None is fine; bot falls back to public Binance API
                b = bot_module.create_bot(
                    client=c,
                    symbol=st.session_state.symbol,
                    strategy=st.session_state.strategy,
                    risk_manager=st.session_state.risk_manager,
                    interval=intv_sel,
                    check_every=ck_val,
                    paper_mode=_eff_paper,
                    threshold=thr_val / 100,
                )
                b._initial_balance = st.session_state.initial_balance
                b.start()
                if c is None:
                    st.info("🔓 Running in paper mode with public Binance data — no API key needed.")
                st.rerun()
    with bc2:
        if st.button("⏹ Stop", use_container_width=True, disabled=not bot_running):
            bot_module.stop_bot()
            st.rerun()

    # Force test trade (bypasses signal — paper only)
    if live_price:
        _ft1, _ft2 = st.columns(2)
        with _ft1:
            if st.button("🧪 Force BUY", use_container_width=True,
                          help="Open a paper BUY trade right now — no signal needed"):
                _inv_ft = st.session_state.risk_manager.get_invest_amount()
                force_paper_trade(st.session_state.symbol, "BUY", live_price, _inv_ft)
                st.success(f"✅ Force BUY @ ${live_price:.4f}")
                st.rerun()
        with _ft2:
            if st.button("🧪 Force SELL", use_container_width=True,
                          help="Open a paper SELL trade right now — no signal needed"):
                _inv_ft = st.session_state.risk_manager.get_invest_amount()
                force_paper_trade(st.session_state.symbol, "SELL", live_price, _inv_ft)
                st.success(f"✅ Force SELL @ ${live_price:.4f}")
                st.rerun()
    else:
        st.caption("Waiting for live price before force trade is available…")

    if st.button("🚨 Emergency Stop", use_container_width=True, type="secondary"):
        st.session_state.risk.emergency_stop = True
        bot_module.stop_bot()
        log_activity("WARNING", "🚨 EMERGENCY STOP activated — all trading halted")
        tg.bot_event("emergency", "All trading halted by user")
        st.rerun()

    if st.session_state.risk.emergency_stop:
        st.error("🚨 Emergency stop ACTIVE")
        if st.button("✅ Clear Emergency Stop", use_container_width=True):
            st.session_state.risk.emergency_stop = False
            log_activity("INFO", "✅ Emergency stop cleared — trading resumed")
            st.rerun()

    # ── Bot performance stats ──────────────────────────────────────────────
    _all_tr   = load_trades()
    _bt       = [t for t in _all_tr if t.get("type") == "bot"]
    _bt_cl    = [t for t in _bt if t.get("status") == "closed"]
    _bt_wins  = sum(1 for t in _bt_cl if (t.get("profit_loss") or 0) >= 0)
    _bt_wr    = (_bt_wins / len(_bt_cl) * 100) if _bt_cl else 0.0
    _bt_pnl   = sum((t.get("profit_loss") or 0) for t in _bt_cl)
    _bt_avg   = (_bt_pnl / len(_bt_cl)) if _bt_cl else 0.0
    _wr_cls   = "up" if _bt_wr  >= 50 else ("dn" if _bt_cl else "")
    _pnl_cls  = "up" if _bt_pnl >= 0  else "dn"
    _avg_cls  = "up" if _bt_avg >= 0  else "dn"
    _pnl_sign = "+" if _bt_pnl >= 0 else ""
    _avg_sign = "+" if _bt_avg >= 0 else ""

    _sess_trades = get_bot_session_trades()
    _max_sess    = st.session_state.risk.max_trades_per_session
    _sess_str    = f"{_sess_trades}/{_max_sess}" if _max_sess > 0 else f"{_sess_trades}/∞"
    _inv_disp    = st.session_state.risk_manager.get_invest_amount()

    st.markdown(f"""
<div style="margin-top:4px;">
<div class="bsc-lbl" style="margin-bottom:4px;">BOT PERFORMANCE</div>
<div class="bot-stat-grid">
  <div class="bot-stat-cell">
    <div class="bsc-lbl">Session</div>
    <div class="bsc-val">{_sess_str}</div>
  </div>
  <div class="bot-stat-cell">
    <div class="bsc-lbl">Win Rate</div>
    <div class="bsc-val {_wr_cls}">{_bt_wr:.0f}%</div>
  </div>
  <div class="bot-stat-cell">
    <div class="bsc-lbl">Total P&amp;L</div>
    <div class="bsc-val {_pnl_cls}">{_pnl_sign}${abs(_bt_pnl):.2f}</div>
  </div>
  <div class="bot-stat-cell">
    <div class="bsc-lbl">Per Trade</div>
    <div class="bsc-val">${_inv_disp:.0f}</div>
  </div>
</div>
</div>""", unsafe_allow_html=True)

    # ── Next-check countdown (only while bot is running) ──────────────────
    if bot_running:
        _last_tick = get_shared_last_tick()
        if _last_tick:
            from datetime import datetime as _dt
            _elapsed  = (_dt.now() - _last_tick).total_seconds()
            _chk_ev   = st.session_state.check_every
            _remain   = max(0.0, _chk_ev - (_elapsed % _chk_ev))
            _fill_pct = (1 - _remain / _chk_ev) * 100
            st.markdown(f"""
<div style="margin-top:8px;">
  <div class="cd-row">
    <span>NEXT CHECK</span>
    <span style="font-family:'JetBrains Mono',monospace;">{_remain:.0f}s</span>
  </div>
  <div class="cd-bar"><div class="cd-fill" style="width:{_fill_pct:.1f}%;"></div></div>
</div>""", unsafe_allow_html=True)

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Investment
    st.markdown('<div class="sec-lbl">Investment</div>', unsafe_allow_html=True)
    if paper_tog:
        ib = st.number_input("Simulated balance (USDT)", 100.0, 1_000_000.0,
                              st.session_state.initial_balance, 100.0)
        st.session_state.initial_balance = ib

    # ── Investment mode selector (Fixed USDT vs % of Available) ──────────────
    if "invest_mode" not in st.session_state:
        st.session_state.invest_mode = "Fixed USDT"
    if "invest_pct"  not in st.session_state:
        st.session_state.invest_pct  = 5.0

    _avail_for_calc = binance_free_usdt if (_binance_connected and not paper_tog) else st.session_state.initial_balance
    st.session_state.invest_mode = st.radio(
        "Investment mode",
        ["Fixed USDT", "% of Available"],
        index=0 if st.session_state.invest_mode == "Fixed USDT" else 1,
        horizontal=True,
        help="Fixed = exact USDT per trade. % = portion of Available USDT (Binance balance when LIVE, simulated when paper). Bot NEVER uses your full balance.",
    )
    if st.session_state.invest_mode == "% of Available":
        st.session_state.invest_pct = st.slider(
            "% of available per trade", 0.5, 25.0,
            float(st.session_state.invest_pct), 0.5,
            help="Capped at Hard cap below. Recommended: 1–5%.",
        )
        _effective_invest = _avail_for_calc * st.session_state.invest_pct / 100
        st.session_state.risk.invest_per_trade = round(_effective_invest, 2)
        st.caption(
            f"➜ Using **${st.session_state.risk.invest_per_trade:,.2f} USDT** per trade "
            f"({st.session_state.invest_pct:.1f}% of ${_avail_for_calc:,.2f} available)"
        )

    ma = st.number_input("Manual order (USDT)", 10.0, 100_000.0,
                          st.session_state.manual_amount, 10.0)
    st.session_state.manual_amount = ma

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Risk
    st.markdown('<div class="sec-lbl">Risk Management</div>', unsafe_allow_html=True)
    r = st.session_state.risk

    # ── Trade sizing (the single most important safety control) ────────────
    _inv_cap = min(float(r.invest_per_trade), float(r.max_trade_usdt)) if r.max_trade_usdt > 0 else float(r.invest_per_trade)
    st.markdown(f"""
<div style="background:#0d1a2a;border:1px solid #2962ff44;border-radius:7px;padding:8px 11px;margin-bottom:8px;">
  <div style="font-size:9px;color:#6e7681;text-transform:uppercase;letter-spacing:.1em;margin-bottom:3px;">Per-trade size (active)</div>
  <div style="font-size:18px;font-weight:700;color:#f0f6fc;font-family:'JetBrains Mono',monospace;">${_inv_cap:.2f} <span style="font-size:11px;color:#6e7681;">USDT</span></div>
</div>""", unsafe_allow_html=True)

    r.invest_per_trade = st.number_input(
        "Invest per trade (USDT)",
        min_value=1.0, max_value=100_000.0,
        value=float(r.invest_per_trade), step=5.0,
        help="Fixed USDT used for every trade — bot AND manual",
    )
    r.max_trade_usdt = st.number_input(
        "Hard cap per trade (USDT)",
        min_value=0.0, max_value=100_000.0,
        value=float(r.max_trade_usdt), step=10.0,
        help="Absolute maximum — trade is rejected if invest > cap. 0 = no cap.",
    )
    r.max_trades_per_session = st.number_input(
        "Max bot trades / session",
        min_value=0, max_value=500,
        value=int(r.max_trades_per_session), step=1,
        help="Bot stops opening new trades after this count. 0 = unlimited.",
    )

    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
    r.stop_loss_pct      = st.slider("Stop loss %",     0.5, 20.0, float(r.stop_loss_pct),      0.5)
    r.take_profit_pct    = st.slider("Take profit %",   0.5, 50.0, float(r.take_profit_pct),    0.5)
    r.max_daily_loss_pct = st.slider("Max daily loss %",1.0, 30.0, float(r.max_daily_loss_pct), 0.5)
    r.max_open_trades    = st.slider("Max open trades", 1,   20,   int(r.max_open_trades),      1)
    st.session_state.risk_manager.settings = r

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Data & Live Refresh
    st.markdown('<div class="sec-lbl">Data & Refresh</div>', unsafe_allow_html=True)
    _ref_opts = [5, 10, 30, 60]   # min 5s to prevent chart flicker
    _cur_ref  = max(5, int(st.session_state.refresh_secs))
    _ref_idx  = _ref_opts.index(_cur_ref) if _cur_ref in _ref_opts else 0
    _ref_choice = st.selectbox(
        "Live refresh interval",
        options=_ref_opts,
        index=_ref_idx,
        format_func=lambda x: f"{x}s",
        help="Chart and data refresh automatically at this interval",
    )
    st.session_state.refresh_secs = _ref_choice
    st.caption(f"Chart auto-refreshes every {_ref_choice}s — no manual action needed")
    if st.button("↺ Refresh Now", use_container_width=True):
        st.rerun()
    if st.button("🗑 Reset All Data", use_container_width=True):
        reset_all_data()
        st.rerun()

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # ── Telegram Notifications ──────────────────────────────────────────────
    st.markdown('<div class="sec-lbl">Telegram Notifications</div>', unsafe_allow_html=True)
    _tg_on = st.toggle(
        "Enable notifications",
        value=st.session_state.tg_enabled,
        help="Send alerts for every trade, error, and bot event to your phone",
    )
    st.session_state.tg_enabled = _tg_on

    _tg_token = st.text_input(
        "Bot Token",
        value=st.session_state.tg_token,
        type="password",
        placeholder="1234567890:ABCDef...",
        help="From @BotFather on Telegram",
    )
    _tg_cid = st.text_input(
        "Chat ID",
        value=st.session_state.tg_chat_id,
        placeholder="-100123456789  or  123456789",
        help="Your personal chat ID or a group chat ID",
    )
    if _tg_token != st.session_state.tg_token or _tg_cid != st.session_state.tg_chat_id:
        st.session_state.tg_token   = _tg_token
        st.session_state.tg_chat_id = _tg_cid

    tg.configure(
        token   = st.session_state.tg_token,
        chat_id = st.session_state.tg_chat_id,
        enabled = st.session_state.tg_enabled,
    )

    if st.button("📨 Send Test Notification", use_container_width=True):
        if not st.session_state.tg_token or not st.session_state.tg_chat_id:
            st.warning("Enter Token and Chat ID first.")
        else:
            tg.configure(st.session_state.tg_token, st.session_state.tg_chat_id, enabled=True)
            _ok, _msg = tg.test_notification()
            if _ok:
                st.success(f"✅ {_msg}")
            else:
                st.error(f"❌ {_msg}")

    if tg.is_enabled():
        st.caption("🟢 Notifications active")
    elif st.session_state.tg_token and st.session_state.tg_chat_id:
        st.caption("🔴 Toggle ON to activate")
    else:
        st.caption("Enter Token + Chat ID to enable")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — fund cards
# ─────────────────────────────────────────────────────────────────────────────
with st.container():
    _, col_main, _ = st.columns([0.005, 99.99, 0.005])
    with col_main:

        roi_cls  = "up" if roi >= 0  else "dn"
        dpnl_cls = "up" if daily_pnl >= 0 else "dn"

        _u_cls = "up" if unrealized_pnl >= 0 else "dn"
        _r_cls = "up" if realized_pnl   >= 0 else "dn"
        _bin_card_style = 'border-color:#26a69a55;' if _binance_connected else 'opacity:.55;'

        st.markdown(f"""
<div class="cards">
  <div class="card" style="{_bin_card_style}">
    <div class="c-lbl">Binance Total (USDT)</div>
    <div class="c-val">${binance_total_usdt:,.2f}</div>
    <div class="c-sub">{'🟢 Live · free + locked' if _binance_connected else 'Not connected'}</div>
  </div>
  <div class="card" style="{_bin_card_style}">
    <div class="c-lbl">Available (USDT)</div>
    <div class="c-val">${binance_free_usdt:,.2f}</div>
    <div class="c-sub">{'Free for new orders' if _binance_connected else 'Connect API key'}</div>
  </div>
  <div class="card">
    <div class="c-lbl">📋 Paper Equity</div>
    <div class="c-val">${equity:,.2f}</div>
    <div class="c-sub">Init ${st.session_state.initial_balance:,.0f} · {len(open_trades)} open</div>
  </div>
  <div class="card">
    <div class="c-lbl">Realized PnL</div>
    <div class="c-val {_r_cls}">{_fmt_pnl(realized_pnl)}</div>
    <div class="c-sub">{len(closed_trades)} closed · Win {win_rate:.1f}%</div>
  </div>
  <div class="card">
    <div class="c-lbl">Unrealized PnL</div>
    <div class="c-val {_u_cls}">{_fmt_pnl(unrealized_pnl)}</div>
    <div class="c-sub">Exposure {(sum((t.get('invested') or 0) for t in open_trades)/equity*100 if equity else 0):.1f}% · ROI {roi:+.2f}%</div>
  </div>
  <div class="card">
    <div class="c-lbl">Daily P&L</div>
    <div class="c-val {dpnl_cls}">{_fmt_pnl(daily_pnl)}</div>
    <div class="c-sub">R {_fmt_pnl(daily_realized)} · {len([t for t in closed_trades if (t.get('close_time') or '').startswith(today_str)])} today</div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── Equity Curve Sparkline ─────────────────────────────────────────────
        _cum       = 0.0
        _spark_trades = sorted(
            [t for t in closed_trades if t.get("close_time")],
            key=lambda t: t["close_time"],
        )
        # Anchor point: first trade open time, or today's start if no trades yet
        if _spark_trades:
            _t0 = datetime.fromisoformat(_spark_trades[0].get("open_time") or _spark_trades[0]["close_time"])
        else:
            _t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        _eq_times  = [_t0]
        _eq_vals   = [0.0]
        if _spark_trades:
            for _t in _spark_trades:
                _cum += _t.get("profit_loss") or 0
                _eq_times.append(datetime.fromisoformat(_t["close_time"]))
                _eq_vals.append(round(_cum, 4))
            # extend to now with current unrealized P&L
            _unreal = sum(
                ((live_price - ot["entry_price"]) / ot["entry_price"] * (ot.get("invested") or 0))
                if ot.get("side") == "BUY"
                else ((ot["entry_price"] - live_price) / ot["entry_price"] * (ot.get("invested") or 0))
                for ot in open_trades
                if live_price and ot.get("entry_price")
            )
            _eq_times.append(datetime.now())
            _eq_vals.append(round(_cum + _unreal, 4))

        _spark_color = "#26a69a" if _eq_vals[-1] >= 0 else "#ef5350"
        _spark_fill  = "rgba(38,166,154,0.08)" if _eq_vals[-1] >= 0 else "rgba(239,83,80,0.08)"

        _sfig = go.Figure()
        _sfig.add_trace(go.Scatter(
            x=_eq_times, y=_eq_vals,
            mode="lines",
            line=dict(color=_spark_color, width=2),
            fill="tozeroy", fillcolor=_spark_fill,
            hovertemplate="$%{y:+.4f}<br>%{x|%H:%M %b %d}<extra></extra>",
        ))
        # zero baseline
        _sfig.add_hline(y=0, line=dict(color="#30363d", width=1))
        # current dot
        _sfig.add_trace(go.Scatter(
            x=[_eq_times[-1]], y=[_eq_vals[-1]],
            mode="markers",
            marker=dict(color=_spark_color, size=7,
                        line=dict(color="#0a0c10", width=2)),
            showlegend=False,
            hovertemplate=f"Now: ${_eq_vals[-1]:+.4f}<extra></extra>",
        ))
        _sfig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=72,
            margin=dict(l=0, r=0, t=0, b=0),
            showlegend=False,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False, zeroline=False),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d",
                            font_color="#c9d1d9", font_size=11),
            uirevision="alphatrade-spark",
        )

        _sp_lbl = f'<span style="font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:.1em;font-weight:600;">Equity Curve</span>'
        _sp_val = f'<span style="font-size:13px;font-weight:700;font-family:\'JetBrains Mono\',monospace;color:{_spark_color};">{"+" if _eq_vals[-1]>=0 else ""}${_eq_vals[-1]:,.4f}</span>'
        _sp_cnt = f'<span style="font-size:10px;color:#484f58;">{len(_spark_trades)} closed trade{"s" if len(_spark_trades)!=1 else ""}</span>'
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:2px;">'
            f'{_sp_lbl}&nbsp;&nbsp;{_sp_val}&nbsp;&nbsp;{_sp_cnt}</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(_sfig, use_container_width=True, key="equity_spark_chart",
                        config={"displayModeBar": False, "staticPlot": False})

        # ── Chart toolbar ─────────────────────────────────────────────────────
        # Amount row (compact, above BUY/SELL)
        _amt_col1, _amt_col2 = st.columns([5, 2])
        with _amt_col1:
            st.markdown(
                '<div style="height:6px;"></div>',
                unsafe_allow_html=True,
            )
        with _amt_col2:
            _new_amt = st.number_input(
                "Amount (USDT)",
                min_value=1.0, max_value=1_000_000.0,
                value=float(st.session_state.manual_amount),
                step=10.0,
                label_visibility="collapsed",
                help="Trade size in USDT for manual BUY/SELL",
                key="toolbar_amount",
            )
            st.session_state.manual_amount = _new_amt

        tb1, tb2, tb3, tb4, tb5, tb6 = st.columns([4, 1, 1, 1, 1, 1])
        with tb1:
            mode_badge = ('<span class="cbadge green">📋 PAPER</span>'
                          if st.session_state.paper_mode
                          else '<span class="cbadge red">⚡ LIVE</span>')
            src_note = "live • public API" if chart_source == "public" else "live • auth"
            # Last signal summary for chart bar
            _ls = get_bot_last_signal()
            _ls_msg = (_ls.get("message","") or "")
            if "BUY" in _ls_msg:
                _sig_badge = '<span class="cbadge green">▲ BUY SIGNAL</span>'
            elif "SELL" in _ls_msg:
                _sig_badge = '<span class="cbadge red">▼ SELL SIGNAL</span>'
            elif _ls_msg:
                _sig_badge = f'<span class="cbadge" style="color:#6e7681;">HOLD</span>'
            else:
                _sig_badge = ""
            _bot_run_badge = (
                '<span class="cbadge" style="color:#e3b341;background:#1e1a0a;">⚡ BOT ON</span>'
                if bot_running else
                '<span class="cbadge" style="color:#484f58;">BOT OFF</span>'
            )
            st.markdown(f"""
<div class="chart-bar">
  <div class="chart-title">
    <span>{st.session_state.symbol}</span>
    <span class="cbadge blue">{st.session_state.interval}</span>
    <span class="cbadge gold">{st.session_state.strategy}</span>
    {mode_badge}
    {_bot_run_badge}
    {_sig_badge}
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
                        b._initial_balance = st.session_state.initial_balance
                        b.start()
                st.rerun()
        with tb6:
            if st.button("↺", use_container_width=True, help="Refresh"):
                st.rerun()

        # ── Live-trade confirmation dialog ────────────────────────────────────
        _plt = st.session_state.pending_live_trade
        if _plt:
            _plt_side = _plt["side"]
            _plt_inv  = _plt["invested"]
            _plt_pr   = _plt["price"]
            _plt_qty  = _plt["qty"]
            _side_color = "#26a69a" if _plt_side == "BUY" else "#ef5350"
            st.markdown(f"""
<div style="background:#1a0a0a;border:1px solid #ef5350;border-radius:8px;padding:12px 16px;margin:8px 0;">
  <div style="font-size:11px;color:#ef5350;font-weight:700;margin-bottom:6px;">⚡ LIVE ORDER CONFIRMATION — REAL MONEY</div>
  <div style="font-size:13px;color:#f0f6fc;font-family:'JetBrains Mono',monospace;">
    <span style="color:{_side_color};font-weight:700;">{_plt_side}</span>&nbsp;
    {_plt_qty:.6f} {st.session_state.symbol} &nbsp;·&nbsp;
    ${_plt_pr:,.4f} &nbsp;·&nbsp; ${_plt_inv:.2f} USDT
  </div>
</div>""", unsafe_allow_html=True)
            _cf1, _cf2 = st.columns(2)
            with _cf1:
                if st.button("✅ Confirm LIVE trade", use_container_width=True, type="primary"):
                    # Re-check gate at confirm time (defence in depth)
                    if (not paper_verified()[0]) or st.session_state.paper_mode or st.session_state.testnet:
                        st.error("🔒 LIVE trade blocked — paper verification required and paper/testnet must be OFF.")
                        st.session_state.pending_live_trade = None
                        st.rerun()
                    _do_live = True
                    c = _cl()
                    try:
                        order = c.place_market_order(st.session_state.symbol, _plt_side, _plt_qty)
                        _exec_price = float(order.get("fills", [{}])[0].get("price", _plt_pr))
                    except Exception as _e:
                        st.error(f"Order failed: {_e}")
                        _do_live = False
                        _exec_price = _plt_pr
                    if _do_live:
                        rm = st.session_state.risk_manager
                        _t = {
                            "coin":            st.session_state.symbol,
                            "exchange":        "Binance Testnet" if st.session_state.testnet else "Binance LIVE",
                            "type":            "manual",
                            "strategy":        "Manual",
                            "side":            _plt_side,
                            "entry_price":     _exec_price,
                            "exit_price":      None,
                            "quantity":        _plt_qty,
                            "invested":        _plt_inv,
                            "profit_loss":     None,
                            "profit_loss_pct": None,
                            "open_time":       datetime.now(_TZ).isoformat(),
                            "close_time":      None,
                            "reason":          f"Manual {_plt_side} LIVE — ${_plt_inv:.2f} USDT @ ${_exec_price:.4f}",
                            "close_reason":    None,
                            "stop_loss":       rm.stop_loss_price(_exec_price, _plt_side),
                            "take_profit":     rm.take_profit_price(_exec_price, _plt_side),
                            "status":          "open",
                            "paper":           False,
                        }
                        add_trade(_t)
                        log_activity("ORDER",
                            f"⚡ LIVE {_plt_side} | {_plt_qty:.6f} {st.session_state.symbol} @ ${_exec_price:.4f}")
                        tg.trade_open(st.session_state.symbol, _plt_side, _exec_price, _plt_inv, "Manual trade", mode="LIVE")
                    st.session_state.pending_live_trade = None
                    st.rerun()
            with _cf2:
                if st.button("✕ Cancel", use_container_width=True):
                    st.session_state.pending_live_trade = None
                    st.rerun()

        # Manual trade execution
        if buy_btn or sell_btn:
            side = "BUY" if buy_btn else "SELL"
            price = live_price
            if price is None:
                st.error("No price available — chart not loaded yet.")
            else:
                c = _cl()
                invested = st.session_state.manual_amount
                # Enforce hard cap
                _cap = st.session_state.risk.max_trade_usdt
                if _cap > 0 and invested > _cap:
                    st.warning(f"⚠️ Amount ${invested:.2f} exceeds hard cap ${_cap:.2f} — capped automatically.")
                    invested = _cap
                qty = invested / price

                if c is not None:
                    try:
                        qty = c.round_quantity(st.session_state.symbol, qty)
                    except Exception:
                        qty = round(qty, 6)

                    # LIVE mode → stage confirmation; paper/testnet → execute immediately
                    if not st.session_state.paper_mode and not st.session_state.testnet:
                        if not paper_verified()[0]:
                            st.error("🔒 LIVE trading locked — close ≥3 paper trades first.")
                            st.session_state.paper_mode = True
                            st.rerun()
                        st.session_state.pending_live_trade = {
                            "side": side, "invested": invested,
                            "price": price, "qty": qty,
                        }
                        st.rerun()
                else:
                    qty = round(qty, 6)

                # Paper mode (or testnet with auth): execute without confirmation
                if not st.session_state.pending_live_trade:
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
                        "open_time":       datetime.now(_TZ).isoformat(),
                        "close_time":      None,
                        "reason":          f"Manual {side} ({'Paper' if st.session_state.paper_mode else 'Testnet'}) — ${invested:.2f} USDT @ ${price:.4f}",
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
                    _m_mode = "PAPER" if st.session_state.paper_mode else "TESTNET"
                    tg.trade_open(st.session_state.symbol, side, price, invested, "Manual trade", mode=_m_mode)
                    st.success(f"✅ {side} @ ${price:.4f} — ID: {added['id']}")

        if emg_btn:
            st.session_state.risk.emergency_stop = True
            bot_module.stop_bot()
            log_activity("WARNING", "🚨 EMERGENCY STOP activated")
            st.rerun()

        # ── Chart (3-panel: Candles+EMA | Stochastic | RSI) ───────────────────
        # ── Active Levels strip (replaces in-chart annotations) ─────────────────
        if df_chart is not None and len(df_chart) > 5:
            _lvl_bits = []
            if live_price:
                _lc = "#26a69a" if change_pct >= 0 else "#ef5350"
                _lvl_bits.append(
                    f'<span style="color:#6e7681;">PRICE</span> '
                    f'<span style="color:{_lc};font-weight:700;">${live_price:,.4f}</span>'
                )
            for _op in open_trades:
                _sid = _op.get("side","BUY"); _tid = (_op.get("id") or "?")[:6]
                _sl = _op.get("stop_loss"); _tp = _op.get("take_profit")
                if _sl or _tp:
                    _side_col = "#26a69a" if _sid == "BUY" else "#ef5350"
                    _lvl_bits.append(
                        f'<span style="color:#6e7681;">{_tid}</span> '
                        f'<span style="color:{_side_col};font-weight:600;">{_sid}</span> '
                        + (f'<span style="color:#ef5350;">SL ${_sl:,.4f}</span> ' if _sl else '')
                        + (f'<span style="color:#26a69a;">TP ${_tp:,.4f}</span>' if _tp else '')
                    )
            if _lvl_bits:
                st.markdown(
                    '<div style="display:flex;flex-wrap:wrap;gap:14px;padding:6px 10px;'
                    'background:#0d1117;border:1px solid #1a2030;border-radius:6px;'
                    "margin:4px 0 8px 0;font-size:11px;font-family:'JetBrains Mono',monospace;\">"
                    + " · ".join(_lvl_bits) + "</div>",
                    unsafe_allow_html=True,
                )

            _has_rsi = "rsi" in df_chart.columns

            fig = make_subplots(
                rows=3 if _has_rsi else 2, cols=1,
                shared_xaxes=True,
                row_heights=[0.60, 0.20, 0.20] if _has_rsi else [0.72, 0.28],
                vertical_spacing=0.015,
                subplot_titles=("", "Stochastic", "RSI 14") if _has_rsi else ("", "Stochastic"),
            )

            # ── Candlestick ────────────────────────────────────────────────────
            fig.add_trace(go.Candlestick(
                x=df_chart["open_time"],
                open=df_chart["open"], high=df_chart["high"],
                low=df_chart["low"],   close=df_chart["close"],
                name=st.session_state.symbol,
                increasing_line_color="#26a69a", increasing_fillcolor="#26a69a",
                decreasing_line_color="#ef5350", decreasing_fillcolor="#ef5350",
                line=dict(width=1), whiskerwidth=0,
            ), row=1, col=1)

            # ── EMA 9 ──────────────────────────────────────────────────────────
            if "ema9" in df_chart.columns:
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["ema9"],
                    line=dict(color="#ef5350", width=1.5),
                    name="EMA 9",
                    hovertemplate="EMA9: %{y:.4f}<extra></extra>",
                ), row=1, col=1)

            # ── EMA 21 ─────────────────────────────────────────────────────────
            if "ema21" in df_chart.columns:
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["ema21"],
                    line=dict(color="#e3b341", width=1.5),
                    name="EMA 21",
                    hovertemplate="EMA21: %{y:.4f}<extra></extra>",
                ), row=1, col=1)

            # ── Volume histogram (scaled to bottom 18% of price panel) ──────────
            if "volume" in df_chart.columns:
                _prange = df_chart["high"].max() - df_chart["low"].min()
                _vmax   = df_chart["volume"].max()
                if _prange > 0 and _vmax > 0:
                    _vscaled = df_chart["volume"] / _vmax * _prange * 0.18
                    _vbase   = df_chart["low"].min() - _prange * 0.02
                    _vcols   = [
                        "rgba(38,166,154,0.40)" if df_chart["close"].iloc[i] >= df_chart["open"].iloc[i]
                        else "rgba(239,83,80,0.40)"
                        for i in range(len(df_chart))
                    ]
                    fig.add_trace(go.Bar(
                        x=df_chart["open_time"],
                        y=_vscaled,
                        base=_vbase,
                        name="Volume",
                        marker_color=_vcols,
                        marker_line_width=0,
                        showlegend=False,
                        customdata=df_chart["volume"],
                        hovertemplate="Vol: %{customdata:,.0f}<extra></extra>",
                    ), row=1, col=1)

            # ── Live price line (NO inline text — info shown in strip above chart) ─
            if live_price:
                p_color = "#26a69a" if change_pct >= 0 else "#ef5350"
                fig.add_hline(
                    y=live_price, row=1, col=1,
                    line=dict(color=p_color, width=1, dash="dot"),
                )

            # ── Trade markers ──────────────────────────────────────────────────
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
                    k = ("mb" if side == "BUY" else "ms") if ttype == "manual" else ("bb" if side == "BUY" else "bs")
                    buckets[k][0].append(ts)
                    buckets[k][1].append(ep)
                    if t.get("exit_price") and t.get("close_time"):
                        xk = "mx" if ttype == "manual" else "bx"
                        buckets[xk][0].append(pd.to_datetime(t["close_time"]))
                        buckets[xk][1].append(t["exit_price"])
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

            # ── SL / TP lines for every open position ─────────────────────────
            for _op in open_trades:
                _ep  = _op.get("entry_price")
                _sl  = _op.get("stop_loss")
                _tp  = _op.get("take_profit")
                _sid = _op.get("side","BUY")
                _tid = _op.get("id","?")
                if _sl:
                    fig.add_hline(
                        y=_sl, row=1, col=1,
                        line=dict(color="rgba(239,83,80,0.55)", width=1, dash="dash"),
                    )
                if _tp:
                    fig.add_hline(
                        y=_tp, row=1, col=1,
                        line=dict(color="rgba(38,166,154,0.55)", width=1, dash="dash"),
                    )

            # ── Stochastic (row 2) ─────────────────────────────────────────────
            if "stoch_k" in df_chart.columns:
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["stoch_k"],
                    line=dict(color="#79b0ff", width=1.5), name="%K",
                    hovertemplate="%K: %{y:.1f}<extra></extra>",
                ), row=2, col=1)
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["stoch_d"],
                    line=dict(color="#c9d1d9", width=1.2, dash="dot"), name="%D",
                    hovertemplate="%D: %{y:.1f}<extra></extra>",
                ), row=2, col=1)
                fig.add_hline(y=80, line=dict(color="rgba(239,83,80,0.38)",   width=1, dash="dash"), row=2, col=1)
                fig.add_hline(y=20, line=dict(color="rgba(38,166,154,0.38)",  width=1, dash="dash"), row=2, col=1)
                fig.add_hrect(y0=80, y1=100, fillcolor="rgba(239,83,80,0.06)",  line_width=0, row=2, col=1)
                fig.add_hrect(y0=0,  y1=20,  fillcolor="rgba(38,166,154,0.06)", line_width=0, row=2, col=1)

            # ── RSI (row 3) ────────────────────────────────────────────────────
            if _has_rsi:
                _rsi_color = df_chart["rsi"].apply(
                    lambda v: "#ef5350" if v > 70 else ("#26a69a" if v < 30 else "#79b0ff")
                )
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["rsi"],
                    line=dict(color="#79b0ff", width=1.5), name="RSI 14",
                    fill="tozeroy", fillcolor="rgba(121,176,255,0.05)",
                    hovertemplate="RSI: %{y:.1f}<extra></extra>",
                ), row=3, col=1)
                fig.add_hline(y=70, line=dict(color="rgba(239,83,80,0.38)",   width=1, dash="dash"), row=3, col=1)
                fig.add_hline(y=30, line=dict(color="rgba(38,166,154,0.38)",  width=1, dash="dash"), row=3, col=1)
                fig.add_hline(y=50, line=dict(color="rgba(72,80,88,0.19)",    width=1, dash="dot"),  row=3, col=1)
                fig.add_hrect(y0=70, y1=100, fillcolor="rgba(239,83,80,0.06)",  line_width=0, row=3, col=1)
                fig.add_hrect(y0=0,  y1=30,  fillcolor="rgba(38,166,154,0.06)", line_width=0, row=3, col=1)

            # ── Layout ─────────────────────────────────────────────────────────
            G = "#1a2030"
            n_rows = 3 if _has_rsi else 2
            fig.update_layout(
                paper_bgcolor="#0a0c10",
                plot_bgcolor="#0a0c10",
                font=dict(color="#6e7681", family="'JetBrains Mono',monospace", size=10),
                xaxis_rangeslider_visible=False,
                height=700,
                # Right margin shrunk (no in-chart legend); bottom expanded for legend below
                margin=dict(l=0, r=64, t=18, b=72),
                showlegend=True,
                # Legend BELOW chart (horizontal) — never overlays candles
                legend=dict(
                    bgcolor="rgba(13,17,23,0.0)",
                    bordercolor="rgba(0,0,0,0)", borderwidth=0,
                    font=dict(size=10, color="#9ba3ad"),
                    orientation="h",
                    yanchor="top", y=-0.10,
                    xanchor="center", x=0.5,
                    itemsizing="constant",
                ),
                hovermode="x unified",
                hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d",
                                font_color="#c9d1d9", font_size=11),
                # Stable uirevision — Plotly preserves zoom/pan/selection across rerenders
                uirevision="alphatrade-main-chart",
            )
            for r in range(1, n_rows + 1):
                fig.update_xaxes(
                    gridcolor=G, gridwidth=1, zerolinecolor=G,
                    showspikes=True, spikecolor="#484f58", spikethickness=1,
                    tickfont=dict(size=10), row=r, col=1,
                    # Exact HH:MM:SS Europe/London time (klines already tz-converted in binance_client)
                    tickformat="%H:%M:%S",
                    hoverformat="%Y-%m-%d %H:%M:%S",
                )
            fig.update_yaxes(
                gridcolor=G, gridwidth=1, zerolinecolor=G,
                showspikes=True, spikecolor="#484f58",
                tickfont=dict(size=10), tickprefix="$",
                row=1, col=1,
            )
            fig.update_yaxes(
                gridcolor=G, gridwidth=1, range=[0, 100],
                tickfont=dict(size=10), row=2, col=1,
            )
            if _has_rsi:
                fig.update_yaxes(
                    gridcolor=G, gridwidth=1, range=[0, 100],
                    tickfont=dict(size=10), row=3, col=1,
                )
            # Subplot title styling
            for ann in fig.layout.annotations:
                ann.font.color = "#484f58"
                ann.font.size  = 9

            # Stable key → Streamlit reuses the same DOM node across reruns (no flicker / no remount)
            st.plotly_chart(fig, use_container_width=True, key="main_candle_chart",
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
                # Prefer cached per-position unrealized (uses correct symbol's price)
                _u_cached = ot.get("_unrealized")
                _cp_cached = ot.get("_cur_price")
                if _u_cached is not None and _cp_cached:
                    uc = "#26a69a" if _u_cached >= 0 else "#ef5350"
                    _u_pct = (_u_cached / (ot.get("invested") or 1)) * 100
                    upnl_html = (f'<span style="color:{uc};font-weight:700;">{_fmt_pnl(_u_cached)}</span> '
                                 f'<span style="color:{uc};font-size:10px;opacity:.75;">({_u_pct:+.2f}%)</span>')
                elif live_price and ep:
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

# ── Auto-refresh (silent JS-driven, no page-stall flash) ─────────────────────
# Uses streamlit-autorefresh: a JS setInterval that triggers a rerun WITHOUT
# blocking the python process. This is the only correct way — the old
# `time.sleep(N); st.rerun()` loop caused the entire page to flash + duplicate
# render at the bottom because the websocket was stalled during sleep.
from streamlit_autorefresh import st_autorefresh
_refresh_ms = max(5, int(st.session_state.get("refresh_secs", 5))) * 1000
st_autorefresh(interval=_refresh_ms, key="alphatrade_autorefresh", limit=None)
