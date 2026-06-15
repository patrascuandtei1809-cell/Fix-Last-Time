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


def _to_london(val):
    """Coerce any stored timestamp (ISO string or datetime, naive=server-local
    or tz-aware) to an Europe/London-aware datetime. DISPLAY-ONLY — never alters
    stored data or backend behavior. A naive value is assumed to be in the
    server's local clock (same clock the bot used to write it) and converted to
    London via .astimezone(_TZ), so this is correct regardless of the server tz."""
    if val is None or val == "":
        return None
    dt = val
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if not isinstance(dt, datetime):
        return None
    try:
        return dt.astimezone(_TZ)
    except Exception:
        return dt


def _fmt_london(val, fmt="%H:%M:%S"):
    """Format any stored timestamp as Europe/London wall-clock text."""
    dt = _to_london(val)
    return dt.strftime(fmt) if dt else "—"

# ── Path ──────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import bot as bot_module
import aggressive_mode as am
import live_settings
import live_engine
import diagnostics
from bot import (
    load_trades, load_activity, get_open_trades,
    add_trade, close_trade, reset_all_data, clear_activity, log_activity,
    get_shared_df, get_shared_price, get_shared_updated_at, get_shared_last_tick,
    get_bot_session_trades, get_bot_last_signal, get_bot_signal_meta,
    get_bot_diagnostics, save_settings, get_all_symbol_state,
)
from strategy import get_indicators
from chart_markers import build_trade_markers
from risk import RiskManager, RiskSettings, GlobalRiskSettings, GlobalRiskManager
import telegram_notifier as tg
from binance_client import public_klines, public_price, public_24h

# ── Real commission → USDT (P2) ─────────────────────────────────────────────
# Manual trades hit the raw Binance client (place_market_order) which returns the
# raw order dict. Convert its commission(s) to USDT so manual trades capture the
# SAME real entry_fee/exit_fee the bot path records. Fails open to 0.0 (never
# blocks a trade) — a missing fee is handled honestly downstream (fees_complete).
_FEE_STABLES = ("USDT", "BUSD", "USDC", "FDUSD", "TUSD", "DAI")


def _order_fee_usdt(order: dict, coin: str, fill_price: float) -> float:
    """Sum an order's real commissions, converted to USDT. 0.0 if unknown."""
    try:
        from binance_client import extract_fees as _extract_fees
        detail = _extract_fees(order) or {}
    except Exception:
        return 0.0
    total = 0.0
    for asset, amt in detail.items():
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        if asset in _FEE_STABLES:
            total += amt
        elif coin.startswith(asset) and fill_price > 0:
            total += amt * fill_price          # base asset → USDT via fill price
        else:
            try:
                total += amt * public_price(f"{asset}USDT")
            except Exception:
                pass                            # unknown asset → skip (fail open)
    return total


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

/* ── ANTI-FLICKER: suppress Streamlit's per-rerun status indicators ──────── */
/* These are the small banners/spinners that appear top-right on every script
   rerun and are the actual visible "flash" the user perceives. */
[data-testid="stStatusWidget"] { display: none !important; }
[data-testid="stToolbar"]      { visibility: hidden !important; height: 0 !important; }
[data-testid="stDecoration"]   { display: none !important; }
[data-testid="stHeader"]       { background: transparent !important; }
div[data-testid="stConnectionStatus"] { display: none !important; }
/* Hide the small "Running..." spinner overlay */
.stSpinner > div { background: transparent !important; }
/* Keep the page from briefly going blank during rerun */
.main .block-container { transition: none !important; }

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
/* text-shadow removed — caused ghosting on rerun */
.c-val.up { color: #26a69a; }
.c-val.dn { color: #ef5350; }
.p-green { box-shadow: 0 0 12px -2px rgba(38,166,154,0.30); }
.p-red   { box-shadow: 0 0 12px -2px rgba(239,83,80,0.30); }
.p-gold  { box-shadow: 0 0 12px -2px rgba(227,179,65,0.25); }
.cbadge.green { box-shadow: 0 0 10px -3px rgba(63,185,80,0.45); }
.cbadge.red   { box-shadow: 0 0 10px -3px rgba(239,83,80,0.45); }
/* button transform removed — caused layout jitter on rerun */
.stButton>button { transition: border-color .15s ease, background-color .15s ease !important; }
.stButton>button:hover { border-color: #2962ff88 !important; }
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

/* ════════════════════════════════════════════════════════════════════════════
   MOBILE LAYOUT (≤ 768px)
   Goals:
   - Header pills (CONNECTED, BOT ON, LIVE, balance) always visible & legible
   - Sidebar (API connect, strategy, risk) wide enough to use one-handed
   - No horizontal overflow; single-column card grid
   - Bigger tap targets for buttons / sliders / inputs
   - Charts, log, position cards fit phone width without clipping
   ════════════════════════════════════════════════════════════════════════════ */
@media (max-width: 768px) {

  /* Base — slightly larger body text on phone */
  html, body { font-size: 15px !important; }
  [data-testid="stAppViewContainer"] { overflow-x: hidden !important; }
  .main .block-container { padding: 0 6px !important; }

  /* ── Header: stack into 2 rows (logo+ticker, then pills) ──────────────── */
  .at-header {
    height: auto !important;
    padding: 8px 10px !important;
    gap: 6px !important;
    flex-direction: column !important;
    align-items: stretch !important;
  }
  .at-logo { font-size: 16px !important; }
  .at-ticker-wrap {
    justify-content: space-between !important;
    width: 100% !important;
    gap: 8px !important;
  }
  .at-price { font-size: 18px !important; }
  .at-stat  { font-size: 10px !important; }
  /* Pills row: horizontally scrollable so they never clip but always all
     visible (CONNECTED / BOT ON / LIVE / MAINNET / refresh badge) */
  .pills {
    width: 100% !important;
    flex-wrap: nowrap !important;
    overflow-x: auto !important;
    padding-bottom: 2px !important;
    scrollbar-width: none !important;
  }
  .pills::-webkit-scrollbar { display: none !important; }
  .pill {
    font-size: 11px !important;
    padding: 4px 9px !important;
    flex-shrink: 0 !important;
  }

  /* ── Market overview strip ── */
  .mkt-strip  { padding: 6px 8px !important; }
  .mkt-tile   { min-width: 96px !important; padding: 6px 10px !important; }

  /* ── Cards: single column on phone ── */
  .cards { grid-template-columns: 1fr !important; gap: 8px !important; }
  .card  { padding: 12px 14px !important; }
  .c-val { font-size: 18px !important; }

  /* ── Chart toolbar wraps cleanly ── */
  .chart-bar { flex-direction: column !important; align-items: flex-start !important; }

  /* ── Open position cards: stack pieces vertically ── */
  .pos-card {
    flex-direction: column !important;
    align-items: flex-start !important;
    font-size: 12.5px !important;
    padding: 10px 12px !important;
  }

  /* ── Activity log: taller, easier to scroll on phone ── */
  .log-wrap  { height: 280px !important; font-size: 12px !important; }
  .log-line  { padding: 6px 10px !important; line-height: 1.5 !important; }
  .l-ts      { min-width: 60px !important; }
  .l-lvl     { min-width: 48px !important; }

  /* ── Bot stat grid (sidebar) ── */
  .bot-stat-grid { gap: 6px !important; }
  .bsc-val       { font-size: 15px !important; }

  /* ── Sidebar: wider on phone so inputs are usable ──────────────────────
     Streamlit's default mobile sidebar is ~21rem; bump to 88vw so
     sliders, API key inputs, and risk controls all fit on a phone.
     Critical: this is where the user connects Binance and tunes risk. */
  section[data-testid="stSidebar"] {
    width: 88vw !important;
    min-width: 88vw !important;
    max-width: 92vw !important;
  }
  section[data-testid="stSidebar"] > div { padding: 8px 12px !important; }

  /* ── Larger tap targets for ALL controls ── */
  .stButton > button {
    min-height: 44px !important;
    font-size: 14px !important;
    padding: 10px 14px !important;
  }
  .stTextInput input, .stNumberInput input, .stTextArea textarea {
    font-size: 15px !important;
    min-height: 42px !important;
  }
  .stSelectbox div[data-baseweb="select"] > div { min-height: 42px !important; }
  [data-testid="stSlider"] { padding: 6px 0 !important; }
  [data-testid="stSlider"] [role="slider"] {
    width: 22px !important; height: 22px !important;
  }

  /* ── Section labels a touch bigger ── */
  .sec-lbl { font-size: 11px !important; margin: 14px 0 6px !important; }

  /* ── Multiselect / radio chips: wrap and breathe ── */
  [data-baseweb="tag"] { font-size: 12px !important; }

  /* ── Plotly chart: prevent it from forcing horizontal scroll ── */
  .js-plotly-plot, .plot-container, .svg-container {
    max-width: 100% !important;
    width: 100% !important;
  }

  /* ── Hint banner shown only on phones: tells user where the controls are ── */
  .mob-hint {
    display: flex !important;
    align-items: center; gap: 8px;
    background: #0d1a2a; border: 1px solid #2962ff44; border-radius: 6px;
    padding: 8px 12px; margin: 6px 0;
    font-size: 12px; color: #79b0ff;
  }
}

/* Hide the mobile-only hint banner on desktop */
.mob-hint    { display: none; }
.mob-summary { display: none; }

/* ── Mobile-only always-visible summary strip ─────────────────────────────── */
@media (max-width: 768px) {
  .mob-summary {
    display: grid !important;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    padding: 8px 10px;
    background: #0d1117;
    border-bottom: 1px solid #1e2736;
  }
  .mob-cell {
    background: #161b22;
    border: 1px solid #1e2736;
    border-radius: 6px;
    padding: 6px 9px;
    min-width: 0;
  }
  .mob-lbl {
    font-size: 9px; color: #6e7681; font-weight: 600;
    text-transform: uppercase; letter-spacing: .1em;
    margin-bottom: 2px;
  }
  .mob-val {
    font-size: 13px; font-weight: 700; color: #f0f6fc;
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .mob-val.up   { color: #26a69a; }
  .mob-val.dn   { color: #ef5350; }
  .mob-val.gray { color: #8b949e; }
}

/* ── MOBILE LAYOUT: no horizontal overflow, controls fit, buttons stack ── */
html, body, [data-testid="stAppViewContainer"] { overflow-x: hidden !important; max-width: 100vw; }
@media (max-width: 768px) {
  /* Kill horizontal scroll everywhere */
  .main .block-container { padding-left: .6rem !important; padding-right: .6rem !important;
    max-width: 100vw !important; overflow-x: hidden !important; }
  [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .35rem !important; }
  /* Each column takes full width and stacks vertically */
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    flex: 1 1 100% !important; width: 100% !important; min-width: 0 !important; }
  /* Buttons full-width + tappable */
  .stButton > button { width: 100% !important; }
  /* Charts / plotly / dataframes never exceed viewport */
  .js-plotly-plot, .plotly, [data-testid="stPlotlyChart"],
  [data-testid="stDataFrame"], [data-testid="stTable"], img {
    max-width: 100% !important; }
  [data-testid="stPlotlyChart"] > div { width: 100% !important; }
  /* Market strip + metric tiles wrap instead of scrolling sideways */
  .mkt-strip { flex-wrap: wrap !important; overflow-x: hidden !important; }
  [data-testid="stMetric"] { min-width: 0 !important; }
  /* Tables scroll inside their own container, not the whole page */
  [data-testid="stDataFrame"] > div { overflow-x: auto !important; }
}
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
def _init():
    defaults = {
        "client":           None,
        "connected":        False,
        "creds_from_disk":  False,    # True when api_key/secret came from backend file
        "api_key":          "",
        "api_secret":       "",
        "symbol":           "BTCUSDT",   # currently-viewed symbol (chart, manual trade)
        "active_symbols":   ["BTCUSDT", "ETHUSDT", "SOLUSDT"], # symbols the bot trades on (max 3)
        "per_symbol_risk":  {},          # {symbol: RiskSettings} — overrides global risk
        "global_risk":      None,        # GlobalRiskSettings (built lazily below)
        # ACTIVE SCALPER MODE — single hardcoded profile (no dropdowns).
        "strategy":         "Active Scalper",
        "interval":         "1m",                # 1-minute candles
        "check_every":      2,                   # 2s tick — ACTIVE SCALPER spec
        "threshold":        0.01,                # 0.01% — ACTIVE SCALPER spec
        "risk":             RiskSettings(),
        "risk_manager":     RiskManager(),
        "initial_balance":  1000.0,
        "manual_amount":    10.0,                # $10–$20 per trade
        "ai_assist":        True,                # AI always on (advisory, never blocks)
        "ai_aggressiveness": "Active Scalper",   # ignored — single mode
        "aggressive_mode":  am.DEFAULT_MODE,     # Conservative/Balanced/Aggressive/Very Aggressive (PG-persisted)
        "refresh_secs":     5,
        "alert_open_ids":      [],
        "alert_closed_ids":    [],
        "pending_live_trade":  None,   # dict stored between reruns for live confirmation
        "tg_enabled":          False,
        "tg_token":            "",
        "tg_chat_id":          "",
        # Chart indicator toggles — ALL ON by default; persisted in settings.json.
        "show_ema":            True,
        "show_volume":         True,
        "show_rsi":            True,
        "show_macd":           True,
        "show_stoch":          True,
        "show_old_trades":     True,
        "show_sl_tp":          True,
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

    # ── Lazy-init global risk settings ────────────────────────────────────────
    if st.session_state.get("global_risk") is None:
        st.session_state.global_risk = GlobalRiskSettings()

    # ── Re-apply Telegram config on every cold-start ─────────────────────────
    tg.configure(
        token   = st.session_state.get("tg_token",   ""),
        chat_id = st.session_state.get("tg_chat_id", ""),
        enabled = st.session_state.get("tg_enabled", False),
    )

    # ── Auto-load persisted Binance credentials (server-side, chmod 600) ─────
    # Only on first cold-start when session has no key yet — survives both
    # browser refresh and Streamlit server restart.
    # The Disconnect button sets `manual_disconnect=True` so we DO NOT
    # immediately auto-reload from disk on the very next rerun. The user must
    # press Clear (which removes the file) or refresh the browser tab to
    # re-enable auto-load.
    if not st.session_state.get("api_key") and not st.session_state.get("manual_disconnect"):
        try:
            from secrets_store import load_credentials
            _saved = load_credentials()
        except Exception as _e:
            _saved = None
            print(f"[CREDS] load_credentials failed: {_e}", flush=True)
        if _saved:
            _k, _s = _saved
            st.session_state.api_key        = _k
            st.session_state.api_secret     = _s
            st.session_state.creds_from_disk = True
            print(f"[CREDS] Auto-loaded LIVE creds (key={_k[:6]}…) — testing connection…",
                  flush=True)
            try:
                from binance_client import BinanceClient
                _c = BinanceClient(_k, _s)
                _ok, _msg = _c.test_connection()
                if _ok:
                    st.session_state.client    = _c
                    st.session_state.connected = True
                    print(f"[CREDS] Auto-connected LIVE — {_msg}", flush=True)
                else:
                    st.session_state.client    = None
                    st.session_state.connected = False
                    st.session_state["_auto_conn_err"] = _msg
                    print(f"[CREDS] Auto-connect FAILED — {_msg}", flush=True)
            except Exception as _e:
                st.session_state.client    = None
                st.session_state.connected = False
                st.session_state["_auto_conn_err"] = str(_e)
                print(f"[CREDS] Auto-connect exception: {_e}", flush=True)

_init()

# ── Auto-refresh (silent JS-driven, no page-stall flash) ─────────────────────
# streamlit-autorefresh = JS setInterval → triggers rerun WITHOUT blocking python.
# Minimum 5s. MUST render EARLY (right after _init populates refresh_secs): if the
# autorefresh iframe is the LAST element on the page, the browser scrolls down to
# it on every refresh → the page "jumps to the bottom" on its own. Rendering it at
# the top keeps the scroll position stable.
from streamlit_autorefresh import st_autorefresh
_refresh_ms = max(5, int(st.session_state.get("refresh_secs", 5))) * 1000
st_autorefresh(interval=_refresh_ms, key="alphatrade_autorefresh", limit=None)


def _maybe_resume_bot():
    """If `bot_was_running` was persisted (user had bot ON before the server
    restarted) AND we just auto-reconnected the LIVE client, rebuild + start
    the bot using the persisted settings. This makes the bot refresh-proof
    and independent of UI lifecycle.
    """
    import bot as _bm
    if _bm.get_bot() and _bm.get_bot().is_running():
        return
    if not st.session_state.get("client"):
        return
    try:
        cfg = _bm.load_settings() or {}
    except Exception:
        return
    # ACTIVE SCALPER MODE: auto-start the bot whenever the LIVE client is
    # connected — no longer gated on bot_was_running. Operator can stop with
    # the ⏹ Stop button; stop sets a session flag so we don't re-launch.
    if st.session_state.get("_user_stopped_bot"):
        return
    syms = cfg.get("active_symbols") or st.session_state.active_symbols
    if not syms:
        return
    # Rebuild per-symbol risk managers from persisted overrides
    _per_sym_rm = {}
    for _s in syms:
        _ov = st.session_state.per_symbol_risk.get(_s)
        _per_sym_rm[_s] = RiskManager(_ov) if _ov else st.session_state.risk_manager
    _global_rm = GlobalRiskManager(st.session_state.global_risk)
    try:
        b = _bm.create_bot(
            client            = st.session_state.client,
            symbols           = syms,
            per_symbol_risk   = _per_sym_rm,
            global_risk       = _global_rm,
            strategy          = st.session_state.strategy,
            risk_manager      = st.session_state.risk_manager,
            interval          = st.session_state.interval,
            check_every       = st.session_state.check_every,
            threshold         = float(st.session_state.threshold) / 100,
            initial_balance   = st.session_state.initial_balance,
            ai_assist         = True,                 # ACTIVE SCALPER — always on
            ai_aggressiveness = "Active Scalper",     # ignored — single mode
            manage_manual_trades = bool(getattr(st.session_state.global_risk,
                                                "manage_manual_trades", False)),
        )
        am.apply_profile_to_bot(b, st.session_state.get("aggressive_mode", am.DEFAULT_MODE))
        b._initial_balance = st.session_state.initial_balance
        b.start()
        st.session_state.bot_was_running = True
        print(f"[BOT] Auto-started ACTIVE SCALPER bot — symbols={syms}",
              flush=True)
    except Exception as _e:
        print(f"[BOT] Auto-resume failed: {_e}", flush=True)


# ── Load persisted settings from disk (survives restart) ─────────────────────
# Only runs once per session; user changes auto-save at bottom of script.
if not st.session_state.get("_settings_loaded"):
    from bot import load_settings as _load_settings
    _persisted = _load_settings()
    _PERSIST_KEYS = (
        "symbol", "strategy", "interval", "check_every",
        "threshold", "initial_balance", "manual_amount",
        "refresh_secs", "tg_enabled", "tg_token", "tg_chat_id",
        "active_symbols", "bot_was_running",
        "ai_assist", "ai_aggressiveness", "aggressive_mode",
        # Chart indicator toggles
        "show_ema", "show_volume", "show_rsi", "show_macd",
        "show_stoch", "show_old_trades", "show_sl_tp",
        # Chart view preferences
        "chart_autofollow", "chart_marker_offset_pct",
    )
    for _k in _PERSIST_KEYS:
        if _k in _persisted:
            st.session_state[_k] = _persisted[_k]
    if "risk" in _persisted and isinstance(_persisted["risk"], dict):
        for _rk, _rv in _persisted["risk"].items():
            # Emergency stop is NEVER restored from disk — it is a session-only
            # kill switch. A stale persisted `true` (e.g. from an older build or
            # the droplet's settings.json) must not silently re-arm it on refresh.
            if _rk == "emergency_stop":
                continue
            if hasattr(st.session_state.risk, _rk):
                setattr(st.session_state.risk, _rk, _rv)
    # Belt-and-suspenders: emergency stop ALWAYS starts OFF on a cold load /
    # page refresh. It only turns ON when the operator clicks 🚨 Emergency Stop
    # in the current session.
    st.session_state.risk.emergency_stop = False
    # Global risk
    if "global_risk" in _persisted and isinstance(_persisted["global_risk"], dict):
        _gr = st.session_state.get("global_risk") or GlobalRiskSettings()
        for _gk, _gv in _persisted["global_risk"].items():
            # GLOBAL emergency stop is never restored from disk (same rule as
            # per-symbol risk) — it must start OFF on every refresh/cold start.
            if _gk == "emergency_stop":
                continue
            if hasattr(_gr, _gk):
                setattr(_gr, _gk, _gv)
        _gr.emergency_stop = False
        st.session_state.global_risk = _gr
    # Per-symbol overrides
    if "per_symbol_risk" in _persisted and isinstance(_persisted["per_symbol_risk"], dict):
        _pso: dict = {}
        for _sym, _vals in _persisted["per_symbol_risk"].items():
            if not isinstance(_vals, dict):
                continue
            _rs = RiskSettings()
            for _rk, _rv in _vals.items():
                # Never restore a per-symbol emergency stop from disk.
                if _rk == "emergency_stop":
                    continue
                if hasattr(_rs, _rk):
                    setattr(_rs, _rk, _rv)
            _rs.emergency_stop = False
            _pso[_sym] = _rs
        st.session_state.per_symbol_risk = _pso
    # ── ACTIVE SCALPER MODE: force-snap protected fields back to spec ────
    # The persisted settings.json on disk may pre-date the FULL RESET (e.g.
    # contains BTCUSDT only, 15s tick, 0.05% threshold, 50/100 risk). The
    # operator has no UI to change these in the new build, so override.
    # EXCEPTION: the operator MAY now opt into EMA_MACD_RSI_VOLUME_V2 from the
    # sidebar — if that was persisted, keep it. Anything else snaps back to
    # the default Market Low live strategy.
    if st.session_state.get("strategy") != "EMA_MACD_RSI_VOLUME_V2":
        st.session_state.strategy       = "Market Low"
    # Interval: V2 is research-validated at 4h ONLY — keep 4h when it's selected;
    # every other (scalping) strategy snaps back to 1m.
    if st.session_state.get("strategy") == "EMA_MACD_RSI_VOLUME_V2":
        st.session_state.interval       = "4h"
    else:
        st.session_state.interval       = "1m"
    st.session_state.check_every        = 2
    st.session_state.threshold          = 0.01
    st.session_state.ai_assist          = True
    st.session_state.ai_aggressiveness  = "Active Scalper"
    # Symbols: re-instate BTC+ETH+SOL if persisted file dropped to one.
    _syms_persist = st.session_state.get("active_symbols") or []
    if len(_syms_persist) < 2:
        st.session_state.active_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # ── Risk numbers PERSIST — no force-snap. ────────────────────────────────
    # The operator now controls SL/TP/max-open/cooldown/exposure from the
    # sidebar and these are saved to settings.json. We must NOT clobber the
    # persisted values on cold start (that was the "max_open keeps resetting"
    # bug). We only (a) ensure new fields exist on sessions migrated from an
    # older build, and (b) keep the concentration cap disabled by default.
    try:
        _r = st.session_state.risk
        if not getattr(_r, "dynamic_size_pct", 0):
            _r.dynamic_size_pct = 40.0
    except Exception:
        pass
    for _sym, _rs in (st.session_state.get("per_symbol_risk") or {}).items():
        try:
            if not getattr(_rs, "dynamic_size_pct", 0):
                _rs.dynamic_size_pct = 40.0
        except Exception:
            pass

    # Sync the shared RiskManager to the freshly-loaded persisted RiskSettings
    # BEFORE auto-resuming the bot. The sidebar also does this (risk_manager
    # .settings = r) but that runs later in the script — without this line an
    # auto-resumed worker would tick with stale/default risk until the sidebar
    # rendered. Workers for non-overridden symbols share this object.
    try:
        st.session_state.risk_manager.settings = st.session_state.risk
    except Exception:
        pass

    # ── Aggressive Mode: PostgreSQL is the source of truth (survives redeploy) ─
    # Falls back to the JSON-persisted / default value if the DB is unavailable.
    try:
        _db_mode = am.get_mode()
        st.session_state.aggressive_mode = _db_mode
        _prof = am.get_profile(_db_mode)
        st.session_state.check_every = int(_prof["check_every"])
        am.apply_profile_to_risk(st.session_state.risk, _db_mode)
        for _rs in (st.session_state.get("per_symbol_risk") or {}).values():
            am.apply_profile_to_risk(_rs, _db_mode)
        st.session_state.risk_manager.settings = st.session_state.risk
    except Exception as _e:
        print(f"[AGGRO] mode load failed, using default: {_e}", flush=True)
        st.session_state.aggressive_mode = am.DEFAULT_MODE

    # ── Market Low strategy (Task #11): load PG-persisted live settings ──
    # PostgreSQL is the source of truth for the dip strategy controls (size
    # mode, limits, aggressive/safe toggles, thresholds). Falls back to the
    # dataclass defaults if the DB is unavailable.
    try:
        st.session_state.live_settings = live_settings.get_settings()
    except Exception as _e:
        print(f"[LIVE-SETTINGS] load failed, using defaults: {_e}", flush=True)
        st.session_state.live_settings = live_settings.LiveSettings()

    st.session_state._settings_loaded = True
    if _persisted:
        print(f"[SETTINGS] loaded {len(_persisted)} keys from disk "
              f"(risk numbers persisted, not force-snapped)", flush=True)
    # FIX FINAL (May 29 2026): surface the effective max-open caps at startup so
    # the operator can confirm settings.json was loaded BEFORE the bot starts.
    try:
        print(f"[SETTINGS] max_open_trades (per-symbol)="
              f"{st.session_state.risk.max_open_trades} | "
              f"max_open_trades_total (global)="
              f"{st.session_state.global_risk.max_open_trades_total} | "
              f"manage_manual_trades="
              f"{st.session_state.global_risk.manage_manual_trades}", flush=True)
    except Exception:
        pass
    tg.configure(
        token   = st.session_state.get("tg_token",   ""),
        chat_id = st.session_state.get("tg_chat_id", ""),
        enabled = st.session_state.get("tg_enabled", False),
    )
    # Now that settings are loaded AND auto-connect has run (in _init), try to
    # resume the bot if it was running before the last server restart.
    _maybe_resume_bot()

SYMBOLS    = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","MATICUSDT"]
INTERVALS  = ["1m","3m","5m","15m","30m","1h","4h","1d"]
STRATEGIES = ["Active Scalper"]   # ACTIVE SCALPER MODE — single hardcoded strategy


def _cl():
    """Return the active LIVE Binance client, rebuilding it if credentials drifted."""
    c = st.session_state.get("client")
    want_key    = st.session_state.get("api_key", "")
    want_secret = st.session_state.get("api_secret", "")
    if not want_key or not want_secret:
        if c is not None:
            print("[BINANCE] Credentials cleared — dropping cached client", flush=True)
            st.session_state.client    = None
            st.session_state.connected = False
        return None
    if (c is None
            or getattr(c, "api_key", None)    != want_key
            or getattr(c, "api_secret", None) != want_secret):
        print(f"[BINANCE] Rebuilding LIVE client — drift detected "
              f"(prev={(getattr(c,'api_key','') or '')[:6]}... → new={want_key[:6]}...)",
              flush=True)
        from binance_client import BinanceClient
        c = BinanceClient(want_key, want_secret)
        st.session_state.client = c
    return c

def _fmt_p(v, d=4): return f"${v:,.{d}f}" if v is not None else "—"

@st.cache_data(ttl=30, show_spinner=False)
def _market_overview():
    """Fetch 24h stats for all symbols — cached 30s to avoid hammering on every 5s rerun."""
    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
    out = {}
    def _fetch(s):
        try: return s, public_24h(s)
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


@st.cache_data(ttl=60, show_spinner=False)
def _load_carry_breakeven_cells():
    """Read the latest research report and return the carry FEE-SWEEP cells that
    carry per-symbol break-even data (`breakeven_four_leg_pct` = gross,
    `breakeven_funding_only_pct` = funding-only). Cached 60s — the report only
    changes when research is re-run. Returns [] on any error (fail soft: the
    card simply hides if no report exists)."""
    import json as _json
    path = os.path.join(os.path.dirname(__file__), "data", "research", "latest.json")
    try:
        with open(path) as f:
            report = _json.load(f)
    except Exception:
        return []
    out = []
    for c in report.get("cells", []):
        if c.get("kind") != "carry":
            continue
        fs = c.get("fee_sweep")
        if not isinstance(fs, dict):
            continue  # only fee-sweep carry cells store per-symbol break-evens
        subs = c.get("subcells", {})
        if not any(sc.get("breakeven_four_leg_pct") is not None
                   for sc in subs.values()):
            continue
        out.append(c)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# LIVE MARKET DATA — always fetched fresh on every rerun
# ─────────────────────────────────────────────────────────────────────────────
sym       = st.session_state.symbol
interval  = st.session_state.interval

live_price    = None
change_pct    = 0.0
high_24h      = None
low_24h       = None
df_chart      = None
chart_source  = ""

# 1. Always fetch 24h stats (public, no auth needed)
try:
    stats      = public_24h(sym)
    live_price = stats["price"]
    change_pct = stats["change_pct"]
    high_24h   = stats["high"]
    low_24h    = stats["low"]
except Exception:
    pass

# 2. Chart data — prefer bot's continuously-updated shared df when bot is running
bot_inst    = bot_module.get_bot()
bot_running = bot_inst.is_running() if bot_inst else False

# Chart history depth — fetch a deep candle set so "Zoom out" can reveal the
# full available history, not just the last few hours. Binance is paginated
# under the hood (1000/req) so this may be several REST calls on cold render.
CHART_CANDLES = 2000

# Cached so we don't re-download 2000 candles on every 5s Streamlit rerun.
# TTL 10s keeps the chart fresh enough for scalping while collapsing the
# repeated paginated REST calls into one fetch per symbol/interval window.
@st.cache_data(ttl=10, show_spinner=False)
def _deep_chart_df(sym: str, interval: str, use_auth: bool, limit: int):
    if use_auth and _cl():
        raw = _cl().get_klines(sym, interval, limit=limit)
    else:
        raw = public_klines(sym, interval, limit=limit)
    return get_indicators(raw)

# Always fetch a DEEP candle set for the chart display (auth if connected,
# else public). The bot's shared df is only ~150 candles (kept small for
# signal speed), so using it for the chart capped zoom-out at a few hours.
df_chart = None
_use_auth = bool(st.session_state.connected and _cl())
try:
    df_chart     = _deep_chart_df(sym, interval, _use_auth, CHART_CANDLES)
    chart_source = "auth" if _use_auth else "public"
except Exception:
    df_chart = None

# Live price: prefer the bot's cached price, else the authed ticker.
if bot_running:
    _bot_price = get_shared_price(symbol=st.session_state.symbol)
    if _bot_price:
        live_price = _bot_price
elif st.session_state.connected and _cl():
    try:
        live_price = _cl().get_symbol_price(sym)
    except Exception:
        pass

# Fallback: if the deep fetch failed, use the bot's live df so the chart
# still renders something rather than going blank.
if df_chart is None and bot_running:
    _bot_df = get_shared_df(symbol=st.session_state.symbol)
    if _bot_df is not None and len(_bot_df) > 5:
        df_chart     = _bot_df
        chart_source = "bot-live"


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
        p = public_price(sym)
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


@st.cache_data(ttl=15, show_spinner=False)
def _compute_account_value(_client, bust: str) -> dict:
    """TRUE total account value in USDT = free+locked USDT PLUS the live market
    value of every coin held (BTC/ETH/SOL/etc). The USDT-only balance does NOT
    include coins the bot/operator bought — that money lives in coins, not USDT,
    which is why the dashboard could show ~$4 while the real account looked full.

    `_client` is UNHASHED (leading underscore, not hashable); `bust` (api-key
    prefix) is the hashable cache key so one account's value is never served for
    another. Cached 15s so the 2s auto-refresh doesn't hammer the API.
    """
    _STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"}
    bals = _client.get_all_balances()  # {asset: {free, locked, total}}
    total = 0.0
    holdings = []
    unpriced = []   # held coins we couldn't get a live price for (NOT counted)
    for _asset, _b in bals.items():
        _amt = float(_b.get("total") or 0)
        if _amt <= 0:
            continue
        if _asset in _STABLES:
            _val = _amt
        else:
            try:
                _val = _amt * public_price(f"{_asset}USDT")
            except Exception:
                _val = 0.0   # no USDT pair / dust / transient quote failure
        if _val > 0:
            total += _val
            holdings.append({"asset": _asset, "amount": _amt, "value": _val})
        elif _asset not in _STABLES:
            # Held a real coin but couldn't price it — flag it so the total is
            # never silently understated (this is what confused the user before).
            unpriced.append({"asset": _asset, "amount": _amt})
    holdings.sort(key=lambda h: h["value"], reverse=True)
    return {"total": total, "holdings": holdings, "unpriced": unpriced}


# Equity = starting capital + realized PnL + unrealized PnL (fallback only —
# overridden by the REAL Binance account value below when connected).
equity  = st.session_state.initial_balance + realized_pnl + unrealized_pnl

# LIVE Binance balance — single source of truth. No simulated/paper equity.
balance = 0.0
binance_total_usdt   = 0.0   # free + locked, real Binance balance
binance_free_usdt    = 0.0   # available USDT for new orders
binance_locked_usdt  = 0.0   # in open orders
binance_balance_err  = None  # populated on API failure → shown in UI
_binance_connected   = (st.session_state.connected and _cl() is not None)
if _binance_connected:
    try:
        # CALLED EVERY REFRESH (no cache) — this is the live USDT balance
        _bal = _cl().get_account_balance("USDT")
        binance_total_usdt  = _bal["total"]
        binance_free_usdt   = _bal["free"]
        binance_locked_usdt = _bal["locked"]
        balance             = binance_total_usdt
    except Exception as _e:
        binance_balance_err = str(_e)
        # Failed balance call is critical — surface it, do NOT fall back to fake equity
        balance = 0.0

# ── TRUE account value: USDT + live value of ALL coin holdings ────────────────
# This is the number that matches the Binance app. The USDT card above is only
# the leftover cash; coins the bot bought are counted here.
account_value_usdt = None
account_holdings   = []
account_unpriced   = []
account_value_err  = None
if _binance_connected and not binance_balance_err:
    try:
        _av = _compute_account_value(_cl(), (st.session_state.get("api_key") or "")[:8])
        account_value_usdt = _av["total"]
        account_holdings   = _av["holdings"]
        account_unpriced   = _av.get("unpriced", [])
    except Exception as _e:
        account_value_err = str(_e)

# REAL account value is the honest equity when connected — no fake starting capital.
if account_value_usdt is not None:
    equity = account_value_usdt

roi = (total_pnl / equity * 100) if equity else 0.0

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

_upd_at  = get_shared_updated_at(symbol=st.session_state.symbol) if bot_running else None
_now_tz  = datetime.now(_TZ)
_upd_str = _fmt_london(_upd_at) if _upd_at else _now_tz.strftime("%H:%M:%S")
_src_label = {"bot-live": "bot-live", "auth": "auth-live", "public": "public"}.get(chart_source, "—")

conn_pill = ('<span class="pill p-green"><span class="dot dot-g"></span>CONNECTED</span>'
             if st.session_state.connected
             else '<span class="pill p-gray"><span class="dot dot-x"></span>NO AUTH</span>')
bot_pill  = ('<span class="pill p-blue"><span class="dot dot-y"></span>BOT ON</span>'
             if bot_running
             else '<span class="pill p-gray">BOT OFF</span>')
mode_pill = '<span class="pill p-red"><span class="dot dot-r"></span>⚡ LIVE</span>'
net_pill  = '<span class="pill p-red">MAINNET</span>'
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

# ── MOBILE always-visible summary (hidden on desktop via CSS) ────────────────
# Phones can clip the header pills or hide the sidebar — this strip guarantees
# the four critical pieces of info are ALWAYS on screen on small viewports:
#   CONNECTED status · USDT balance · Bot ON/OFF · Active symbols
_mob_conn_val = "CONNECTED" if (st.session_state.connected and _cl()) else "NOT CONNECTED"
_mob_conn_cls = "up" if (st.session_state.connected and _cl()) else "dn"
try:
    _mob_bal = f"${_cl().get_account_balance('USDT')['total']:,.2f}" if (st.session_state.connected and _cl()) else "—"
except Exception:
    _mob_bal = "ERR"
_mob_bot_val = "ON" if bot_running else "OFF"
_mob_bot_cls = "up" if bot_running else "gray"
_mob_syms = ",".join(s.replace("USDT", "") for s in st.session_state.active_symbols) or "—"
st.markdown(f"""
<div class="mob-summary">
  <div class="mob-cell"><div class="mob-lbl">Binance</div><div class="mob-val {_mob_conn_cls}">{_mob_conn_val}</div></div>
  <div class="mob-cell"><div class="mob-lbl">Balance</div><div class="mob-val">{_mob_bal}</div></div>
  <div class="mob-cell"><div class="mob-lbl">Bot</div><div class="mob-val {_mob_bot_cls}">{_mob_bot_val}</div></div>
  <div class="mob-cell"><div class="mob-lbl">Symbols</div><div class="mob-val gray">{_mob_syms}</div></div>
</div>
<div class="mob-hint">☰ Tap top-left to open Connection · Strategy · Risk controls</div>
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

# ── Verification Panel (always visible in main area) ──────────────────────────
_conn_banner_parts = []
# Left: connection status
if st.session_state.connected and _cl():
    _net_lbl  = "🔴 LIVE MAINNET"
    _net_col  = "#ef5350"
    try:
        _b_usdt = _cl().get_account_balance("USDT")["total"]
        _bal_str = f"${_b_usdt:,.2f} USDT"
    except Exception as _e:
        _bal_str = f"Balance ERR: {_e}"
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
        f'<span style="font-size:11px;color:#6e7681;">Not connected · LIVE trading requires API key · open sidebar to connect</span>'
        f'</div>'
    )

# Right: bot status + last signal + confidence + last check
# Prefer the structured meta from shared state (set by every bot tick);
# fall back to log-message parsing if the bot hasn't ticked yet this process.
_sig_meta = get_bot_signal_meta(symbol=st.session_state.symbol)
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
_last_tick = get_shared_last_tick(symbol=st.session_state.symbol)
if _last_tick:
    _tick_str = _fmt_london(_last_tick)
    _elapsed  = int((datetime.now(_TZ) - _to_london(_last_tick)).total_seconds())
    _tick_disp = f"{_tick_str} <span style=\"opacity:.55;\">· {_elapsed}s ago</span>"
else:
    _tick_disp = "—"

# LIVE mode always — paper verification has been removed
_gate_html = ('<span class="pill p-red"><span class="dot dot-r"></span>'
              '⚡ LIVE MAINNET · every order is real</span>')

if bot_running:
    _bot_dot = '<span class="dot dot-y"></span>'
    _bot_lbl = '<span style="font-size:11px;font-weight:700;color:#e3b341;">BOT ON</span>'
else:
    _bot_dot = '<span class="dot dot-x"></span>'
    _bot_lbl = '<span style="font-size:11px;font-weight:700;color:#6e7681;">BOT OFF</span>'

_strat_html = (f'<span style="font-size:10px;color:#484f58;">STRATEGY</span> '
               f'<span style="font-size:11px;font-weight:700;color:#79b0ff;font-family:\'JetBrains Mono\',monospace;">'
               f'{st.session_state.strategy}</span>')

# ── Strategy pill — the only live strategy is the Market Low ──────────
_ai_on   = True
_ai_prof = "Market Low"
_ai_col  = "#2ea043"
_ai_pill = (f'<span class="pill" style="background:{_ai_col}22;'
            f'border:1px solid {_ai_col}66;color:{_ai_col};'
            f'font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;">'
            f'💧 Market Low Live Strategy</span>')

# Last AI decision for the current symbol (from shared state set in tick())
_ai_last  = (_sig_meta.get("ai_decision") or "").upper() if _sig_meta else ""
_ai_lconf = int((_sig_meta.get("ai_confidence") or 0)) if _sig_meta else 0
_ai_lreason = ((_sig_meta.get("ai_reason") or "")[:80]) if _sig_meta else ""
# Learning-mode extras (trend pill + why-bullets panel rendered below).
_ai_trend  = (_sig_meta.get("ai_trend") or "SIDEWAYS") if _sig_meta else "SIDEWAYS"
_ai_sstr   = int((_sig_meta.get("ai_signal_strength") or 0)) if _sig_meta else 0
_ai_why    = (_sig_meta.get("ai_why_bullets") or []) if _sig_meta else []
_ai_blkr   = (_sig_meta.get("ai_blocker") or "") if _sig_meta else ""
_ai_last_html = ""
_trend_pill_html = ""
if _ai_on and _ai_last:
    _alc = {"BUY":"#26a69a","SELL":"#ef5350","HOLD":"#6e7681"}.get(_ai_last, "#484f58")
    _ai_last_html = (
        f'<div style="display:flex;align-items:center;gap:6px;">'
        f'<span style="font-size:10px;color:#484f58;">AI →</span>'
        f'<span style="font-size:11px;font-weight:800;color:{_alc};'
        f'font-family:\'JetBrains Mono\',monospace;">{_ai_last}</span>'
        f'<span style="font-size:10px;color:#6e7681;">·</span>'
        f'<span style="font-size:11px;color:{_alc};font-family:\'JetBrains Mono\',monospace;">'
        f'{_ai_lconf}%</span>'
        f'</div>'
    )
# NOTE: the legacy GPT "HYBRID" advisor badge has been removed — the only live
# strategy is the Market Low and its deterministic AI advisory is already
# surfaced via the AI pill + AI→ decision above. No GPT/hybrid mode runs.

if _ai_on and _ai_trend:
    _tc = {"UP":"#26a69a","DOWN":"#ef5350","SIDEWAYS":"#a371f7"}.get(_ai_trend, "#6e7681")
    _ticon = {"UP":"📈","DOWN":"📉","SIDEWAYS":"➡️"}.get(_ai_trend, "·")
    _trend_pill_html = (
        f'<span class="pill" style="background:{_tc}22;border:1px solid {_tc}66;'
        f'color:{_tc};font-size:10px;font-weight:700;padding:2px 8px;'
        f'border-radius:10px;">{_ticon} TREND · {_ai_trend}</span>'
    )

_bot_status_html = (
    f'<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">'
    f'<div style="display:flex;align-items:center;gap:6px;">{_bot_dot}{_bot_lbl}</div>'
    f'<div>{_ai_pill}</div>'
    f'<div>{_trend_pill_html}</div>'
    f'{_ai_last_html}'
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

_diag = get_bot_diagnostics(symbol=st.session_state.symbol)
_block = (_diag.get("block_reason") or "").strip()
_lo    = _diag.get("last_order") or {}

_block_html = ""
if bot_running and _block:
    _block_html = (
        f'<div style="padding:6px 20px;background:#2a0f0f;border-bottom:1px solid #ef535066;'
        f'font-size:11px;color:#ffb4b0;font-family:\'JetBrains Mono\',monospace;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;">'
        f'<span style="color:#ef5350;">⛔ BOT BLOCKED ›</span> {_block}'
        f'</div>'
    )

_order_html = ""
if bot_running and _lo:
    if _lo.get("ok"):
        _order_html = (
            f'<div style="padding:5px 20px;background:#0d2a1a;border-bottom:1px solid #26a69a44;'
            f'font-size:10px;color:#7ce0c2;font-family:\'JetBrains Mono\',monospace;">'
            f'<span style="color:#26a69a;">✅ LAST ORDER ›</span> {_lo.get("mode")} '
            f'{_lo.get("side")} {_lo.get("qty")} {_lo.get("symbol")} @ ${(_lo.get("price") or 0):.4f}'
            f'</div>'
        )
    else:
        _order_html = (
            f'<div style="padding:5px 20px;background:#2a0f0f;border-bottom:1px solid #ef535066;'
            f'font-size:10px;color:#ffb4b0;font-family:\'JetBrains Mono\',monospace;">'
            f'<span style="color:#ef5350;">❌ LAST ORDER FAILED ›</span> {_lo.get("mode")} '
            f'{_lo.get("side")} {_lo.get("qty")} {_lo.get("symbol")} — {_lo.get("error")}'
            f'</div>'
        )

_reason_html = (
    f'<div style="padding:5px 20px;background:#0a0d12;border-bottom:1px solid #1e2736;'
    f'font-size:10px;color:#6e7681;font-family:\'JetBrains Mono\',monospace;'
    f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
    f'<span style="color:#484f58;">REASON ›</span> {_sig_reason or "Waiting for first bot check…"}'
    f'</div>'
) if bot_running else ""
_reason_html = _block_html + _order_html + _reason_html

st.markdown(
    f'<div style="display:flex;align-items:center;justify-content:space-between;'
    f'flex-wrap:wrap;gap:10px;padding:8px 20px;background:#0d1117;border-bottom:1px solid #1e2736;">'
    f'{"".join(_conn_banner_parts)}'
    f'{_bot_status_html}'
    f'</div>'
    f'{_reason_html}',
    unsafe_allow_html=True,
)

# ── Sticky "last action" banner — survives st.rerun() so the user always
# sees the result of the most recent button click (success or error).
# Buttons set st.session_state.last_action = {"kind":"ok|err","msg":"..."}.
# Cleared by the small ✕ button.
_la = st.session_state.get("last_action")
if _la and isinstance(_la, dict) and _la.get("msg"):
    _kind = _la.get("kind", "ok")
    _bg   = "#0d2a1a" if _kind == "ok" else "#2a0f0f"
    _bd   = "#26a69a" if _kind == "ok" else "#ef5350"
    _fg   = "#7ce0c2" if _kind == "ok" else "#ffb4b0"
    _ico  = "✅" if _kind == "ok" else "❌"
    _lcol1, _lcol2 = st.columns([0.97, 0.03])
    with _lcol1:
        st.markdown(
            f'<div style="padding:8px 14px;background:{_bg};border:1px solid {_bd}66;'
            f'border-radius:4px;color:{_fg};font-size:12px;font-weight:600;'
            f'font-family:\'JetBrains Mono\',monospace;margin:6px 14px;'
            f'word-break:break-word;">{_ico} {_la["msg"]}</div>',
            unsafe_allow_html=True,
        )
    with _lcol2:
        if st.button("✕", key="dismiss_last_action", help="Dismiss"):
            st.session_state.last_action = None
            st.rerun()

# ── Multi-symbol bot overview + "BOT ACTIVE BUT WAITING" banner ──────────────
# Shows per-symbol signal / last check / last order / block reason so the user
# can see at a glance WHY each enabled symbol isn't trading. Always rendered
# (with placeholders) when bot is ON, so an idle bot is never silently idle.
if bot_running:
    # ── AUTO-DISABLE GATE STATUS ─────────────────────────────────────────────
    # NOTE: the LIVE Market Low path is intentionally NOT research-gated
    # (Task #11). There is no validation/allowlist/AUTO-DISABLE banner here —
    # the bot auto-trades on its own price signal, bounded only by the
    # money-safety gates (emergency stop, safe mode, balance, spending limit,
    # max position size, cooldown, global risk, daily-loss breaker).

    _all_state = get_all_symbol_state() or {}
    _active    = list(st.session_state.active_symbols or [])
    # Ensure every active symbol has a row, even before its first tick
    for _s in _active:
        _all_state.setdefault(_s, {})

    # Compute "WAITING" banner: bot is ON but no successful order recently.
    # Threshold scales with check_every so a slow tick (e.g. 300s) doesn't
    # falsely trigger the banner; minimum 5 min, otherwise 2× the tick.
    _now = datetime.now()
    _wait_thresh = max(300, int(st.session_state.check_every) * 2)
    _recent_order = False
    _waiting_reasons: list[str] = []
    for _s in _active:
        _d   = get_bot_diagnostics(symbol=_s) or {}
        _stx = _all_state.get(_s, {})
        _lo_at = _d.get("last_order_at")
        if _lo_at and (_now - _lo_at).total_seconds() < _wait_thresh:
            _lo = _d.get("last_order") or {}
            if _lo.get("ok"):
                _recent_order = True
        _br  = (_d.get("block_reason") or "").strip()
        _sig = (_stx.get("signal") or "").upper()
        if _br:
            _waiting_reasons.append(f"{_s}: {_br}")
        elif _sig == "HOLD":
            _waiting_reasons.append(f"{_s}: HOLD — {_stx.get('last_reason','no signal yet')}")

    if not _recent_order:
        _msg = " · ".join(_waiting_reasons[:3]) if _waiting_reasons \
               else "waiting for first signal across all enabled symbols"
        st.markdown(
            f'<div style="padding:8px 20px;background:#1e1a0a;border-bottom:1px solid #e3b34166;'
            f'font-size:12px;color:#e3b341;font-family:\'JetBrains Mono\',monospace;font-weight:700;">'
            f'⏳ BOT ACTIVE BUT WAITING › <span style="color:#f0d169;font-weight:600;">{_msg}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Per-symbol cards: signal · last check · last order · block reason
    _cards = ""
    for _s in _active:
        _st   = _all_state.get(_s, {})
        _d    = get_bot_diagnostics(symbol=_s) or {}
        _sig  = (_st.get("signal") or "—").upper()
        _sc   = {"BUY": "#26a69a", "SELL": "#ef5350", "HOLD": "#e3b341"}.get(_sig, "#6e7681")
        _upd  = _st.get("updated_at")
        _lo_at = _d.get("last_order_at")
        _lo    = _d.get("last_order") or {}
        _br    = (_d.get("block_reason") or "").strip()

        def _ago(dt):
            if not dt:
                return "never"
            sec = int((_now - dt).total_seconds())
            if sec < 60:  return f"{sec}s ago"
            if sec < 3600: return f"{sec//60}m ago"
            return f"{sec//3600}h ago"

        _last_check_s = _ago(_upd)
        if _lo_at and _lo.get("ok"):
            _last_order_s = f'{_lo.get("side","?")} @ ${(_lo.get("price") or 0):.4f} · {_ago(_lo_at)}'
            _lo_col = "#7ce0c2"
        elif _lo_at:
            _last_order_s = f'FAILED · {_ago(_lo_at)}'
            _lo_col = "#ffb4b0"
        else:
            _last_order_s = "no orders yet"
            _lo_col = "#6e7681"
        # When there's no hard block, surface the HOLD reason so the user
        # always sees WHY the symbol isn't trading.
        if _br:
            _block_s, _block_col = _br, "#ffb4b0"
        elif _sig == "HOLD":
            _block_s = _st.get("last_reason") or "waiting for signal"
            _block_col = "#e3b341"
        else:
            _block_s, _block_col = "—", "#6e7681"

        # SMART AI SCALPING BOT — score + regime display
        _score = int(_st.get("score") or 0)
        if   _score >= 85: _score_col = "#26a69a"   # excellent (green)
        elif _score >= 75: _score_col = "#7ce0c2"   # strong (mint)
        elif _score >= 65: _score_col = "#e3b341"   # medium (yellow)
        else:              _score_col = "#6e7681"   # below-min (grey)
        _regime = (_st.get("regime") or "").upper()
        _reg_col = {
            "TREND":    "#26a69a",   # green
            "VOLATILE": "#e3b341",   # yellow
            "RANGE":    "#7ce0c2",   # mint
            "DEAD":     "#ef5350",   # red
        }.get(_regime, "#6e7681")
        _reg_txt = _regime or "—"
        _cards += (
            f'<div style="flex:1 1 0;min-width:220px;background:#0d1117;border:1px solid #1e2736;'
            f'border-radius:8px;padding:10px 12px;">'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
            f'    <span style="font-size:12px;font-weight:800;color:#f0f6fc;font-family:\'JetBrains Mono\',monospace;">{_s.replace("USDT","")}</span>'
            f'    <span style="font-size:11px;font-weight:800;color:{_sc};font-family:\'JetBrains Mono\',monospace;">{_sig}</span>'
            f'  </div>'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
            f'    <span style="font-size:9px;color:#484f58;font-family:\'JetBrains Mono\',monospace;">SCORE</span>'
            f'    <span style="font-size:14px;font-weight:800;color:{_score_col};font-family:\'JetBrains Mono\',monospace;">{_score}</span>'
            f'  </div>'
            f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
            f'    <span style="font-size:9px;color:#484f58;font-family:\'JetBrains Mono\',monospace;">REGIME</span>'
            f'    <span style="font-size:10px;font-weight:800;color:{_reg_col};font-family:\'JetBrains Mono\',monospace;">{_reg_txt}</span>'
            f'  </div>'
            f'  <div style="font-size:10px;color:#8b949e;line-height:1.55;">'
            f'    <div><span style="color:#484f58;">LAST CHECK</span> <span style="color:#c9d1d9;font-family:\'JetBrains Mono\',monospace;">{_last_check_s}</span></div>'
            f'    <div><span style="color:#484f58;">LAST ORDER</span> <span style="color:{_lo_col};font-family:\'JetBrains Mono\',monospace;">{_last_order_s}</span></div>'
            f'    <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="{_block_s}"><span style="color:#484f58;">BLOCK</span> <span style="color:{_block_col};font-family:\'JetBrains Mono\',monospace;">{_block_s}</span></div>'
            f'  </div>'
            f'</div>'
        )
    if _cards:
        st.markdown(
            f'<div style="display:flex;gap:10px;flex-wrap:wrap;padding:10px 20px;background:#0a0d12;border-bottom:1px solid #1e2736;">'
            f'{_cards}</div>',
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# 🚦 WHY NO TRADE? — live trade diagnostics (decision journal + frequency)
# Observability only. Shows the EXACT current reason each symbol isn't trading,
# trade-frequency stats, and the top reasons trades were blocked this run. Also
# exposes manual Binance pre-flight verification + ghost-trade reconciliation.
# ─────────────────────────────────────────────────────────────────────────────
try:
    _freq   = diagnostics.trade_frequency_stats()
    _latest = diagnostics.get_latest_by_symbol()
    _topblk = diagnostics.get_block_summary(top=10)
    _cyc    = diagnostics.get_cycle_stats()

    with st.expander("🚦 WHY NO TRADE? — live trade diagnostics",
                     expanded=bool(bot_running)):
        _m1, _m2, _m3, _m4 = st.columns(4)
        _m1.metric("Trades today", _freq["trades_today"])
        _m2.metric("Avg trades/day", _freq["avg_per_day"])
        _m3.metric("Open now", _freq["open_trades"])
        _mins = _freq["minutes_since_last"]
        _m4.metric("Min since last trade",
                   f"{_mins:.0f}" if _mins is not None else "—")
        if _freq["last_trade_at"]:
            st.caption(
                f"Last trade: {_freq['last_trade_at'].strftime('%Y-%m-%d %H:%M:%S')}"
                f"  ·  total all-time: {_freq['total_trades']}"
                f"  ·  bot {_freq['bot_trades']} / manual {_freq['manual_trades']}")
        else:
            st.caption("No trades recorded yet — the bot has never opened a position.")

        st.markdown("**Current reason per symbol** (refreshes every cycle)")
        if _latest:
            def _fmt_chg(d):
                c = d.get("change_pct")
                return f"{c:+.2f}%" if isinstance(c, (int, float)) else "—"

            def _fmt_vol(d):
                v = d.get("volume_ratio")
                return f"{v:.2f}×" if isinstance(v, (int, float)) else "—"

            st.dataframe(
                [{"Symbol": d["symbol"], "Signal": d["signal"],
                  "20m Δ": _fmt_chg(d), "Vol": _fmt_vol(d),
                  "Why no trade": d["reason"]}
                 for d in _latest.values()],
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("No decisions recorded yet. Start the bot and give it a few "
                    "cycles, then this fills in.")

        st.markdown(
            f"**Top reasons trades were blocked this run** "
            f"(cycles: {_cyc['cycles']} · traded: {_cyc['traded']} · "
            f"block events: {_cyc['total_blocks']})")
        if _topblk:
            st.dataframe(
                [{"#": i + 1, "Reason": r["category"], "Count": r["count"],
                  "% of blocks": f"{r['pct']:.1f}%"}
                 for i, r in enumerate(_topblk)],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("No block events recorded yet this run.")

        st.divider()
        _bcol1, _bcol2, _bcol3 = st.columns(3)
        _client_now = st.session_state.get("client")
        _verify = _bcol1.button("🔍 Verify Binance", use_container_width=True,
                                disabled=_client_now is None,
                                help="Check connection, balance, minNotional, "
                                     "stepSize, and quantity precision.")
        _recon  = _bcol2.button("🧹 Reconcile now", use_container_width=True,
                                disabled=_client_now is None,
                                help="Close local open trades that no longer "
                                     "exist on Binance.")
        _report = _bcol3.button("📋 Full report", use_container_width=True,
                                help="Generate the Top-10 text diagnostic report.")

        if _client_now is None:
            st.caption("🔌 Connect a LIVE Binance key to enable verification + "
                       "reconciliation. (On Replit, api.binance.com is "
                       "geo-blocked — run these on your droplet.)")

        if _verify or _recon or _report:
            from exchanges.binance import BinanceExchange
            _ex_now = BinanceExchange(_client_now) if _client_now else None
            _syms_now = list(st.session_state.active_symbols or
                             ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            if _verify:
                _pf = diagnostics.preflight_checks(_ex_now, _syms_now)
                if _pf["ok"]:
                    st.success(f"✅ Connected · USDT free ${_pf['usdt_free']:.2f}")
                else:
                    st.error("⚠️ Pre-flight found issues: " +
                             "; ".join(_pf["errors"]) if _pf["errors"]
                             else "Not connected.")
                st.json(_pf)
            if _recon:
                _rc = diagnostics.reconcile_ghost_trades(_ex_now)
                if _rc.get("errors"):
                    st.error("; ".join(_rc["errors"]))
                if _rc.get("closed"):
                    st.warning(f"🧹 Closed {len(_rc['closed'])} ghost trade(s) "
                               f"not on Binance: {_rc['closed']}")
                elif not _rc.get("errors"):
                    st.success(f"✅ Reconcile complete — checked "
                               f"{_rc['checked']} open trade(s), no ghosts found.")
                if _rc.get("mismatches"):
                    st.info(f"{len(_rc['mismatches'])} partial mismatch(es) left "
                            f"OPEN for review (not auto-closed).")
                    st.json(_rc["mismatches"])
            if _report:
                st.code(diagnostics.build_report(exchange=_ex_now,
                                                 symbols=_syms_now))
except Exception as _diag_err:
    st.caption(f"Diagnostics panel unavailable: {_diag_err}")

# ─────────────────────────────────────────────────────────────────────────────
# 🧠 WHY AI DID THIS — learning panel for the currently-viewed symbol
# Always-visible expander (collapsed by default) that shows the bullet list
# of factors the AI checked on its last tick, the detected trend, signal
# strength, and any blocker. Empowers the operator to understand every
# HOLD/BUY/SELL decision in plain language.
# ─────────────────────────────────────────────────────────────────────────────
if _ai_on and (_ai_why or _ai_last or _ai_blkr):
    _verdict_col = {"BUY":"#26a69a","SELL":"#ef5350","HOLD":"#e3b341"}.get(
        (_ai_last or "HOLD"), "#6e7681")
    _verdict_txt = _ai_last or "HOLD"
    _blk_line = ""
    if _ai_blkr:
        _blk_line = (f'<div style="margin-top:6px;color:#e3b341;font-size:11px;">'
                     f'🚫 Blocker: <code style="background:#1e2736;padding:1px 6px;'
                     f'border-radius:4px;color:#f0d169;">{_ai_blkr}</code></div>')
    _bul_lines = ""
    for _b in (_ai_why or []):
        _bc = "#26a69a" if str(_b).lstrip().startswith("✓") else "#ef5350" \
              if str(_b).lstrip().startswith("✗") else "#c9d1d9"
        _bul_lines += (f'<div style="font-size:11px;color:{_bc};font-family:'
                       f'\'JetBrains Mono\',monospace;line-height:1.7;">{_b}</div>')
    if not _bul_lines:
        _bul_lines = ('<div style="font-size:11px;color:#6e7681;font-style:italic;">'
                      'Waiting for first AI tick on this symbol…</div>')
    with st.expander(f"🧠 Why AI did this · {st.session_state.symbol} · "
                     f"verdict {_verdict_txt} · trend {_ai_trend} · "
                     f"strength {_ai_sstr}%", expanded=False):
        st.markdown(
            f'<div style="padding:8px 12px;background:#0d1117;border:1px solid #1e2736;'
            f'border-radius:6px;">'
            f'<div style="font-size:10px;color:#484f58;text-transform:uppercase;'
            f'letter-spacing:.5px;margin-bottom:6px;">Last AI decision</div>'
            f'<div style="font-size:13px;font-weight:700;color:{_verdict_col};'
            f'margin-bottom:8px;">{_verdict_txt} · confidence {_ai_lconf}% · '
            f'trend {_ai_trend}</div>'
            f'<div style="font-size:10px;color:#484f58;text-transform:uppercase;'
            f'letter-spacing:.5px;margin-bottom:4px;">Factors checked</div>'
            f'{_bul_lines}'
            f'{_blk_line}'
            f'<div style="margin-top:8px;font-size:10px;color:#6e7681;font-style:italic;">'
            f'{(_ai_lreason or "—")[:200]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ AlphaTrade")

    # Connection — LIVE Mainnet ONLY
    st.markdown('<div class="sec-lbl">API Connection</div>', unsafe_allow_html=True)
    st.error("⚡ **LIVE Mainnet** — every order uses real money on api.binance.com")

    from secrets_store import (
        save_credentials   as _save_creds,
        clear_credentials  as _clear_creds,
        has_saved_credentials as _has_saved_creds,
    )

    if st.session_state.connected and _cl():
        _conn_c = _cl()
        try:
            _live_bal  = _conn_c.get_account_balance("USDT")
            _live_usdt = _live_bal["total"]
            _live_free = _live_bal["free"]
            _live_lock = _live_bal["locked"]
            _src_lbl = "saved backend credentials" if st.session_state.get("creds_from_disk") else "this session"
            st.markdown(f"""
<div style="background:#1a0a0a;border:1px solid #ef535044;border-radius:8px;padding:10px 12px;margin-bottom:8px;">
  <div style="font-size:9px;color:#ef5350;font-weight:700;letter-spacing:.1em;margin-bottom:4px;">CONNECTED · 🔴 LIVE MAINNET</div>
  <div style="font-size:10px;color:#8b949e;margin-bottom:6px;">via {_src_lbl}</div>
  <div style="font-size:18px;font-weight:700;color:#f0f6fc;font-family:'JetBrains Mono',monospace;">${_live_usdt:,.2f} <span style="font-size:11px;color:#6e7681;">USDT total</span></div>
  <div style="font-size:10px;color:#8b949e;margin-top:4px;font-family:'JetBrains Mono',monospace;">free ${_live_free:,.2f} · locked ${_live_lock:,.2f}</div>
</div>""", unsafe_allow_html=True)
        except Exception as _be:
            st.error(f"❌ Binance balance fetch failed: {_be}")
        # Show which credentials this client is currently using
        _cur = _cl()
        if _cur is not None:
            _kp = (getattr(_cur, "api_key", "") or "")[:6]
            st.caption(f"🔑 Key `{_kp}…` · 🌐 `api.binance.com`")
        _dc1, _dc2 = st.columns(2)
        with _dc1:
            if st.button("🔌 Disconnect", width="stretch",
                         help="Drop client from this session. Saved backend keys remain — use 'Clear saved' to delete them permanently."):
                print("[BINANCE] User disconnected — clearing session client only", flush=True)
                st.session_state.client            = None
                st.session_state.connected         = False
                st.session_state.api_key           = ""
                st.session_state.api_secret        = ""
                st.session_state.creds_from_disk   = False
                st.session_state.manual_disconnect = True  # block auto-reload from disk
                st.rerun()
        with _dc2:
            _has_saved = _has_saved_creds()
            if st.button("🧹 Clear saved", width="stretch",
                         disabled=not _has_saved,
                         help="Delete persisted backend credentials file. Disconnects and prevents auto-reconnect."):
                _clear_creds()
                st.session_state.client            = None
                st.session_state.connected         = False
                st.session_state.api_key           = ""
                st.session_state.api_secret        = ""
                st.session_state.creds_from_disk   = False
                st.session_state.manual_disconnect = True
                log_activity("INFO", "🧹 Cleared saved Binance credentials from backend")
                st.rerun()
    else:
        # Surface auto-connect failure (e.g. -1022/-2015) so user sees the exact Binance error
        _auto_err = st.session_state.pop("_auto_conn_err", None)
        if _auto_err:
            st.error(f"❌ Auto-connect failed with saved credentials: {_auto_err}")
        api_key    = st.text_input("API Key",    type="password",
                                    placeholder="Binance Mainnet API key (binance.com)")
        api_secret = st.text_input("API Secret", type="password",
                                    placeholder="Binance Mainnet API secret")
        _remember = st.checkbox("Remember on server (chmod 600, survives refresh + restart)",
                                value=True, key="_remember_creds")
        if st.button("🔌 Connect to Binance LIVE", width="stretch", type="primary"):
            if api_key and api_secret:
                with st.spinner("Connecting to api.binance.com…"):
                    try:
                        from binance_client import BinanceClient
                        # Strip trailing whitespace/newlines that often sneak in
                        # when pasting keys (especially api_secret) from email/notes.
                        api_key    = (api_key    or "").strip()
                        api_secret = (api_secret or "").strip()
                        # Guard: HTTP headers must be Latin-1. Cyrillic К (U+041A)
                        # looks identical to Latin K and sneaks in when keys are
                        # copied from chat apps — detect and refuse with a clear
                        # error instead of letting urllib3 crash with a codec
                        # exception deep inside requests.
                        def _bad_chars(label, s):
                            bad = [(i, ch, hex(ord(ch))) for i, ch in enumerate(s)
                                   if ord(ch) > 127]
                            if bad:
                                preview = ", ".join(f"pos {i}: '{ch}' ({h})"
                                                    for i, ch, h in bad[:3])
                                raise ValueError(
                                    f"{label} contains non-ASCII characters "
                                    f"({preview}). Likely a Cyrillic letter that "
                                    f"looks like a Latin one — re-copy the "
                                    f"{label.lower()} DIRECTLY from binance.com "
                                    f"(not from chat/email/notes) and try again.")
                        _bad_chars("API Key",    api_key)
                        _bad_chars("API Secret", api_secret)
                        st.session_state.manual_disconnect = False  # re-enable auto-load
                        st.session_state.api_key    = api_key
                        st.session_state.api_secret = api_secret
                        print(f"[BINANCE] CONNECT clicked — LIVE key_prefix={api_key[:6]}...",
                              flush=True)
                        c   = BinanceClient(api_key, api_secret)
                        ok, msg = c.test_connection()
                        if ok:
                            st.session_state.client    = c
                            st.session_state.connected = True
                            if _remember:
                                try:
                                    _save_creds(api_key, api_secret)
                                    st.session_state.creds_from_disk = True
                                    log_activity("INFO",
                                        f"🔌 Connected LIVE + saved to backend — key {api_key[:6]}…")
                                except Exception as _se:
                                    log_activity("ERROR",
                                        f"🔌 Connected LIVE but FAILED to persist creds: {_se}")
                                    st.warning(f"Connected, but could not save credentials: {_se}")
                            else:
                                st.session_state.creds_from_disk = False
                                log_activity("INFO",
                                    f"🔌 Connected LIVE (session only) — key {api_key[:6]}…")
                            st.success("✅ Connected to LIVE Mainnet!")
                            st.rerun()
                        else:
                            st.session_state.client    = None
                            st.session_state.connected = False
                            st.error(f"❌ {msg}")
                    except Exception as e:
                        st.session_state.client    = None
                        st.session_state.connected = False
                        st.error(f"Connection error: {e}")
            else:
                st.info("API key required — bot/manual trading is disabled until connected.")
        if _has_saved_creds():
            if st.button("🧹 Clear saved Binance keys", width="stretch",
                         help="Delete persisted backend credentials file."):
                _clear_creds()
                log_activity("INFO", "🧹 Cleared saved Binance credentials from backend")
                st.rerun()

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Market
    st.markdown('<div class="sec-lbl">Market</div>', unsafe_allow_html=True)

    # Active symbols — the bot trades all of these (max 3)
    _act_default = [s for s in st.session_state.active_symbols if s in SYMBOLS] \
                   or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    _act_default = _act_default[:3]
    act_sel = st.multiselect(
        "Active symbols (bot)", SYMBOLS,
        default=_act_default,
        max_selections=3,
        help="Bot trades all selected symbols. Maximum 3.",
    )
    if not act_sel:
        act_sel = ["BTCUSDT"]
        st.caption("⚠️ At least one symbol required — defaulted to BTCUSDT")
    st.session_state.active_symbols = act_sel

    # Currently-viewed symbol — drives chart, manual trade, force buttons
    _view_default = st.session_state.symbol if st.session_state.symbol in act_sel else act_sel[0]
    sym_sel = st.selectbox(
        "View symbol", act_sel,
        index=act_sel.index(_view_default),
        help="Symbol shown in the main chart + manual-trade buttons.",
    )
    st.session_state.symbol = sym_sel

    intv_sel = st.selectbox("Interval", INTERVALS,
                             index=INTERVALS.index(st.session_state.interval)
                             if st.session_state.interval in INTERVALS else 2)
    st.session_state.interval = intv_sel

    # Active strategy. The default live strategy is the Market Low
    # (DipLiveEngine). Task #19 adds the research-validated EMA_MACD_RSI_VOLUME_V2
    # @ 4h (ETH only) as a SECOND, gated live path (StrategyLiveEngine). The
    # operator picks which one runs; create_bot() routes V2 → strategy_mode and
    # everything else → the dip live path.
    _strat_opts = ["Market Low", "EMA_MACD_RSI_VOLUME_V2"]
    _strat_cur  = st.session_state.get("strategy", "Market Low")
    if _strat_cur not in _strat_opts:
        _strat_cur = "Market Low"
    strat_pick = st.selectbox(
        "Live strategy", _strat_opts,
        index=_strat_opts.index(_strat_cur),
        help="Market Low is the default live path. EMA_MACD_RSI_VOLUME_V2 is "
             "research-validated at 4h (ETH only).",
    )
    st.session_state.strategy = strat_pick
    if strat_pick == "EMA_MACD_RSI_VOLUME_V2":
        # Research-validated cell is V2 @ 4h ONLY — lock the interval to 4h so the
        # live gate (is_strategy_validated) can authorize it.
        st.session_state.interval = "4h"
        st.markdown(
            '<div style="background:#15233a;border:1px solid #1f6feb;border-radius:6px;'
            'padding:8px 10px;margin:4px 0;color:#a9c7ff;font-weight:700;font-size:13px;">'
            '📈 EMA · MACD · RSI · VOLUME V2 · 4h'
            '<div style="font-size:10px;font-weight:600;color:#79c0ff;'
            'margin-top:4px;letter-spacing:0.5px;">'
            'Research-validated · trend + momentum + volume · ATR SL/TP'
            '</div></div>',
            unsafe_allow_html=True,
        )
        # ── Live research-validation banner (Task #19) ──────────────────────
        # Show which symbols the research allowlist authorizes for V2/4h. Only
        # validated symbols (ETH today) will place LIVE orders; the rest are
        # blocked fail-closed by is_strategy_validated in the live engine.
        try:
            from research import is_strategy_validated as _isv
            _ok_syms, _blocked_syms = [], []
            for _s in (st.session_state.get("active_symbols")
                       or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
                _allow, _ = _isv("EMA_MACD_RSI_VOLUME_V2", "4h", _s)
                (_ok_syms if _allow else _blocked_syms).append(
                    _s.replace("USDT", ""))
            _ok_txt = (("✅ Enabled: " + ", ".join(_ok_syms))
                       if _ok_syms else "⚠️ No symbol validated yet")
            _bl_txt = (" · 🔒 Blocked: " + ", ".join(_blocked_syms)
                       if _blocked_syms else "")
            _bg = "#11251a" if _ok_syms else "#2d1a1a"
            _bd = "#2ea043" if _ok_syms else "#f85149"
            _fg = "#7ee787" if _ok_syms else "#ff9c9c"
            st.markdown(
                f'<div style="background:{_bg};border:1px solid {_bd};'
                f'border-radius:6px;padding:7px 10px;margin:4px 0;color:{_fg};'
                f'font-weight:700;font-size:12px;">{_ok_txt}{_bl_txt}'
                '<div style="font-size:10px;font-weight:600;color:#8b949e;'
                'margin-top:3px;">Live trades only on research-validated symbols. '
                'Risk caps · emergency stop · manual-trade protection still apply.'
                '</div></div>',
                unsafe_allow_html=True,
            )
        except Exception:
            pass
        st.caption("LONG when EMA50>EMA200, MACD hist>0, RSI>50, volume>1.2×20-avg. "
                   "Stop-loss / take-profit sized from ATR. Needs ≥200 candles — "
                   "uses a deeper kline fetch. Validated at 4h only.")
    else:
        st.session_state.strategy = "Market Low"
        st.markdown(
            '<div style="background:#15233a;border:1px solid #1f6feb;border-radius:6px;'
            'padding:8px 10px;margin:4px 0;color:#a9c7ff;font-weight:700;font-size:13px;">'
            '💧 MARKET LOW'
            '<div style="font-size:10px;font-weight:600;color:#79c0ff;'
            'margin-top:4px;letter-spacing:0.5px;">'
            'The only live strategy · tune it in the 💧 Market Low panel below'
            '</div></div>',
            unsafe_allow_html=True,
        )
        st.caption("BUY on a market-low setup with volume + trend confirmation, sell at "
                   "target profit, stop-loss to cap the downside. Configure every "
                   "number in the 💧 Market Low Strategy panel.")

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Bot — LIVE only
    st.markdown('<div class="sec-lbl">Bot</div>', unsafe_allow_html=True)
    st.caption("⚡ Bot places **real** Binance Mainnet orders on every signal.")

    # The live cadence is driven by the 🔥 Aggressive toggle in the dip panel
    # (2s ON / 6s OFF). check_every / threshold are kept only to satisfy
    # create_bot(); the dip loop sets its own tick and ignores them.
    ck_val  = int(st.session_state.check_every)
    thr_val = float(st.session_state.threshold)
    st.session_state.ai_aggressiveness = "Active Scalper"

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("▶ Start", width="stretch", disabled=bot_running):
            c = _cl()
            if c is None:
                st.error("Connect to Binance first — LIVE bot requires API keys.")
            else:
                # Build per-symbol risk managers from overrides (fallback = shared)
                _per_sym_rm = {}
                for _s in st.session_state.active_symbols:
                    _ov = st.session_state.per_symbol_risk.get(_s)
                    _per_sym_rm[_s] = RiskManager(_ov) if _ov else st.session_state.risk_manager
                _global_rm = GlobalRiskManager(st.session_state.global_risk)
                b = bot_module.create_bot(
                    client=c,
                    symbols=st.session_state.active_symbols,
                    per_symbol_risk=_per_sym_rm,
                    global_risk=_global_rm,
                    strategy=st.session_state.strategy,
                    risk_manager=st.session_state.risk_manager,
                    interval=intv_sel,
                    check_every=ck_val,
                    threshold=thr_val / 100,
                    initial_balance=st.session_state.initial_balance,
                    ai_assist=True,                       # ACTIVE SCALPER — always on
                    ai_aggressiveness="Active Scalper",   # ignored — single mode
                    manage_manual_trades=bool(getattr(st.session_state.global_risk,
                                                      "manage_manual_trades", False)),
                )
                am.apply_profile_to_bot(b, st.session_state.aggressive_mode)
                b._initial_balance = st.session_state.initial_balance
                b.start()
                # Refresh-proof: clear stop flag so auto-resume re-launches on
                # next cold start.
                st.session_state.bot_was_running   = True
                st.session_state._user_stopped_bot = False
                st.rerun()
    with bc2:
        if st.button("⏹ Stop", width="stretch", disabled=not bot_running):
            bot_module.stop_bot()
            st.session_state.bot_was_running   = False
            # ACTIVE SCALPER auto-resume: explicit Stop suppresses auto-launch
            # on next cold start. ▶ Start clears this flag.
            st.session_state._user_stopped_bot = True
            st.rerun()

    if st.button("🚨 Emergency Stop", width="stretch", type="secondary"):
        st.session_state.risk.emergency_stop = True
        bot_module.stop_bot()
        # Unified stop semantics — same as ⏹ Stop and toolbar OFF: suppress
        # auto-resume on next cold start until operator explicitly restarts.
        st.session_state.bot_was_running   = False
        st.session_state._user_stopped_bot = True
        log_activity("WARNING", "🚨 EMERGENCY STOP activated — all trading halted")
        tg.bot_event("emergency", "All trading halted by user")
        st.rerun()

    if st.session_state.risk.emergency_stop:
        st.error("🚨 Emergency stop ACTIVE")
        if st.button("✅ Clear Emergency Stop", width="stretch"):
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
        _last_tick = get_shared_last_tick(symbol=st.session_state.symbol)
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

    # ── Investment mode selector (Fixed USDT vs % of Available) ──────────────
    if "invest_mode" not in st.session_state:
        st.session_state.invest_mode = "Fixed USDT"
    if "invest_pct"  not in st.session_state:
        st.session_state.invest_pct  = 5.0

    # Available = real Binance free USDT (or 0 if not connected)
    _avail_for_calc = binance_free_usdt if _binance_connected else 0.0
    st.session_state.invest_mode = st.radio(
        "Investment mode",
        ["Fixed USDT", "% of Available"],
        index=0 if st.session_state.invest_mode == "Fixed USDT" else 1,
        horizontal=True,
        help="Fixed = exact USDT per trade. % = portion of real free USDT on Binance. Bot NEVER uses your full balance.",
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

    st.markdown('<hr class="s-div"/>', unsafe_allow_html=True)

    # Risk
    st.markdown('<div class="sec-lbl">Risk Management</div>', unsafe_allow_html=True)
    r = st.session_state.risk

    # ── Trade sizing — ACTIVE SCALPER dynamic % of free USDT ────────────────
    if not hasattr(r, "dynamic_size_pct") or not r.dynamic_size_pct:
        r.dynamic_size_pct = 40.0
    # Live preview: project current size against current free USDT.
    try:
        _live_free = float(binance_free_usdt) if _binance_connected else 0.0
    except Exception:
        _live_free = 0.0
    _proj = min(_live_free * r.dynamic_size_pct / 100.0, _live_free * 0.75) if _live_free > 0 else 0.0
    _proj_str = f"${_proj:.2f}" if _proj >= 10.0 else f"<$10 ⚠️ ({_proj:.2f})"
    st.markdown(f"""
<div style="background:#0d1a2a;border:1px solid #2962ff44;border-radius:7px;padding:8px 11px;margin-bottom:8px;">
  <div style="font-size:9px;color:#6e7681;text-transform:uppercase;letter-spacing:.1em;margin-bottom:3px;">Per-trade size (projected)</div>
  <div style="font-size:18px;font-weight:700;color:#f0f6fc;font-family:'JetBrains Mono',monospace;">{_proj_str} <span style="font-size:11px;color:#6e7681;">USDT · {r.dynamic_size_pct:.0f}% of ${_live_free:.2f} free</span></div>
</div>""", unsafe_allow_html=True)

    r.dynamic_size_pct = st.slider(
        "% of free USDT per trade",
        5.0, 75.0, float(r.dynamic_size_pct), 5.0,
        help="Legacy sizing knob (does NOT drive the live Market Low orders — "
             "use the 💧 Market Low Strategy panel below for live sizing). "
             "Capped at 75% of free USDT (always leaves a buffer). Floored at $10.",
    )
    r.max_trades_per_session = st.number_input(
        "Max bot trades / session",
        min_value=0, max_value=500,
        value=int(r.max_trades_per_session), step=1,
        help="Bot stops opening new trades after this count. 0 = unlimited.",
    )

    # ── 💰 BOT SPENDING LIMIT (fixed $) ───────────────────────────────────────
    # The MOST of YOUR money the bot may have in open trades at once. Each new
    # trade is sized down to fit what's left; the bot stops opening trades once
    # it's fully used. The rest of your balance is never touched. 0 = no limit.
    _g = st.session_state.global_risk
    _g.max_total_exposure_usdt = st.number_input(
        "💰 Bot spending limit (USDT)",
        min_value=0.0, max_value=1_000_000.0,
        value=float(getattr(_g, "max_total_exposure_usdt", 0.0) or 0.0), step=10.0,
        help="The most of your money the bot is allowed to have in open trades "
             "at once (across BTC/ETH/SOL, counting bot + manual positions). "
             "Each trade is sized to fit what's left of this budget; the bot "
             "stops opening new trades once it's fully used. 0 = no limit (the "
             "bot can use all your available USDT).",
    )
    if _g.max_total_exposure_usdt > 0:
        st.caption(f"🔒 Bot capped at **${_g.max_total_exposure_usdt:,.0f}** of "
                   f"your money in play at once — the rest stays untouched.")
    else:
        st.caption("⚠️ No limit set — the bot can use ALL your available USDT.")

    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
    # Min 0.1 + step 0.1 so PRO FAST SCALPER preset (SL 0.4 / TP 0.5) and
    # other sub-0.5% scalping setups don't trip Streamlit's value-below-min
    # validation after the preset rerun.
    r.stop_loss_pct      = st.slider("Stop loss %",     0.1, 20.0,
        max(0.1, float(r.stop_loss_pct)),   0.1)
    r.take_profit_pct    = st.slider("Take profit %",   0.1, 50.0,
        max(0.1, float(r.take_profit_pct)), 0.1)
    r.max_daily_loss_pct = st.slider("Max daily loss %",1.0, 30.0, float(r.max_daily_loss_pct), 0.5)
    r.max_open_trades    = st.slider("Max open trades", 1,   50,
        min(50, max(1, int(r.max_open_trades))),      1)
    st.session_state.risk_manager.settings = r

    # ── Global (account-wide) risk caps — applies across all symbols ──────────
    # ── 🔥 Aggressive Mode ────────────────────────────────────────────────────
    with st.expander("💧 Market Low Strategy (LIVE)", expanded=True):
        _ls = st.session_state.get("live_settings") or live_settings.LiveSettings()
        st.caption(
            f"**The only live strategy.** BUY when the market-low change ≤ "
            f"**{_ls.buy_threshold_pct:.2f}%** · SELL at "
            f"**+{_ls.take_profit_pct:.2f}%** profit · STOP-LOSS at "
            f"**{_ls.stop_loss_pct:.2f}%** · then a **1-min** cooldown after "
            f"BOTH a stop-loss and a sell. BUY also needs volume ≥ "
            f"**{_ls.min_volume_multiple:.1f}×** avg + trend filter."
        )
        if not live_settings.db_available():
            st.caption("⚠️ Database unavailable — settings use defaults and may "
                       "not persist across restart.")

        # Three operator-facing sizing modes. They each affect the LIVE order
        # size: the dip engine reads these settings every cycle. (Legacy AUTO is
        # kept in the enum for backward-compat but folded into "All available".)
        _offer = [live_settings.SIZE_FIXED, live_settings.SIZE_PERCENT,
                  live_settings.SIZE_ALL]
        _mode_labels = {
            live_settings.SIZE_FIXED:   "Fixed USDT — a fixed $ amount per trade",
            live_settings.SIZE_PERCENT: "% of available — a % of free USDT per trade",
            live_settings.SIZE_ALL:     "Use 100% of free USDT — deploy the entire free balance",
        }
        _cur = live_settings.normalize_size_mode(_ls.size_mode)
        if _cur not in _offer:
            _cur = live_settings.SIZE_ALL          # legacy AUTO → All available
        _new_mode = st.selectbox(
            "Position size mode", _offer,
            index=_offer.index(_cur),
            format_func=lambda m: _mode_labels.get(m, m),
            key="dip_size_mode",
        )
        _ls.size_mode = _new_mode
        if _new_mode == live_settings.SIZE_FIXED:
            _ls.fixed_usdt_amount = float(st.number_input(
                "Fixed amount per trade (USDT)",
                min_value=10.0, value=float(_ls.fixed_usdt_amount), step=5.0,
                key="dip_fixed_usdt",
            ))
        elif _new_mode == live_settings.SIZE_PERCENT:
            _ls.portfolio_percent = float(st.slider(
                "Portfolio % per trade", 1.0, 100.0,
                float(_ls.portfolio_percent), 1.0, key="dip_portfolio_pct",
            ))
        else:  # SIZE_ALL
            st.caption("Deploys **100% of free USDT** on each trade — the 25% "
                       "reserve and the max-position-% cap are bypassed so a small "
                       "balance can still meet the Binance $10 minimum. Only the "
                       "Binance minimum and any explicit spending / hard-$ limit "
                       "below still apply.")

        st.markdown("**Limits**")
        _ls.bot_spending_limit_usdt = float(st.number_input(
            "Bot spending limit (USDT, 0 = off)",
            min_value=0.0, value=float(_ls.bot_spending_limit_usdt), step=10.0,
            help="Max total USDT the bot may have deployed across all open "
                 "trades at once.", key="dip_spend_limit",
        ))
        _ls.max_position_pct = float(st.slider(
            "Max position size (% of free USDT)", 1.0, 100.0,
            float(getattr(_ls, "max_position_pct", 50.0)), 1.0,
            help="FINAL RULE: a single trade is capped at this % of free USDT.",
            key="dip_max_pos_pct",
        ))
        _ls.max_position_size_usdt = float(st.number_input(
            "Max position size (USDT, 0 = off)",
            min_value=0.0, value=float(_ls.max_position_size_usdt), step=10.0,
            help="Optional hard $ cap on a single trade's notional.", key="dip_max_pos",
        ))
        _ls.min_trade_size_usdt = float(st.number_input(
            "Min trade size (USDT)",
            min_value=10.0, value=float(_ls.min_trade_size_usdt), step=1.0,
            help="Binance min-notional is ~$10.", key="dip_min_pos",
        ))

        # ── Calculated next-order-size preview (the REAL number the dip engine
        #    will use this cycle: mode → max-position cap → spending limit →
        #    25% reserve → $10 min-notional floor). ────────────────────────────
        _prev_free     = float(binance_free_usdt) if _binance_connected else 0.0
        _prev_exposure = sum((t.get("invested") or 0) for t in open_trades)
        try:
            _amt, _ok, _why = live_engine.compute_order_amount(
                _ls, _prev_free, _prev_exposure)
        except Exception as _ce:
            _amt, _ok, _why = 0.0, False, f"preview error: {_ce}"
        _mode_short = _mode_labels.get(_new_mode, _new_mode).split(" — ")[0]
        _binance_min = max(float(_ls.min_trade_size_usdt or 0.0),
                           live_engine.BINANCE_MIN_NOTIONAL)
        if not _binance_connected:
            st.info("📐 **Order sizing preview:** connect to Binance LIVE to see "
                    "free USDT and the calculated order size.")
        else:
            st.markdown("**📐 Order sizing preview**")
            _pc1, _pc2 = st.columns(2)
            _pc1.metric("Free USDT", f"${_prev_free:,.2f}")
            _pc2.metric("Binance minimum", f"${_binance_min:,.2f}")
            _pc3, _pc4 = st.columns(2)
            _pc3.metric("Selected sizing mode", _mode_short)
            _pc4.metric("Calculated order size",
                        f"${_amt:,.2f}" if _ok else "—")
            if _ok:
                st.success(
                    f"✅ **CAN TRADE** — next order ≈ **${_amt:,.2f}** from "
                    f"${_prev_free:,.2f} free USDT "
                    f"(${_prev_exposure:,.2f} already in open trades). "
                    f"Click 💾 Save below so the live bot uses these settings on "
                    f"its next BUY.")
            else:
                st.error(f"⛔ **CANNOT TRADE** — {_why}")
            st.caption("This reflects sizing only. A live BUY is also gated by "
                       "Emergency Stop, Safe Mode, the daily-loss limit, "
                       "cooldowns and Binance exchange checks.")

        _c1, _c2 = st.columns(2)
        with _c1:
            _ls.aggressive_on = st.checkbox(
                "🔥 Aggressive", value=bool(_ls.aggressive_on),
                key="dip_aggressive",
                help="ON (default): scan every 2s. OFF: calmer 6s cadence.",
            )
        with _c2:
            _ls.safe_mode = st.checkbox(
                "🛡️ Safe mode", value=bool(_ls.safe_mode),
                key="dip_safe_mode",
                help="ON: bot opens NO new trades (existing TP/SL still run).",
            )

        if st.button("💾 Save dip settings", key="dip_save", use_container_width=True):
            st.session_state.live_settings = _ls
            if live_settings.save_settings(_ls, actor="dashboard"):
                st.success("Saved to database.")
            else:
                st.warning("Saved in session only — database unavailable.")
        st.session_state.live_settings = _ls

    with st.expander("🔥 Trading intensity", expanded=False):
        # SINGLE SOURCE OF TRUTH: the persisted mode (DB → JSON fallback).
        # The selectbox widget and the description below ALWAYS reflect the same
        # persisted value, so they can never disagree (the old "Very Aggressive
        # vs Balanced" contradiction). The widget key holds the live selection;
        # a successful save reruns with the new persisted value, a failed save
        # snaps the widget back — either way both stay consistent.
        _persisted = am.normalize_mode(st.session_state.get("aggressive_mode"))
        if "aggro_intensity_sel" not in st.session_state:
            st.session_state.aggro_intensity_sel = _persisted

        if st.session_state.pop("_aggro_save_error", False):
            st.error("Could not persist trading intensity (no database AND no "
                     "writable file) — kept the previous mode.")

        _sel_mode = st.selectbox(
            "Trading intensity",
            am.MODES,
            key="aggro_intensity_sel",
            help="Higher intensity = more trades and larger size. Safety limits "
                 "(risk caps, spending limits, safe mode) ALWAYS apply and are "
                 "never bypassed.",
        )
        if _sel_mode != _persisted:
            if am.set_mode(_sel_mode, actor="dashboard"):
                st.session_state.aggressive_mode = _sel_mode
                _pp = am.get_profile(_sel_mode)
                st.session_state.check_every = int(_pp["check_every"])
                am.apply_profile_to_risk(st.session_state.risk, _sel_mode)
                for _rs in (st.session_state.get("per_symbol_risk") or {}).values():
                    am.apply_profile_to_risk(_rs, _sel_mode)
                st.session_state.risk_manager.settings = st.session_state.risk
                _rb = bot_module.get_bot()
                if _rb and _rb.is_running():
                    am.apply_profile_to_bot(_rb, _sel_mode)
            else:
                # Snap the widget back to the persisted mode → no mixed state.
                st.session_state.aggro_intensity_sel = _persisted
                st.session_state["_aggro_save_error"] = True
            st.rerun()

        # Description is driven by the SAME persisted value the selectbox shows.
        _cur_mode = am.normalize_mode(st.session_state.get("aggressive_mode"))
        _p = am.get_profile(_cur_mode)
        st.caption(f"**{_cur_mode}** — {am.MODE_DESCRIPTIONS[_cur_mode]}")
        st.markdown(
            f"- Min AI confidence to trade: **≥ {int(_p['confidence_floor'])}**\n"
            f"- Signal score bar: **{int(_p['score_threshold_base'])}** "
            f"(floor {int(_p['score_threshold_floor'])})\n"
            f"- Requested size: **{_p['dynamic_size_pct']:.0f}% of free USDT** "
            f"(still capped by your risk limits)\n"
            f"- Scan every **{int(_p['check_every'])}s**, "
            f"**{int(_p['global_throttle_sec'])}s** between trades, "
            f"re-entry cooldown **{int(_p['cooldown_seconds'])}s**"
        )
        st.caption("🔒 Never bypasses: risk caps · max position size · spending "
                   "limits · safe mode · exchange safety checks.")
        if not am.db_available():
            st.caption("ℹ️ No database — the selected mode is saved to a local "
                       "file (data/aggressive_mode.json) and DOES persist across "
                       "restart.")
        _audit = am.get_audit_log(limit=5)
        if _audit:
            st.markdown("**Recent mode changes**")
            for _row in _audit:
                _when = _row.get("changed_at")
                _ws = (_when.strftime("%Y-%m-%d %H:%M")
                       if hasattr(_when, "strftime") else str(_when))
                st.caption(f"• {_ws} — {_row.get('old_mode') or '—'} → "
                           f"**{_row.get('new_mode')}** "
                           f"({_row.get('actor') or '?'})")

    with st.expander("🌐 Global risk (account-wide)", expanded=False):
        g = st.session_state.global_risk
        st.caption(
            "💰 Bot spending limit: "
            + (f"**${g.max_total_exposure_usdt:,.0f}**" if g.max_total_exposure_usdt > 0
               else "**OFF** (no limit)")
            + " — set it above under **Risk Management → 💰 Bot spending limit**."
        )
        g.max_exposure_per_symbol_pct = st.slider(
            "Max % of exposure in one symbol",
            10, 100, int(g.max_exposure_per_symbol_pct), 5,
            help="No single symbol can exceed this % of total open exposure.",
        )
        g.max_open_trades_total = st.slider(
            "Max open trades (total, all symbols)",
            1, 50, int(g.max_open_trades_total), 1,
            help="Hard cap on simultaneous open positions across all symbols. "
                 "Saved automatically and kept across refresh/restart.",
        )
        g.max_daily_loss_pct = st.slider(
            "Global max daily loss %",
            1.0, 30.0, float(g.max_daily_loss_pct), 0.5,
            help="Auto-stop the orchestrator when today's PnL ≤ −X%.",
        )
        # ── MANUAL TRADES PROTECTION ──────────────────────────────────────────
        g.manage_manual_trades = st.checkbox(
            "Allow bot to manage manual trades",
            value=bool(getattr(g, "manage_manual_trades", False)),
            key="cb_manage_manual",
            help="OFF (default & recommended): the bot NEVER closes positions "
                 "you opened manually — no auto-SL/TP/exit on them. ON: the bot "
                 "manages your manual trades with the same SL/TP rules as its own.",
        )
        if not g.manage_manual_trades:
            st.caption("🔒 Manual trades are protected — the bot will not close them.")
        else:
            st.caption("⚠️ Bot WILL auto-close your manual trades on SL/TP/exit.")

    # ── Per-symbol risk overrides ─────────────────────────────────────────────
    with st.expander("🎯 Per-symbol risk overrides", expanded=False):
        st.caption("Leave OFF to use the shared risk settings above.")
        for _sym in st.session_state.active_symbols:
            _en_key = f"pso_en_{_sym}"
            _has    = _sym in st.session_state.per_symbol_risk
            _enabled = st.checkbox(f"Override {_sym}", value=_has, key=_en_key)
            if _enabled:
                _o = st.session_state.per_symbol_risk.get(_sym) or RiskSettings(
                    invest_per_trade=r.invest_per_trade,
                    max_trade_usdt=r.max_trade_usdt,
                    stop_loss_pct=r.stop_loss_pct,
                    take_profit_pct=r.take_profit_pct,
                    max_open_trades=r.max_open_trades,
                )
                _c1, _c2 = st.columns(2)
                with _c1:
                    _o.invest_per_trade = st.number_input(
                        f"{_sym} invest/trade", 1.0, 100_000.0,
                        float(_o.invest_per_trade), 5.0, key=f"pso_inv_{_sym}")
                    _o.stop_loss_pct    = st.slider(
                        f"{_sym} SL %", 0.1, 20.0,
                        max(0.1, float(_o.stop_loss_pct)), 0.1, key=f"pso_sl_{_sym}")
                with _c2:
                    _o.max_trade_usdt   = st.number_input(
                        f"{_sym} hard cap", 0.0, 100_000.0,
                        float(_o.max_trade_usdt), 10.0, key=f"pso_cap_{_sym}")
                    _o.take_profit_pct  = st.slider(
                        f"{_sym} TP %", 0.1, 50.0,
                        max(0.1, float(_o.take_profit_pct)), 0.1, key=f"pso_tp_{_sym}")
                st.session_state.per_symbol_risk[_sym] = _o
            elif _has:
                st.session_state.per_symbol_risk.pop(_sym, None)

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
    if st.button("↺ Refresh Now", width="stretch"):
        st.rerun()
    if st.button("🗑 Reset All Data", width="stretch"):
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

    if st.button("📨 Send Test Notification", width="stretch"):
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

        if binance_balance_err:
            st.error(f"❌ Binance balance API failed: {binance_balance_err} — check API key permissions / IP whitelist / system clock")
        _bin_total_disp = "ERR"  if binance_balance_err else f"${binance_total_usdt:,.2f}"
        _bin_free_disp  = "ERR"  if binance_balance_err else f"${binance_free_usdt:,.2f}"
        _bin_sub_total  = ("⚠️ API ERROR" if binance_balance_err
                           else ('🟢 Live · free + locked' if _binance_connected else 'Not connected'))
        # Account Value card = TRUE total (USDT + coins). Falls back to USDT-only
        # if the per-coin valuation failed, so the card is never blank.
        _acct_disp = ("ERR" if account_value_err
                      else (f"${account_value_usdt:,.2f}" if account_value_usdt is not None
                            else _bin_total_disp))
        _acct_sub  = ("⚠️ price feed error" if account_value_err
                      else ("🟢 USDT + coins (live)" if account_value_usdt is not None
                            else _bin_sub_total))
        _bin_sub_free   = ("⚠️ API ERROR" if binance_balance_err
                           else ('Free for new orders' if _binance_connected else 'Connect API key'))
        if binance_balance_err:
            _bin_card_style = 'border-color:#ef535088;background:#2a0f0f;'

        st.markdown(f"""
<div class="cards">
  <div class="card" style="{_bin_card_style}">
    <div class="c-lbl">Account Value</div>
    <div class="c-val">{_acct_disp}</div>
    <div class="c-sub">{_acct_sub}</div>
  </div>
  <div class="card" style="{_bin_card_style}">
    <div class="c-lbl">Available (USDT)</div>
    <div class="c-val">{_bin_free_disp}</div>
    <div class="c-sub">{_bin_sub_free}</div>
  </div>
  <div class="card">
    <div class="c-lbl">Locked (USDT)</div>
    <div class="c-val">{f"${binance_locked_usdt:,.2f}" if _binance_connected else "—"}</div>
    <div class="c-sub">{f"In open orders · {len(open_trades)} positions" if _binance_connected else "Connect to Binance"}</div>
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

        # ── Holdings breakdown: show WHERE the money is (coins, not lost) ───────
        if account_holdings:
            _STABLES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")
            _hbits = []
            for _h in account_holdings[:6]:
                if _h["asset"] in _STABLES:
                    _hbits.append(f"{_h['amount']:,.2f} {_h['asset']}")
                else:
                    _hbits.append(f"{_h['amount']:,.6g} {_h['asset']} (${_h['value']:,.2f})")
            st.caption("💼 Held as:  " + "   ·   ".join(_hbits)
                       + "  —  money in coins isn't lost, it's just not USDT.")
        if account_unpriced:
            _ub = ", ".join(f"{_u['amount']:,.6g} {_u['asset']}" for _u in account_unpriced[:6])
            st.warning(f"⚠️ Couldn't get a live price for: {_ub}. These are NOT "
                       f"counted in Account Value above, so your real total may "
                       f"be a little higher than shown.")

        # ── Bot spending limit meter: how much of YOUR money is in play ─────────
        _bot_limit   = float(getattr(st.session_state.global_risk,
                                     "max_total_exposure_usdt", 0) or 0)
        _bot_in_play = sum((t.get("invested") or 0) for t in open_trades)
        if _bot_limit > 0:
            _bot_avail = max(0.0, _bot_limit - _bot_in_play)
            _bot_frac  = min(1.0, _bot_in_play / _bot_limit) if _bot_limit else 0.0
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;margin:4px 0 2px;flex-wrap:wrap;">'
                f'<span style="font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:.1em;font-weight:600;">💰 Bot Spending Limit</span>'
                f'<span style="font-size:13px;font-weight:700;font-family:\'JetBrains Mono\',monospace;color:#f0f6fc;">${_bot_in_play:,.2f} in play</span>'
                f'<span style="font-size:11px;color:#6e7681;">of ${_bot_limit:,.2f} limit · ${_bot_avail:,.2f} still free for the bot</span>'
                f'</div>',
                unsafe_allow_html=True)
            st.progress(_bot_frac)
        else:
            st.caption("💸 Bot spending limit: OFF — the bot can use all your "
                       "available USDT. Set a $ limit in the sidebar (Risk "
                       "Management) to cap how much it uses.")

        # ── Equity Curve Sparkline ─────────────────────────────────────────────
        _cum       = 0.0
        _spark_trades = sorted(
            [t for t in closed_trades if t.get("close_time")],
            key=lambda t: t["close_time"],
        )
        # Anchor point: first trade open time, or today's start if no trades yet
        if _spark_trades:
            _t0 = _to_london(_spark_trades[0].get("open_time") or _spark_trades[0]["close_time"])
        else:
            _t0 = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        _eq_times  = [_t0]
        _eq_vals   = [0.0]
        if _spark_trades:
            for _t in _spark_trades:
                _cum += _t.get("profit_loss") or 0
                _eq_times.append(_to_london(_t["close_time"]))
                _eq_vals.append(round(_cum, 4))
            # Extend to now with current unrealized P&L. REUSE unrealized_pnl
            # (computed above), which prices EACH open position with ITS OWN
            # coin's live price via _cur_price_for(). The old code here multiplied
            # EVERY open position by the single selected-symbol `live_price`, so
            # one open SOL/ETH trade priced as if it were BTC produced thousands
            # of dollars of fake "profit" (the bogus +$8,941 equity number).
            _eq_times.append(datetime.now(_TZ))
            _eq_vals.append(round(_cum + unrealized_pnl, 4))

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

        _sp_lbl = f'<span style="font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:.1em;font-weight:600;">Cumulative P&amp;L (real)</span>'
        _sp_val = f'<span style="font-size:13px;font-weight:700;font-family:\'JetBrains Mono\',monospace;color:{_spark_color};">{"+" if _eq_vals[-1]>=0 else ""}${_eq_vals[-1]:,.4f}</span>'
        _sp_cnt = f'<span style="font-size:10px;color:#484f58;">{len(_spark_trades)} closed trade{"s" if len(_spark_trades)!=1 else ""}</span>'
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:2px;">'
            f'{_sp_lbl}&nbsp;&nbsp;{_sp_val}&nbsp;&nbsp;{_sp_cnt}</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(_sfig, width="stretch", key="equity_spark_chart",
                        config={"displayModeBar": False, "staticPlot": False})

        # ── Chart toolbar ─────────────────────────────────────────────────────
        # Manual BUY/SELL controls were removed — the Market Low bot is the
        # only trader. Operator controls kept: emergency STOP, bot ON/OFF, refresh.
        tb1, tb4, tb5, tb6 = st.columns([7, 1, 1, 1])
        with tb1:
            mode_badge = '<span class="cbadge red">⚡ LIVE</span>'
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
  </div>
</div>
""", unsafe_allow_html=True)
        with tb4:
            emg_btn  = st.button("🚨 STOP", width="stretch", type="secondary")
        with tb5:
            bot_label = "⏹ Bot OFF" if bot_running else "⏩ Bot ON"
            if st.button(bot_label, width="stretch",
                          help="Toggle bot on/off"):
                if bot_running:
                    bot_module.stop_bot()
                    st.session_state.bot_was_running   = False
                    st.session_state._user_stopped_bot = True
                else:
                    c = _cl()
                    if c is None:
                        st.error("Connect to Binance first — LIVE bot requires API keys.")
                    else:
                        _per_sym_rm2 = {}
                        for _s in st.session_state.active_symbols:
                            _ov = st.session_state.per_symbol_risk.get(_s)
                            _per_sym_rm2[_s] = RiskManager(_ov) if _ov else st.session_state.risk_manager
                        b = bot_module.create_bot(
                            client=c,
                            symbols=st.session_state.active_symbols,
                            per_symbol_risk=_per_sym_rm2,
                            global_risk=GlobalRiskManager(st.session_state.global_risk),
                            strategy=st.session_state.strategy,
                            risk_manager=st.session_state.risk_manager,
                            interval=st.session_state.interval,
                            check_every=st.session_state.check_every,
                            threshold=st.session_state.threshold / 100,
                            initial_balance=st.session_state.initial_balance,
                            ai_assist=bool(st.session_state.get("ai_assist", False)),
                            ai_aggressiveness="Active Scalper",   # ACTIVE SCALPER MODE — ignored
                            manage_manual_trades=bool(getattr(st.session_state.global_risk,
                                                              "manage_manual_trades", False)),
                        )
                        am.apply_profile_to_bot(b, st.session_state.aggressive_mode)
                        b._initial_balance = st.session_state.initial_balance
                        b.start()
                st.rerun()
        with tb6:
            if st.button("↺", width="stretch", help="Refresh"):
                st.rerun()

        if emg_btn:
            st.session_state.risk.emergency_stop = True
            bot_module.stop_bot()
            st.session_state.bot_was_running   = False
            st.session_state._user_stopped_bot = True
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

            # ── Compute MACD inline (12/26/9) so the chart can show it ──────────
            if "macd" not in df_chart.columns and len(df_chart) >= 26:
                _ema12 = df_chart["close"].ewm(span=12, adjust=False).mean()
                _ema26 = df_chart["close"].ewm(span=26, adjust=False).mean()
                df_chart["macd"]        = _ema12 - _ema26
                df_chart["macd_signal"] = df_chart["macd"].ewm(span=9, adjust=False).mean()
                df_chart["macd_hist"]   = df_chart["macd"] - df_chart["macd_signal"]
            _has_macd  = "macd" in df_chart.columns
            _has_stoch = "stoch_k" in df_chart.columns

            # ── Timeframe pills (TradingView-style) ─────────────────────────────
            TF_PILLS = [("1m","1m"),("5m","5m"),("15m","15m"),("1h","1h"),("4h","4h")]
            _tfcols = st.columns([0.07]*len(TF_PILLS) + [0.65])
            for _i,(_lbl,_val) in enumerate(TF_PILLS):
                _active = (st.session_state.interval == _val)
                with _tfcols[_i]:
                    if st.button(("● " if _active else "") + _lbl,
                                 key=f"tf_pill_{_val}",
                                 type="primary" if _active else "secondary",
                                 width="stretch"):
                        st.session_state.interval = _val
                        st.rerun()
            with _tfcols[-1]:
                st.markdown(
                    f'<div style="text-align:right;font-size:11px;color:#6e7681;'
                    f'font-family:\'JetBrains Mono\',monospace;padding-top:9px;">'
                    f'<b style="color:#c9d1d9;">{st.session_state.symbol}</b> · '
                    f'<b style="color:#79b0ff;">{st.session_state.interval}</b> · '
                    f'{len(df_chart)} candles</div>',
                    unsafe_allow_html=True,
                )

            # ── Indicator toggles + view controls ───────────────────────────────
            _ind_defaults = {"show_volume": True, "show_ema": True,
                             "show_rsi": True, "show_macd": True, "show_stoch": True,
                             "show_old_trades": True, "show_sl_tp": True}
            for _k,_v in _ind_defaults.items():
                if _k not in st.session_state: st.session_state[_k] = _v

            DEFAULT_WINDOW_HOURS = 2            # Binance-like default: last 2h
            _win_key   = "chart_window_hours"
            _nonce_key = "chart_view_nonce"
            _follow_key = "chart_autofollow"
            _moff_key   = "chart_marker_offset_pct"
            if _win_key   not in st.session_state: st.session_state[_win_key]   = DEFAULT_WINDOW_HOURS
            if _nonce_key not in st.session_state: st.session_state[_nonce_key] = 0
            if _follow_key not in st.session_state: st.session_state[_follow_key] = True
            if _moff_key   not in st.session_state: st.session_state[_moff_key]   = 0.03
            # The visible window is ALWAYS applied as an explicit, bounded x-range
            # (driven by the Zoom in/out/Reset buttons below). We never leave the
            # axis on autorange, otherwise Plotly expands to fit old off-screen
            # trade markers and the chart "springs out" to many days on every
            # refresh. Buttons change `chart_window_hours`; that window persists
            # across auto-refreshes because it's re-applied each tick.

            # Row 1 — indicator toggles + Auto-follow.
            _c1,_c2,_c3,_c4,_c5,_c6,_c7,_csp,_cf = st.columns(
                [0.07,0.07,0.07,0.07,0.07,0.09,0.09, 0.04, 0.20]
            )
            with _c1:
                st.session_state.show_ema = st.checkbox(
                    "EMA", value=st.session_state.show_ema, key="cb_ema",
                    help="EMA 9 + EMA 21 overlay")
            with _c2:
                st.session_state.show_volume = st.checkbox(
                    "Vol", value=st.session_state.show_volume, key="cb_vol",
                    help="Volume bars at bottom of price panel")
            with _c3:
                st.session_state.show_rsi = st.checkbox(
                    "RSI", value=st.session_state.show_rsi, key="cb_rsi",
                    help="RSI 14 sub-panel")
            with _c4:
                st.session_state.show_macd = st.checkbox(
                    "MACD", value=st.session_state.show_macd, key="cb_macd",
                    help="MACD 12/26/9 sub-panel")
            with _c5:
                st.session_state.show_stoch = st.checkbox(
                    "Stoch", value=st.session_state.show_stoch, key="cb_stoch",
                    help="Stochastic %K/%D sub-panel")
            with _c6:
                st.session_state.show_old_trades = st.checkbox(
                    "Trades", value=st.session_state.show_old_trades, key="cb_old_tr",
                    help="Show historical BUY/SELL/EXIT markers from past trades")
            with _c7:
                st.session_state.show_sl_tp = st.checkbox(
                    "SL/TP", value=st.session_state.show_sl_tp, key="cb_sltp",
                    help="Show stop-loss / take-profit dashed lines for OPEN positions")
            with _cf:
                st.session_state[_follow_key] = st.checkbox(
                    "🛰 Auto-follow latest", value=st.session_state[_follow_key],
                    key="cb_autofollow",
                    help="ON: chart tracks the newest candle. OFF: chart is frozen "
                         "where you left it — it never moves on refresh, bot scan, "
                         "or price update (zoom/pan stays exactly put, like Binance).")

            # Row 2 — zoom / reset buttons + marker offset.
            _zi,_zo,_zr2,_zr24,_msp,_mo = st.columns(
                [0.12,0.12,0.14,0.15, 0.04, 0.30]
            )
            with _zi:
                if st.button("➕ Zoom in", key="chart_zoom_in_btn", width="stretch",
                             help="Halve the visible window"):
                    st.session_state[_win_key] = max(0.25, st.session_state[_win_key] / 2)
                    st.session_state[_nonce_key] += 1
                    st.rerun()
            with _zo:
                if st.button("➖ Zoom out", key="chart_zoom_out_btn", width="stretch",
                             help="Double the visible window"):
                    st.session_state[_win_key] = min(720, st.session_state[_win_key] * 2)
                    st.session_state[_nonce_key] += 1
                    st.rerun()
            with _zr2:
                if st.button("⟲ Reset 2h", key="chart_reset_2h_btn", width="stretch",
                             help="Reset view to the last 2 hours"):
                    st.session_state[_win_key]   = 2
                    st.session_state[_nonce_key] += 1
                    st.rerun()
            with _zr24:
                if st.button("⟲ Reset 24h", key="chart_reset_24h_btn", width="stretch",
                             help="Reset view to the last 24 hours"):
                    st.session_state[_win_key]   = 24
                    st.session_state[_nonce_key] += 1
                    st.rerun()
            with _mo:
                _moff_val = min(0.20, max(0.01, float(st.session_state[_moff_key])))
                st.session_state[_moff_key] = st.slider(
                    "Marker offset %", min_value=0.01, max_value=0.20,
                    value=_moff_val, step=0.01,
                    key="sl_marker_offset", format="%.2f",
                    help="How far the BUY/SELL arrows sit from the candle "
                         "(% of price). BUY below, SELL above.")

            # Bound the visible window to the candle data we actually have, so
            # zooming out reveals REAL price history (max ~the fetched range)
            # instead of empty space with old trade markers floating in it.
            # _view_end = latest candle; _view_start = `window` hours back but
            # never earlier than the oldest candle we hold.
            _win_hours = float(st.session_state[_win_key])
            # Chart timezone of the candle axis (naive Europe/London wall-clock,
            # so `_xtz` is normally None). Used only for the view-window fallback
            # below; trade-marker placement is handled by chart_markers, which
            # does its own axis-tz coercion.
            try:
                _xtz = df_chart["open_time"].dt.tz
            except Exception:
                _xtz = None
            try:
                _data_start = df_chart["open_time"].min()
                _data_end   = df_chart["open_time"].max()
            except Exception:
                _data_start = _data_end = None
            # ── Visible window: Auto-follow vs frozen ──────────────────────────
            # The x-range we hand Plotly is stored in session_state and reused on
            # rerun. Two modes:
            #   • Auto-follow ON  → recompute every tick so _view_end tracks the
            #     newest candle (chart follows live price).
            #   • Auto-follow OFF → reuse the saved range; _view_end does NOT
            #     advance, so the chart never moves on refresh / scan / price tick.
            # In BOTH modes uirevision is stable across reruns, so once the user
            # scroll-zooms or pans, Plotly preserves that exact view and ignores
            # the range we pass — the view only snaps to a fresh range when the
            # nonce changes (Zoom in/out / Reset 2h / Reset 24h) or symbol/
            # interval changes. We force a recompute on those events too.
            _autofollow = bool(st.session_state[_follow_key])
            _nonce      = st.session_state[_nonce_key]
            _saved_x    = st.session_state.get("chart_saved_xrange")
            _saved_sig  = st.session_state.get("chart_saved_sig")
            _cur_sig    = (st.session_state.symbol, st.session_state.interval, _nonce)
            _need_fresh = (
                _autofollow                       # following: always re-center
                or _saved_x is None               # nothing saved yet
                or _saved_sig != _cur_sig         # reset / zoom-btn / symbol / interval
            )

            def _compute_window():
                if _data_start is not None and _data_end is not None:
                    # Pad the right edge slightly past the last candle so a trade
                    # placed RIGHT NOW (a few seconds-to-a-minute after the last
                    # candle's open_time) is not clipped off the right edge.
                    _ve = _data_end + max(
                        pd.Timedelta(hours=_win_hours) * 0.05, pd.Timedelta(minutes=1))
                    _vs = max(_data_start, _data_end - pd.Timedelta(hours=_win_hours))
                else:
                    _ve = pd.Timestamp.now(tz=_xtz) if _xtz is not None else pd.Timestamp.now()
                    _vs = _ve - pd.Timedelta(hours=_win_hours)
                return _vs, _ve

            if _need_fresh:
                _view_start, _view_end = _compute_window()
                st.session_state["chart_saved_xrange"] = [_view_start, _view_end]
                st.session_state["chart_saved_sig"]    = _cur_sig
            else:
                _view_start, _view_end = _saved_x

            # ── Dynamic subplot layout based on enabled indicators ──────────────
            _panels = ["price"]
            if st.session_state.show_rsi   and _has_rsi:   _panels.append("rsi")
            if st.session_state.show_macd  and _has_macd:  _panels.append("macd")
            if st.session_state.show_stoch and _has_stoch: _panels.append("stoch")
            n_rows = len(_panels)
            # Price panel takes ≥70% of vertical so candles are large + readable.
            _sub_h = {1:[1.0], 2:[0.78,0.22], 3:[0.72,0.14,0.14], 4:[0.70,0.10,0.10,0.10]}[n_rows]
            fig = make_subplots(
                rows=n_rows, cols=1, shared_xaxes=True,
                row_heights=_sub_h, vertical_spacing=0.02,
            )
            _row = {p:(i+1) for i,p in enumerate(_panels)}

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

            _pr = _row["price"]

            # ── EMA 9 / 21 (subdued, toggleable) ───────────────────────────────
            if st.session_state.show_ema and "ema9" in df_chart.columns:
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["ema9"],
                    line=dict(color="rgba(239,83,80,0.85)", width=1.2),
                    name="EMA 9",
                    hovertemplate="EMA9: %{y:.4f}<extra></extra>",
                ), row=_pr, col=1)
            if st.session_state.show_ema and "ema21" in df_chart.columns:
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["ema21"],
                    line=dict(color="rgba(227,179,65,0.85)", width=1.2),
                    name="EMA 21",
                    hovertemplate="EMA21: %{y:.4f}<extra></extra>",
                ), row=_pr, col=1)

            # ── Volume histogram (scaled to bottom 18% of price panel) ──────────
            if st.session_state.show_volume and "volume" in df_chart.columns:
                _prange = df_chart["high"].max() - df_chart["low"].min()
                _vmax   = df_chart["volume"].max()
                if _prange > 0 and _vmax > 0:
                    _vscaled = df_chart["volume"] / _vmax * _prange * 0.18
                    _vbase   = df_chart["low"].min() - _prange * 0.02
                    _vcols   = [
                        "rgba(38,166,154,0.35)" if df_chart["close"].iloc[i] >= df_chart["open"].iloc[i]
                        else "rgba(239,83,80,0.35)"
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
                    ), row=_pr, col=1)

            # ── Live price line + right-edge price pill ────────────────────────
            if live_price:
                p_color = "#26a69a" if change_pct >= 0 else "#ef5350"
                fig.add_hline(
                    y=live_price, row=_pr, col=1,
                    line=dict(color=p_color, width=1, dash="dot"),
                )
                # Pill on the right axis — exact live price, always visible
                fig.add_annotation(
                    xref=f"x{_pr} domain" if _pr > 1 else "x domain",
                    yref=f"y{_pr}" if _pr > 1 else "y",
                    x=1.0, y=live_price,
                    text=f" ${live_price:,.4f} ",
                    showarrow=False,
                    font=dict(color="#0a0c10", size=11,
                              family="'JetBrains Mono',monospace"),
                    bgcolor=p_color, bordercolor=p_color, borderwidth=1,
                    xanchor="left", yanchor="middle",
                    row=_pr, col=1,
                )

            # ── Last candle emphasis (thin marker dot at close) ────────────────
            try:
                _lc_x = df_chart["open_time"].iloc[-1]
                _lc_y = df_chart["close"].iloc[-1]
                _lc_col = ("#26a69a" if df_chart["close"].iloc[-1] >= df_chart["open"].iloc[-1]
                           else "#ef5350")
                fig.add_trace(go.Scatter(
                    x=[_lc_x], y=[_lc_y], mode="markers",
                    marker=dict(symbol="circle", size=8, color=_lc_col,
                                line=dict(color="#0a0c10", width=2)),
                    showlegend=False, hoverinfo="skip",
                ), row=_pr, col=1)
            except Exception:
                pass

            # ── Trade markers (larger, clearer) ────────────────────────────────
            # ALL historical markers are added to the figure. They don't
            # stretch the visible x-axis because we set an explicit `range`
            # on the xaxis below — Plotly clips to that range on first paint
            # but the markers stay in the dataset, so panning/scrolling back
            # reveals every past BUY/SELL exit.
            # CRITICAL: filter to ONLY the currently-viewed symbol (handled inside
            # build_trade_markers via the `coin` field). Mixing ETH trades onto the
            # BTC chart blows out the Y-axis. Markers are resolved to the candle
            # nearest each trade timestamp by `chart_markers.build_trade_markers`,
            # which coerces UTC `open_time` and naive-local `close_time` to the
            # chart's naive Europe/London axis (the old inline coercion mis-placed
            # markers and a try/except:continue silently dropped them).
            _chart_sym = st.session_state.symbol
            _markers = build_trade_markers(
                all_trades, _chart_sym, df_chart["open_time"])
            # Stash for the debug panel + warning rendered just below the chart.
            st.session_state["_chart_marker_stats"] = {
                "symbol":        _chart_sym,
                "trades_found":  _markers.trades_found,
                "buy_drawn":     _markers.buy_drawn,
                "sell_drawn":    _markers.sell_drawn,
                "unmatched":     [(u.trade_id, u.kind, u.reason)
                                  for u in _markers.unmatched],
            }

            # GREEN BUY markers (up-arrow + "BUY" label) on the OPEN candle, and
            # RED SELL markers (down-arrow + "SELL" label) on the CLOSE candle.
            # Rendered when the "Trades" toggle is on (default ON) so they are
            # visible at every zoom level inside the current view.
            # Marker offset: nudge the arrow off the exact price so it doesn't
            # overlap the candle — BUY sits BELOW, SELL sits ABOVE, by
            # `marker_offset_pct` % of price. The REAL entry/exit price is carried
            # in customdata so the hover stays accurate.
            _moff = float(st.session_state[_moff_key]) / 100.0
            if st.session_state.show_old_trades:
                if _markers.buy:
                    fig.add_trace(go.Scatter(
                        x=[m.x for m in _markers.buy],
                        y=[m.y * (1.0 - _moff) for m in _markers.buy],
                        mode="markers+text",
                        marker=dict(symbol="triangle-up", size=16, color="#26a69a",
                                    line=dict(color="rgba(255,255,255,0.85)", width=1.4)),
                        text=["BUY"] * len(_markers.buy),
                        textposition="bottom center",
                        textfont=dict(color="#26a69a", size=10,
                                      family="'JetBrains Mono',monospace"),
                        name="BUY",
                        customdata=[[m.trade_id, m.y] for m in _markers.buy],
                        hovertemplate=("BUY %{customdata[0]}<br>%{x}<br>"
                                       "entry $%{customdata[1]:.4f}<extra></extra>"),
                    ), row=_pr, col=1)
                if _markers.sell:
                    fig.add_trace(go.Scatter(
                        x=[m.x for m in _markers.sell],
                        y=[m.y * (1.0 + _moff) for m in _markers.sell],
                        mode="markers+text",
                        marker=dict(symbol="triangle-down", size=16, color="#ef5350",
                                    line=dict(color="rgba(255,255,255,0.85)", width=1.4)),
                        text=["SELL"] * len(_markers.sell),
                        textposition="top center",
                        textfont=dict(color="#ef5350", size=10,
                                      family="'JetBrains Mono',monospace"),
                        name="SELL",
                        customdata=[[m.trade_id, m.y] for m in _markers.sell],
                        hovertemplate=("SELL %{customdata[0]}<br>%{x}<br>"
                                       "exit $%{customdata[1]:.4f}<extra></extra>"),
                    ), row=_pr, col=1)

            # ── SL / TP lines for every open position (toggle) ────────────────
            # Same symbol filter — don't draw ETH's SL/TP on the BTC chart.
            if st.session_state.show_sl_tp:
                _sym_open = [t for t in open_trades
                             if (t.get("coin") or "").upper() == _chart_sym.upper()]
                for _op in _sym_open:
                    _sl  = _op.get("stop_loss")
                    _tp  = _op.get("take_profit")
                    if _sl and float(_sl or 0) > 0:
                        fig.add_hline(y=_sl, row=_pr, col=1,
                            line=dict(color="rgba(239,83,80,0.55)", width=1, dash="dash"))
                    if _tp and float(_tp or 0) > 0:
                        fig.add_hline(y=_tp, row=_pr, col=1,
                            line=dict(color="rgba(38,166,154,0.55)", width=1, dash="dash"))

            # ── RSI panel ───────────────────────────────────────────────────────
            if "rsi" in _row:
                _rr = _row["rsi"]
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["rsi"],
                    line=dict(color="#b794f6", width=1.5), name="RSI 14",
                    hovertemplate="RSI: %{y:.1f}<extra></extra>",
                ), row=_rr, col=1)
                fig.add_hline(y=70, line=dict(color="rgba(239,83,80,0.45)", width=1, dash="dash"), row=_rr, col=1)
                fig.add_hline(y=30, line=dict(color="rgba(38,166,154,0.45)", width=1, dash="dash"), row=_rr, col=1)
                fig.add_hline(y=50, line=dict(color="rgba(110,118,129,0.25)", width=1, dash="dot"), row=_rr, col=1)

            # ── MACD panel ──────────────────────────────────────────────────────
            if "macd" in _row:
                _mr = _row["macd"]
                _hist_col = ["rgba(38,166,154,0.55)" if v >= 0 else "rgba(239,83,80,0.55)"
                             for v in df_chart["macd_hist"]]
                fig.add_trace(go.Bar(
                    x=df_chart["open_time"], y=df_chart["macd_hist"],
                    marker_color=_hist_col, marker_line_width=0,
                    name="Hist", showlegend=False,
                    hovertemplate="Hist: %{y:.4f}<extra></extra>",
                ), row=_mr, col=1)
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["macd"],
                    line=dict(color="#79b0ff", width=1.4), name="MACD",
                    hovertemplate="MACD: %{y:.4f}<extra></extra>",
                ), row=_mr, col=1)
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["macd_signal"],
                    line=dict(color="#e3b341", width=1.2, dash="dot"), name="Signal",
                    hovertemplate="Signal: %{y:.4f}<extra></extra>",
                ), row=_mr, col=1)
                fig.add_hline(y=0, line=dict(color="rgba(110,118,129,0.35)", width=1), row=_mr, col=1)

            # ── Stochastic panel (optional) ────────────────────────────────────
            if "stoch" in _row:
                _sr = _row["stoch"]
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["stoch_k"],
                    line=dict(color="#79b0ff", width=1.4), name="%K",
                    hovertemplate="%K: %{y:.1f}<extra></extra>",
                ), row=_sr, col=1)
                fig.add_trace(go.Scatter(
                    x=df_chart["open_time"], y=df_chart["stoch_d"],
                    line=dict(color="#c9d1d9", width=1.1, dash="dot"), name="%D",
                    hovertemplate="%D: %{y:.1f}<extra></extra>",
                ), row=_sr, col=1)
                fig.add_hline(y=80, line=dict(color="rgba(239,83,80,0.45)",  width=1, dash="dash"), row=_sr, col=1)
                fig.add_hline(y=20, line=dict(color="rgba(38,166,154,0.45)", width=1, dash="dash"), row=_sr, col=1)

            # ── Subtle corner labels for each sub-panel (no big titles) ────────
            _panel_lbl = {"rsi": "RSI 14", "macd": "MACD 12/26/9", "stoch": "Stoch 14"}
            for _p, _r in _row.items():
                if _p == "price": continue
                fig.add_annotation(
                    xref=f"x{_r} domain" if _r > 1 else "x domain",
                    yref=f"y{_r} domain" if _r > 1 else "y domain",
                    x=0.005, y=0.95, xanchor="left", yanchor="top",
                    text=_panel_lbl.get(_p, _p),
                    showarrow=False,
                    font=dict(size=9, color="#6e7681",
                              family="'JetBrains Mono',monospace"),
                    row=_r, col=1,
                )

            # ── Layout (TradingView-style: minimal, sparse grid) ───────────────
            G       = "rgba(48,54,61,0.35)"   # very subtle gridlines
            G_ZERO  = "rgba(48,54,61,0.55)"
            _heights = {1: 560, 2: 660, 3: 760, 4: 860}
            fig.update_layout(
                paper_bgcolor="#0a0c10",
                plot_bgcolor="#0a0c10",
                font=dict(color="#9ba3ad", family="'JetBrains Mono',monospace", size=10),
                xaxis_rangeslider_visible=False,
                height=_heights[n_rows],
                margin=dict(l=0, r=78, t=10, b=58),  # extra right margin for price pill
                showlegend=True,
                legend=dict(
                    bgcolor="rgba(13,17,23,0)",
                    bordercolor="rgba(0,0,0,0)", borderwidth=0,
                    font=dict(size=10, color="#9ba3ad"),
                    orientation="h",
                    yanchor="top", y=-0.06,
                    xanchor="center", x=0.5,
                    itemsizing="constant",
                ),
                hovermode="x unified",
                hoverlabel=dict(bgcolor="#161b22", bordercolor="#30363d",
                                font_color="#c9d1d9", font_size=11),
                dragmode="pan",
                uirevision=f"trade_chart_fixed-"
                           f"{st.session_state.symbol}-"
                           f"{st.session_state.interval}-"
                           f"{st.session_state['chart_view_nonce']}",
            )
            # X-axis: shared, sparse grid, scroll/pan unlocked, default 4h window
            for r in range(1, n_rows + 1):
                fig.update_xaxes(
                    gridcolor=G, gridwidth=1, zerolinecolor=G_ZERO,
                    showspikes=True, spikecolor="#484f58",
                    spikethickness=1, spikedash="dot", spikemode="across",
                    tickfont=dict(size=10, color="#6e7681"),
                    tickformat="%H:%M\n%b-%d",
                    hoverformat="%Y-%m-%d %H:%M:%S",
                    range=[_view_start, _view_end],
                    fixedrange=False, autorange=False,
                    nticks=8,
                    row=r, col=1,
                )
            # Price Y-axis — auto-fit to VISIBLE candles only (not full history),
            # with 3% padding above/below so wicks don't kiss the frame.
            try:
                # df_chart["open_time"] is tz-naive (Europe/London wall clock,
                # tz stripped at load). _view_start/_view_end may be tz-aware
                # if _xtz was set — coerce both sides to naive so the mask
                # never raises TypeError on mixed tz comparisons.
                _ot = df_chart["open_time"]
                if getattr(_ot.dt, "tz", None) is not None:
                    _ot = _ot.dt.tz_convert(None) if _ot.dt.tz else _ot
                _vs = _view_start.tz_localize(None) if getattr(_view_start, "tzinfo", None) else _view_start
                _ve = _view_end.tz_localize(None)   if getattr(_view_end,   "tzinfo", None) else _view_end
                _vis = df_chart[(_ot >= _vs) & (_ot <= _ve)]
                if len(_vis) >= 2:
                    _y_lo = float(_vis["low"].min())
                    _y_hi = float(_vis["high"].max())
                    _pad  = max((_y_hi - _y_lo) * 0.03, _y_hi * 0.0005)
                    _y_range = [_y_lo - _pad, _y_hi + _pad]
                else:
                    _y_range = None
            except Exception:
                _y_range = None
            _price_yaxis_kwargs = dict(
                gridcolor=G, gridwidth=1, zerolinecolor=G_ZERO,
                showspikes=True, spikecolor="#484f58",
                spikethickness=1, spikedash="dot",
                tickfont=dict(size=10, color="#9ba3ad"),
                tickprefix="$", side="right", nticks=8,
                fixedrange=False,
                row=_row["price"], col=1,
            )
            if _y_range is not None:
                _price_yaxis_kwargs["range"]     = _y_range
                _price_yaxis_kwargs["autorange"] = False
            else:
                _price_yaxis_kwargs["autorange"] = True
            fig.update_yaxes(**_price_yaxis_kwargs)
            # RSI / Stoch Y-axes: pinned 0–100, sparse ticks
            for _p in ("rsi", "stoch"):
                if _p in _row:
                    fig.update_yaxes(
                        gridcolor=G, gridwidth=1, range=[0, 100],
                        tickvals=[20, 50, 80] if _p == "stoch" else [30, 50, 70],
                        tickfont=dict(size=9, color="#6e7681"),
                        side="right", fixedrange=False,
                        row=_row[_p], col=1,
                    )
            # MACD Y-axis: autoscale, sparse
            if "macd" in _row:
                fig.update_yaxes(
                    gridcolor=G, gridwidth=1, zerolinecolor=G_ZERO,
                    tickfont=dict(size=9, color="#6e7681"),
                    side="right", nticks=4,
                    autorange=True, fixedrange=False,
                    row=_row["macd"], col=1,
                )

            # Stable key → Streamlit reuses the same DOM node across reruns (no flicker / no remount)
            # scrollZoom=True lets the user wheel/pinch-zoom freely; doubleClick="reset"
            # auto-restores the default 4h view; pan is the default drag.
            st.plotly_chart(
                fig, width="stretch", key="main_candle_chart",
                config={
                    "displayModeBar": True,
                    "displaylogo":    False,
                    "scrollZoom":     True,
                    "doubleClick":    "reset",
                    "modeBarButtonsToRemove": ["select2d", "lasso2d", "toImage"],
                },
            )

            # ── Chart markers debug panel + unmatched warning ──────────────────
            # Truthful accounting of how recorded trades map onto the chart so a
            # "markers missing" report is diagnosable at a glance instead of a
            # silent drop.
            _ms = st.session_state.get("_chart_marker_stats", {})
            if _ms.get("unmatched"):
                st.warning(
                    f"⚠️ Trade found but chart timestamp not matched — "
                    f"{len(_ms['unmatched'])} marker(s) could not be placed for "
                    f"{_ms.get('symbol','?')}. Zoom out or widen the window; see "
                    f"the Chart markers panel for details.")
            with st.expander(
                    f"🔍 Chart markers — {_ms.get('buy_drawn',0)} BUY · "
                    f"{_ms.get('sell_drawn',0)} SELL"
                    + (f" · {len(_ms.get('unmatched',[]))} unmatched"
                       if _ms.get('unmatched') else ""),
                    expanded=False):
                _dm1, _dm2, _dm3, _dm4 = st.columns(4)
                _dm1.metric("Trades found", _ms.get("trades_found", 0))
                _dm2.metric("BUY markers drawn", _ms.get("buy_drawn", 0))
                _dm3.metric("SELL markers drawn", _ms.get("sell_drawn", 0))
                _dm4.metric("Unmatched trades", len(_ms.get("unmatched", [])))
                st.caption(
                    f"Symbol {_ms.get('symbol','?')} · BUY anchored on `open_time`/"
                    f"`entry_price`, SELL on `close_time`/`exit_price`, each snapped "
                    f"to the nearest candle. Source: data/trades/*.json.")
                if _ms.get("unmatched"):
                    st.dataframe(
                        pd.DataFrame(_ms["unmatched"],
                                     columns=["trade id", "marker", "reason"]),
                        width="stretch", hide_index=True)
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
                                 width="stretch"):
                        c = _cl()
                        if c is None:
                            st.error("❌ Connect to Binance first — closes require a real counter-order.")
                        else:
                            _coin = ot["coin"]
                            _qty  = float(ot.get("quantity") or 0)
                            _side = ot.get("side", "BUY")
                            if _qty <= 0:
                                st.error("❌ Position has zero quantity — cannot place counter-order.")
                            else:
                                try:
                                    _q_rnd = c.round_quantity(_coin, _qty)
                                except Exception:
                                    _q_rnd = round(_qty, 6)
                                # Counter-order: long → SELL, short → BUY.
                                # Recorded close MUST come from Binance execution
                                # (extract_fill raises if Binance didn't fill) — never
                                # fall back to ticker. Fill-size guard mirrors
                                # SymbolWorker._close_position: >5% qty deviation
                                # leaves the trade OPEN for manual reconciliation.
                                from binance_client import extract_fill as _extract_fill
                                try:
                                    _counter_side = "SELL" if _side == "BUY" else "BUY"
                                    _order = c.place_market_order(_coin, _counter_side, _q_rnd)
                                    _exec_q, xp = _extract_fill(_order)
                                    # P2 REAL PnL — convert the real exit commission to
                                    # USDT via the shared helper (same logic the manual
                                    # OPEN paths use, so entry/exit fees stay consistent).
                                    _exit_fee = _order_fee_usdt(_order, _coin, xp)
                                except Exception as _e:
                                    st.error(f"❌ LIVE close order failed (NOT recorded): {_e}")
                                    log_activity("ERROR", f"👤 Close {ot['id']} FAILED on Binance: {_e}")
                                    st.stop()
                                if xp <= 0 or _exec_q <= 0:
                                    st.error(f"❌ Close response missing execution data — trade NOT closed.")
                                    log_activity("ERROR",
                                        f"👤 Close {ot['id']} aborted — invalid execution data "
                                        f"(qty={_exec_q}, price={xp}).")
                                    st.stop()
                                _dev = abs(_exec_q - _q_rnd) / _q_rnd if _q_rnd > 0 else 1.0
                                if _dev > 0.05:
                                    st.error(
                                        f"❌ Fill size mismatch — intended {_q_rnd}, "
                                        f"filled {_exec_q} (Δ {_dev*100:.2f}%). Trade "
                                        f"left OPEN for manual reconciliation.")
                                    log_activity("ERROR",
                                        f"👤 Close {ot['id']} qty mismatch — intended {_q_rnd}, "
                                        f"filled {_exec_q} (Δ {_dev*100:.2f}%). Trade OPEN.")
                                    st.stop()
                                _closed = close_trade(ot["id"], xp, "Manual close via dashboard (LIVE)", _exit_fee)
                                if not _closed:
                                    st.error("❌ Counter-order filled but persistence failed — manual reconciliation required.")
                                    log_activity("ERROR",
                                        f"👤 Close {ot['id']} — Binance filled {_counter_side} "
                                        f"{_exec_q} @ ${xp:.4f} but close_trade returned no record.")
                                    st.stop()
                                log_activity("ORDER",
                                    f"👤 Closed {ot['id']} | LIVE {_counter_side} {_exec_q:.6f} "
                                    f"{_coin} @ ${xp:.4f}")
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
                    width="stretch", hide_index=True, height=280,
                    column_config={
                        "Reason": st.column_config.TextColumn("Reason", width="large"),
                    },
                )

        with tab_a:
            # ── 💧 Dip strategy — per-symbol live decision panel ───────────────
            try:
                _dip_acts = live_engine.get_all_activity()
            except Exception:
                _dip_acts = []
            if _dip_acts:
                st.markdown("**💧 Market Low — live decisions**")
                _cols = st.columns(min(len(_dip_acts), 3))
                for _i, _rec in enumerate(_dip_acts):
                    with _cols[_i % len(_cols)]:
                        _chg = _rec.change_pct
                        _chg_cls = "up" if (_chg is not None and _chg >= 0) else "dn"
                        _chg_txt = f"{_chg:+.3f}%" if _chg is not None else "—"
                        _px = f"${_rec.price:,.4f}" if _rec.price else "—"
                        _ago = f"${_rec.price_20m_ago:,.4f}" if _rec.price_20m_ago else "—"
                        _amt = (f"${_rec.amount:,.2f}"
                                if getattr(_rec, "amount", None) else "—")
                        _pnl = getattr(_rec, "profit_pct", None)
                        _pnl_txt = (f'<span class="{"up" if _pnl >= 0 else "dn"}">'
                                    f'{_pnl:+.3f}%</span>'
                                    if _pnl is not None else "—")
                        _buy_t = getattr(_rec, "buy_threshold", None)
                        _tp_t = getattr(_rec, "take_profit", None)
                        _sl_t = getattr(_rec, "stop_loss", None)
                        _thr_txt = (
                            f'BUY ≤ {_buy_t:.2f}% · TP +{_tp_t:.2f}% · SL {_sl_t:.2f}%'
                            if None not in (_buy_t, _tp_t, _sl_t) else "—")
                        # Volume multiplier vs the required spike threshold.
                        _vr = getattr(_rec, "volume_ratio", None)
                        _vr_need = float(getattr(_rec, "min_volume_multiple", 1.5))
                        _vr_on = bool(getattr(_rec, "volume_filter_on", True))
                        if _vr is None:
                            _vol_txt = "vol —"
                        elif not _vr_on:
                            _vol_txt = f"vol {_vr:.2f}× (filter off)"
                        else:
                            _v_ok = _vr >= _vr_need
                            _vol_txt = (f'vol <b class="{"up" if _v_ok else "dn"}">'
                                        f'{_vr:.2f}×</b> (need ≥{_vr_need:.1f}×) '
                                        f'{"✓" if _v_ok else "✗"}')
                        # Trend-filter result.
                        _tok = getattr(_rec, "trend_ok", None)
                        _t_on = bool(getattr(_rec, "trend_filter_on", True))
                        if not _t_on:
                            _trend_txt = "trend (filter off)"
                        elif _tok is None:
                            _trend_txt = "trend —"
                        else:
                            _trend_txt = (f'trend <b class="up">✓ upturn</b>'
                                          if _tok else
                                          f'trend <b class="dn">✗ no upturn</b>')
                        st.markdown(
                            f'<div class="card">'
                            f'<div class="c-lbl">{_rec.symbol}</div>'
                            f'<div class="c-val {_chg_cls}">{_chg_txt}</div>'
                            f'<div style="font-size:11px;color:#8b949e;">'
                            f'now {_px} · 20m ago {_ago}<br>'
                            f'20m change <b>{_chg_txt}</b> · open PnL {_pnl_txt}<br>'
                            f'{_vol_txt} · {_trend_txt}<br>'
                            f'{_thr_txt}<br>'
                            f'last action <b>{_rec.decision}</b> · size {_amt}<br>'
                            f'{(_rec.reason or "")}'
                            f'</div></div>',
                            unsafe_allow_html=True,
                        )
                st.markdown("---")

            ac1, ac2 = st.columns([8, 1])
            with ac2:
                if st.button("🗑 Clear", width="stretch"):
                    clear_activity(); st.rerun()

            activity = load_activity()
            if not activity:
                st.markdown('<div style="background:#0d1117;border:1px solid #1e2736;border-radius:6px;padding:18px;color:#484f58;text-align:center;">No activity yet.</div>', unsafe_allow_html=True)
            else:
                lines = []
                for entry in reversed(activity[-300:]):
                    ts  = _fmt_london(entry.get("time"), "%Y-%m-%d %H:%M:%S")
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

            # ── 💸 Real P&L after ESTIMATED fees ─────────────────────────────
            # Binance spot fees are NOT recorded per trade, and the stored
            # profit_loss is the raw price difference (GROSS). A scalper pays a
            # fee on BOTH the entry and the exit, so true (net) results can be
            # far worse than the gross numbers above. This panel estimates fees
            # at the rate below so the operator sees net reality.
            st.markdown('<div class="sec-lbl" style="margin-top:18px;">💸 Real P&L — after estimated fees</div>', unsafe_allow_html=True)
            if "fee_rate_pct" not in st.session_state:
                st.session_state.fee_rate_pct = 0.10
            fee_rate_pct = st.number_input(
                "Binance fee per side (%) — 0.10 = standard spot taker · 0.075 = with BNB discount",
                min_value=0.0, max_value=1.0, step=0.005, format="%.3f",
                key="fee_rate_pct",
            )
            _fr = fee_rate_pct / 100.0
            if not closed_trades:
                st.markdown('<div style="background:#0d1117;border:1px solid #1e2736;border-radius:6px;padding:14px;color:#484f58;">No closed trades yet — net P&L appears once trades close.</div>', unsafe_allow_html=True)
            else:
                def _finite(x):
                    try:
                        v = float(x)
                        return v if np.isfinite(v) else 0.0
                    except (TypeError, ValueError):
                        return 0.0
                _gross = 0.0; _fees = 0.0; _net_wins = 0; _bad_rows = 0
                _net_gross_pnls = []   # per-trade gross used for win/loss stats
                def _is_bad(x):
                    if x is None:
                        return False   # missing is treated as 0, not "bad data"
                    try:
                        return not np.isfinite(float(x))
                    except (TypeError, ValueError):
                        return True
                _real_fee_rows = 0
                for t in closed_trades:
                    raw_g = t.get("gross_pnl", t.get("profit_loss"))
                    if _is_bad(raw_g):
                        _bad_rows += 1
                    g    = _finite(raw_g)
                    # P2 REAL PnL — use the REAL figure ONLY when BOTH commission
                    # legs were captured (fees_complete). A partial capture (e.g. a
                    # manual trade missing its entry fee, or a failed BNB→USDT
                    # conversion) would UNDERSTATE fees and OVERSTATE net, so we fall
                    # back to the honest rate-based estimate instead.
                    if t.get("fees_complete") and t.get("total_fees") is not None:
                        fee = _finite(t.get("total_fees"))
                        _real_fee_rows += 1
                    else:
                        qty  = _finite(t.get("quantity"))
                        entry_notional = _finite(t.get("invested"))
                        xp   = _finite(t.get("exit_price"))
                        exit_notional  = (xp * qty) if (xp > 0 and qty > 0) else entry_notional
                        fee  = (entry_notional + exit_notional) * _fr
                    _gross += g; _fees += fee
                    _net_gross_pnls.append(g)
                    if (g - fee) > 0:          # strict: a net-zero trade is NOT a win
                        _net_wins += 1
                _net    = _gross - _fees
                _n      = len(closed_trades)
                _net_wr = (_net_wins / _n * 100) if _n else 0.0
                # Data-driven breakeven win rate from avg gross win/loss + avg fee.
                _g_wins = [p for p in _net_gross_pnls if p > 0]
                _g_loss = [-p for p in _net_gross_pnls if p < 0]
                _avg_w  = (sum(_g_wins) / len(_g_wins)) if _g_wins else 0.0
                _avg_l  = (sum(_g_loss) / len(_g_loss)) if _g_loss else 0.0
                _avg_fee = (_fees / _n) if _n else 0.0
                _be_ok  = (_avg_w + _avg_l) > 0
                _be_wr  = (((_avg_l + _avg_fee) / (_avg_w + _avg_l)) * 100) if _be_ok else 0.0
                _be_str = f"{_be_wr:.1f}%" if _be_ok else "N/A"

                f1, f2, f3, f4, f5 = st.columns(5)
                _sc(f1, "Gross P&L",       _fmt_pnl(_gross),  "up" if _gross >= 0 else "dn")
                _sc(f2, "Est. Fees",       f"-${_fees:.4f}",  "dn")
                _sc(f3, "NET P&L",         _fmt_pnl(_net),    "up" if _net >= 0 else "dn")
                _sc(f4, "Net Win Rate",    f"{_net_wr:.1f}%", "up" if _net_wr >= 50 else "dn")
                _sc(f5, "Breakeven Needs", _be_str,           ("dn" if (_be_ok and _be_wr > _net_wr) else "up"))

                if _gross > 0 and _net < 0:
                    _verdict = (f"Your trades made <b>+${_gross:.2f} gross</b>, but fees of "
                                f"<b>${_fees:.2f}</b> turned that into "
                                f"<b style='color:#f85149;'>−${abs(_net):.2f} net</b>. "
                                f"Fees alone wiped out the profit.")
                    _vc = "#f85149"
                elif _net < 0:
                    _verdict = (f"Net loss of <b style='color:#f85149;'>−${abs(_net):.2f}</b> "
                                f"(gross {_fmt_pnl(_gross)} minus <b>${_fees:.2f}</b> fees).")
                    _vc = "#f85149"
                else:
                    _verdict = (f"Net profit of <b style='color:#3fb950;'>+${_net:.2f}</b> "
                                f"after <b>${_fees:.2f}</b> in fees.")
                    _vc = "#3fb950"
                if _be_ok:
                    _verdict += (f" At this fee rate you need a <b>{_be_wr:.1f}%</b> win rate just to "
                                 f"break even — you're currently at <b>{_net_wr:.1f}%</b> net.")
                st.markdown(
                    f'<div style="background:#0d1117;border:1px solid {_vc}55;'
                    f'border-left:3px solid {_vc};border-radius:6px;padding:12px 14px;'
                    f'margin-top:10px;color:#c9d1d9;font-size:13px;line-height:1.55;">'
                    f'{_verdict}</div>', unsafe_allow_html=True)
                if _bad_rows:
                    st.caption(f"⚠️ {_bad_rows} trade(s) had invalid/non-finite P&L and were "
                               f"treated as 0 — totals may be incomplete.")
                if _real_fee_rows >= _n and _n:
                    st.caption(f"✅ Fees are REAL Binance commissions for all {_n} closed "
                               f"trade(s). NET P&L above is exact.")
                elif _real_fee_rows:
                    st.caption(f"ℹ️ {_real_fee_rows}/{_n} trades use REAL recorded Binance "
                               f"commissions; the rest fall back to the estimate above.")
                else:
                    st.caption("⚠️ Fees are ESTIMATED — no real commissions recorded yet "
                               "(older trades). New trades capture real Binance fees. "
                               "The Gross/Total P&L numbers elsewhere do NOT subtract fees.")

            if closed_trades:
                st.markdown('<div class="sec-lbl" style="margin-top:16px;">P&L per Closed Trade</div>', unsafe_allow_html=True)
                def _row_fee_net(t):
                    """Real fee/net when both legs captured; else honest estimate
                    (prefixed ≈) so a partial capture never looks exact."""
                    g = _finite(t.get("gross_pnl", t.get("profit_loss")))
                    if t.get("fees_complete") and t.get("total_fees") is not None:
                        fee = round(_finite(t.get("total_fees")), 4)
                        net = round(_finite(t.get("net_pnl")), 4)
                        pct = round(t.get("net_pnl_pct", t.get("profit_loss_pct")) or 0, 2)
                        return fee, net, pct
                    qty  = _finite(t.get("quantity"))
                    inv  = _finite(t.get("invested"))
                    xp   = _finite(t.get("exit_price"))
                    exit_notional = (xp * qty) if (xp > 0 and qty > 0) else inv
                    fee_est = (inv + exit_notional) * _fr
                    net_est = g - fee_est
                    pct_est = (net_est / inv * 100) if inv else 0.0
                    return (f"≈{fee_est:.4f}", f"≈{net_est:.4f}", round(pct_est, 2))
                pnl_data = []
                for t in closed_trades:
                    _fee_c, _net_c, _pct_c = _row_fee_net(t)
                    pnl_data.append(
                        {"Trade": f"{t.get('id','?')} {t.get('side','')} {t.get('coin','')}",
                         "Gross $": round(_finite(t.get("gross_pnl", t.get("profit_loss"))), 4),
                         "Fees $":  _fee_c,
                         "Net $":   _net_c,
                         "P&L %":   _pct_c,
                         "Strategy": t.get("strategy","—"),
                         "Type": "🤖" if t.get("type")=="bot" else "👤",
                         "Closed": _fmt_london(t.get("close_time"), "%Y-%m-%d %H:%M")})
                st.dataframe(pd.DataFrame(pnl_data), width="stretch",
                             hide_index=True, height=220)

            # ── ⚖️ Carry break-even: funding-only vs gross (research) ──────────
            # Surfaces the per-symbol break-even four-leg fee for the delta-
            # neutral carry so an operator scanning the UI can see — at a glance,
            # without reading the full reason string — whether FUNDING income
            # alone covers costs (binding = lower of gross / funding-only). When
            # funding-only is the tighter limit, a healthy-looking gross figure is
            # propped up by a one-off basis move that funding does NOT cover.
            _carry_cells = _load_carry_breakeven_cells()
            try:
                from research import MIN_SYMBOLS
            except Exception:
                MIN_SYMBOLS = 2
            if _carry_cells:
                st.markdown('<div class="sec-lbl" style="margin-top:18px;">'
                            '⚖️ Carry break-even — funding-only vs gross (research)'
                            '</div>', unsafe_allow_html=True)
                st.caption("Per-symbol break-even four-leg fee for the delta-neutral "
                           "carry. A symbol clears only if the fee is below BOTH its "
                           "gross AND its funding-only carry, so the BINDING limit is "
                           "the lower of the two. ⚠️ flags symbols where funding-only "
                           "binds — funding alone does not cover costs. (Research only; "
                           "carry is never wired to live trading.)")
                _EPS = 1e-9
                for _cc in _carry_cells:
                    _fs = _cc.get("fee_sweep", {})
                    _be_verdict = _fs.get("verdict_breakeven_four_leg_pct")
                    _maker = _fs.get("achievable_maker_four_leg_pct")
                    _taker = _fs.get("achievable_taker_four_leg_pct")
                    _vd = _cc.get("verdict", "—")
                    _vd_col = "#3fb950" if _vd == "ACCEPT" else "#f85149"
                    _strat = _cc.get("strategy") or _cc.get("strategy_key", "Carry")
                    # Does funding-only bind the breadth verdict (vs the gross-only
                    # break-even)? Mirror research._breakeven_reason: the verdict BE
                    # uses binding=min(gross,funding); compute the gross-only BE and
                    # compare.
                    _rows = []
                    for _sym, _sc in _cc.get("subcells", {}).items():
                        _g = _sc.get("breakeven_four_leg_pct")
                        if _g is None:
                            continue
                        _f = _sc.get("breakeven_funding_only_pct")
                        if _f is None:
                            _f = _g
                        _rows.append((_sym, _g, _f, min(_g, _f)))
                    _rows.sort(key=lambda r: r[3], reverse=True)
                    _grosses = sorted((g for _, g, _f, _b in _rows), reverse=True)
                    _gross_verdict_be = (_grosses[MIN_SYMBOLS - 1]
                                         if len(_grosses) >= MIN_SYMBOLS else None)
                    _funding_binds_verdict = (
                        _be_verdict is not None and _gross_verdict_be is not None
                        and _gross_verdict_be > _be_verdict + _EPS)

                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;'
                        f'margin:12px 0 4px;"><span style="font-weight:700;'
                        f'font-size:13px;color:#c9d1d9;">{_strat}</span>'
                        f'<span style="background:{_vd_col}22;color:{_vd_col};'
                        f'border:1px solid {_vd_col}66;border-radius:4px;padding:1px 7px;'
                        f'font-size:11px;font-weight:700;">{_vd}</span></div>',
                        unsafe_allow_html=True)

                    # Binding verdict break-even line + achievable tiers.
                    if _be_verdict is not None:
                        _ord = {1: "st", 2: "nd", 3: "rd"}.get(MIN_SYMBOLS, "th")
                        _vb = (f"<b>Verdict break-even {_be_verdict:.3f}%</b> "
                               f"<span style='color:#8b949e;'>(binding = lower of "
                               f"gross / funding-only · {MIN_SYMBOLS}{_ord}-highest "
                               f"binding break-even)</span>")
                        if _maker is not None and _taker is not None:
                            _clears = ("both clear it" if _taker < _be_verdict else
                                       "maker clears it, taker does NOT"
                                       if _maker < _be_verdict else "neither clears it")
                            _vb += (f" · achievable maker ≈{_maker:.2f}% / taker "
                                    f"≈{_taker:.2f}% → {_clears}")
                        if _funding_binds_verdict:
                            _vb += (f"<br><span style='color:#e3b341;'>⚠️ funding-only "
                                    f"is the binding limit — the gross break-even is "
                                    f"{_gross_verdict_be:.3f}% but funding income alone "
                                    f"only breaks even at {_be_verdict:.3f}%, so the "
                                    f"verdict flips ACCEPT→REJECT at {_be_verdict:.3f}% "
                                    f"(a one-off basis move props up the gross figure)."
                                    f"</span>")
                        st.markdown(
                            f'<div style="background:#0d1117;border:1px solid #1e2736;'
                            f'border-radius:6px;padding:9px 12px;margin-bottom:6px;'
                            f'color:#c9d1d9;font-size:12px;line-height:1.5;">'
                            f'{_vb}</div>', unsafe_allow_html=True)

                    # Per-symbol gross / funding-only / binding table.
                    _be_rows = []
                    for _sym, _g, _f, _b in _rows:
                        _funding_binds = _f < _g - _EPS
                        _be_rows.append({
                            "Symbol":          _sym.replace("USDT", ""),
                            "Gross BE %":      f"{_g:+.3f}",
                            "Funding-only BE %": f"{_f:+.3f}",
                            "Binding BE %":    f"{_b:+.3f}",
                            "Binds on":        ("⚠️ funding-only" if _funding_binds
                                                else "gross"),
                        })
                    if _be_rows:
                        st.dataframe(pd.DataFrame(_be_rows), width="stretch",
                                     hide_index=True,
                                     height=min(60 + 35 * len(_be_rows), 240))

        st.markdown("<div style='height:48px'></div>", unsafe_allow_html=True)

# ── Auto-persist settings on every script run ────────────────────────────────
# Compares current setting values to last saved snapshot; writes settings.json
# only when something changed. Shows "Settings saved" toast on persist.
def _collect_settings_snapshot() -> dict:
    _r  = st.session_state.risk
    _gr = st.session_state.get("global_risk") or GlobalRiskSettings()
    _pso_dump: dict = {}
    for _s, _rs in (st.session_state.get("per_symbol_risk") or {}).items():
        _pso_dump[_s] = {
            "invest_per_trade":   getattr(_rs, "invest_per_trade", 50.0),
            "max_trade_usdt":     getattr(_rs, "max_trade_usdt", 100.0),
            "stop_loss_pct":      getattr(_rs, "stop_loss_pct", 2.0),
            "take_profit_pct":    getattr(_rs, "take_profit_pct", 4.0),
            "max_open_trades":    getattr(_rs, "max_open_trades", 3),
        }
    return {
        "symbol":          st.session_state.symbol,
        "active_symbols":  st.session_state.active_symbols,
        "bot_was_running": bool(st.session_state.get("bot_was_running", False)),
        "strategy":        st.session_state.strategy,
        "interval":        st.session_state.interval,
        "check_every":     st.session_state.check_every,
        "threshold":       st.session_state.threshold,
        "initial_balance": st.session_state.initial_balance,
        "manual_amount":   st.session_state.manual_amount,
        "refresh_secs":    st.session_state.refresh_secs,
        "tg_enabled":      st.session_state.tg_enabled,
        "tg_token":        st.session_state.tg_token,
        "tg_chat_id":      st.session_state.tg_chat_id,
        # Chart indicator toggles — persisted so they survive refresh/restart.
        "show_ema":        bool(st.session_state.get("show_ema", True)),
        "show_volume":     bool(st.session_state.get("show_volume", True)),
        "show_rsi":        bool(st.session_state.get("show_rsi", True)),
        "show_macd":       bool(st.session_state.get("show_macd", True)),
        "show_stoch":      bool(st.session_state.get("show_stoch", True)),
        "show_old_trades": bool(st.session_state.get("show_old_trades", True)),
        "show_sl_tp":      bool(st.session_state.get("show_sl_tp", True)),
        # Chart view preferences — persisted so they survive refresh/restart.
        "chart_autofollow":        bool(st.session_state.get("chart_autofollow", True)),
        "chart_marker_offset_pct": float(st.session_state.get("chart_marker_offset_pct", 0.03)),
        "global_risk": {
            "max_total_exposure_usdt":     _gr.max_total_exposure_usdt,
            "max_exposure_per_symbol_pct": _gr.max_exposure_per_symbol_pct,
            "max_open_trades_total":       _gr.max_open_trades_total,
            "max_daily_loss_pct":          _gr.max_daily_loss_pct,
            "manage_manual_trades":        bool(getattr(_gr, "manage_manual_trades", False)),
        },
        "per_symbol_risk": _pso_dump,
        "risk": {
            "invest_per_trade":         getattr(_r, "invest_per_trade", 50.0),
            "max_trade_usdt":           getattr(_r, "max_trade_usdt", 100.0),
            "stop_loss_pct":            getattr(_r, "stop_loss_pct", 2.0),
            "take_profit_pct":          getattr(_r, "take_profit_pct", 4.0),
            "max_open_trades":          getattr(_r, "max_open_trades", 2),
            "cooldown_seconds":         getattr(_r, "cooldown_seconds", 180),
            "max_daily_loss_pct":       getattr(_r, "max_daily_loss_pct", 5.0),
            "max_trades_per_session":   getattr(_r, "max_trades_per_session", 0),
            # emergency_stop is intentionally NOT persisted — it is a session-only
            # kill switch that always starts OFF after a refresh/restart.
        },
    }

import json as _json
_snap = _collect_settings_snapshot()
_snap_hash = hash(_json.dumps(_snap, sort_keys=True, default=str))
if st.session_state.get("_last_settings_hash") != _snap_hash:
    if save_settings(_snap):
        st.session_state._last_settings_hash = _snap_hash
        # Only toast after the initial load (avoids "saved" flash on first render)
        if st.session_state.get("_settings_initial_saved"):
            st.toast("✅ Settings saved", icon="💾")
        st.session_state._settings_initial_saved = True
