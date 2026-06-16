"""Dashboard support — trade history, stats, scanner meta, health, decisions.

All data comes from real files/APIs. Never fabricates trades, balances, or PnL.
"""
from __future__ import annotations

import glob
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_DIR = os.path.join(_DIR, "data", "trades")
SCANNER_PATH = os.path.join(_DIR, "data", "multi_exchange_opportunities.json")

# Global live rules (display baseline — operator may override via live_settings)
GLOBAL_BUY_PCT = -0.05
GLOBAL_TP_PCT = 1.00
GLOBAL_SL_PCT = -0.30
GLOBAL_COOLDOWN_SEC = 120


def trades_dir_status() -> Dict[str, Any]:
    exists = os.path.isdir(TRADES_DIR)
    files = sorted(glob.glob(os.path.join(TRADES_DIR, "*.json"))) if exists else []
    return {
        "exists": exists,
        "path": TRADES_DIR,
        "file_count": len(files),
        "files": [os.path.basename(f) for f in files],
    }


def ensure_utc(dt) -> Optional[datetime]:
    """Coerce ISO string or datetime to timezone-aware UTC."""
    if dt is None or dt == "":
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(dt[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def trade_sort_ts(t: Dict) -> datetime:
    """Best timestamp for ordering trades (closed → close_time, else open_time)."""
    raw = t.get("close_time") if t.get("status") == "closed" else t.get("open_time")
    return ensure_utc(raw) or datetime.min.replace(tzinfo=timezone.utc)


def sort_trades_latest_first(trades: List[Dict]) -> List[Dict]:
    return sorted(trades, key=trade_sort_ts, reverse=True)


def format_age(iso_or_dt, now: Optional[datetime] = None) -> str:
    """Human-readable age, e.g. '42s ago' or '3m ago'."""
    dt = ensure_utc(iso_or_dt)
    if dt is None:
        return "—"
    now = ensure_utc(now) or datetime.now(timezone.utc)
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def group_trades_by_exchange(trades: List[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        ex = (t.get("exchange") or "binance").lower()
        if "mexc" in ex:
            ex = "mexc"
        else:
            ex = "binance"
        out[ex].append(t)
    return dict(out)


def _finite(x, default=0.0) -> float:
    try:
        v = float(x)
        return v if pd.notna(v) else default
    except (TypeError, ValueError):
        return default


def trade_pnl_usd(t: Dict, prefer_net: bool = True) -> float:
    if prefer_net and t.get("net_pnl") is not None:
        return _finite(t.get("net_pnl"))
    return _finite(t.get("profit_loss"))


def summarize_closed(closed: List[Dict]) -> Dict[str, Any]:
    if not closed:
        return {
            "count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_gross": 0.0, "total_net": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        }
    wins, losses = [], []
    gross = net = 0.0
    for t in closed:
        g = _finite(t.get("gross_pnl", t.get("profit_loss")))
        n = trade_pnl_usd(t)
        gross += g
        net += n
        (wins if g >= 0 else losses).append(g)
    n = len(closed)
    return {
        "count": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "total_gross": gross,
        "total_net": net,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
    }


def performance_by_symbol(closed: List[Dict], top_n: int = 15) -> pd.DataFrame:
    by_sym: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in closed:
        sym = t.get("coin") or "?"
        p = trade_pnl_usd(t)
        by_sym[sym]["trades"] += 1
        by_sym[sym]["pnl"] += p
        if p >= 0:
            by_sym[sym]["wins"] += 1
    rows = []
    for sym, v in by_sym.items():
        wr = (v["wins"] / v["trades"] * 100) if v["trades"] else 0
        rows.append({
            "Symbol": sym,
            "Trades": v["trades"],
            "Win %": round(wr, 1),
            "Total PnL $": round(v["pnl"], 4),
        })
    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df
    return df.sort_values("Total PnL $", ascending=False).head(top_n)


def performance_by_hour(closed: List[Dict]) -> pd.DataFrame:
    by_h: Dict[int, Dict] = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    for t in closed:
        ct = t.get("close_time") or t.get("open_time") or ""
        try:
            h = datetime.fromisoformat(str(ct).replace("Z", "+00:00")).hour
        except Exception:
            continue
        by_h[h]["trades"] += 1
        by_h[h]["pnl"] += trade_pnl_usd(t)
    rows = [{"Hour (UTC)": h, "Trades": v["trades"], "PnL $": round(v["pnl"], 4)}
            for h, v in sorted(by_h.items())]
    return pd.DataFrame(rows)


def load_scanner_payload() -> Dict[str, Any]:
    if not os.path.isfile(SCANNER_PATH):
        return {}
    try:
        import json
        with open(SCANNER_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def scanner_file_missing_message() -> str:
    return (
        f"Scanner output not found. Expected file:\n`{SCANNER_PATH}`\n\n"
        "Run **Refresh scan now** in the sidebar, or start the scanner daemon "
        "from the bot. No scanner data will be fabricated."
    )


def trades_dir_missing_message() -> str:
    return (
        f"Trade history directory missing or empty.\n"
        f"Expected: `{TRADES_DIR}/*.json`\n\n"
        "Restore from your production droplet backup, or trades will appear "
        "as the bot opens and closes positions. No history is fabricated."
    )


def build_history_rows(trades: List[Dict], fmt_pnl, fmt_pct) -> List[Dict]:
    rows = []
    for t in sort_trades_latest_first(trades):
        pnl = t.get("profit_loss")
        pct = t.get("profit_loss_pct")
        net = t.get("net_pnl")
        rows.append({
            "ID": t.get("id", "—"),
            "Exchange": (t.get("exchange") or "binance"),
            "Coin": t.get("coin", "—"),
            "Type": "Bot" if t.get("type") == "bot" else "Manual",
            "Side": t.get("side", "—"),
            "Strategy": t.get("strategy", "—"),
            "Entry": f"${_finite(t.get('entry_price')):.4f}",
            "Exit": (f"${_finite(t.get('exit_price')):.4f}"
                     if t.get("exit_price") else "open"),
            "Invested": f"${_finite(t.get('invested')):.2f}",
            "Gross PnL": fmt_pnl(pnl),
            "Net PnL": fmt_pnl(net) if net is not None else "—",
            "Pnl %": fmt_pct(pct),
            "Status": t.get("status", "—"),
            "Opened": (t.get("open_time") or "")[:19].replace("T", " "),
            "Closed": ((t.get("close_time") or "")[:19].replace("T", " ")
                       if t.get("close_time") else "—"),
            "Open Reason": (t.get("reason") or "")[:120],
            "Close Reason": (t.get("close_reason") or "")[:120],
        })
    return rows


def closed_trade_detail_rows(closed: List[Dict], fee_rate_pct: float = 0.10) -> List[Dict]:
    fr = fee_rate_pct / 100.0
    out = []
    for t in closed:
        g = _finite(t.get("gross_pnl", t.get("profit_loss")))
        if t.get("fees_complete") and t.get("total_fees") is not None:
            fee = _finite(t.get("total_fees"))
            net = _finite(t.get("net_pnl"))
            pct = _finite(t.get("net_pnl_pct", t.get("profit_loss_pct")))
        else:
            qty = _finite(t.get("quantity"))
            inv = _finite(t.get("invested"))
            xp = _finite(t.get("exit_price"))
            exit_n = (xp * qty) if (xp > 0 and qty > 0) else inv
            fee = (inv + exit_n) * fr
            net = g - fee
            pct = (net / inv * 100) if inv else 0.0
        out.append({
            "Trade": f"{t.get('id', '?')} {t.get('side', '')} {t.get('coin', '')}",
            "Exchange": t.get("exchange", "binance"),
            "Gross $": round(g, 4),
            "Fees $": round(fee, 4) if isinstance(fee, float) else fee,
            "Net $": round(net, 4) if isinstance(net, float) else net,
            "Pnl %": round(pct, 2),
            "Open Reason": (t.get("reason") or "—")[:80],
            "Close Reason": (t.get("close_reason") or "—")[:80],
            "Closed": (t.get("close_time") or "")[:16],
        })
    return out


def macd_rsi_state(df) -> Tuple[str, str, float]:
    """Return (macd_label, rsi_label, confidence 0-100) from indicator df."""
    if df is None or len(df) < 30:
        return "—", "—", 0.0
    try:
        row = df.iloc[-1]
        rsi = float(row.get("rsi", 50) or 50)
        macd = float(row.get("macd", 0) or 0)
        sig = float(row.get("macd_signal", 0) or 0)
        hist = float(row.get("macd_hist", macd - sig) or 0)
        if macd > sig and hist > 0:
            macd_l = "Bullish"
        elif macd < sig and hist < 0:
            macd_l = "Bearish"
        else:
            macd_l = "Neutral"
        if rsi < 35:
            rsi_l = "Oversold"
        elif rsi > 65:
            rsi_l = "Overbought"
        else:
            rsi_l = "Mid"
        conf = min(100, max(0, 50 + (10 if macd_l == "Bullish" else -10 if macd_l == "Bearish" else 0)
                         + (8 if rsi_l == "Oversold" else -8 if rsi_l == "Overbought" else 0)))
        return macd_l, rsi_l, conf
    except Exception:
        return "—", "—", 0.0


def classify_decision(
    rec,
    buy_thr: float = GLOBAL_BUY_PCT,
    macd_l: str = "—",
    rsi_l: str = "—",
    ai_conf: float = 0.0,
) -> Tuple[str, str, str]:
    """Map ActivityRecord + indicators → (label, color, detail reason)."""
    d = (getattr(rec, "decision", "") or "HOLD").upper().replace("-", "_")
    chg = getattr(rec, "change_pct", None)
    vr = getattr(rec, "volume_ratio", None)
    v_on = getattr(rec, "volume_filter_on", True)
    v_min = float(getattr(rec, "min_volume_multiple", 1.0) or 1.0)
    t_ok = getattr(rec, "trend_ok", None)
    t_on = getattr(rec, "trend_filter_on", True)
    base_reason = getattr(rec, "reason", "") or ""

    if d == "BUY":
        label = "BUY READY"
        color = "#26a69a"
    elif d in ("SELL", "TAKE_PROFIT"):
        label = "SELL TARGET"
        color = "#3b82f6"
    elif d == "STOP_LOSS":
        label = "STOP LOSS"
        color = "#ef5350"
    elif d == "SKIP":
        label = "BUY BLOCKED"
        color = "#d29922"
    else:
        label = "HOLD"
        color = "#8b949e"

    # AI/MACD may block weak BUY setups (assistant layer — never bypasses risk)
    if label == "BUY READY":
        blocks = []
        if t_on and t_ok is False:
            blocks.append("trend")
        if v_on and v_min > 0 and vr is not None and vr < v_min:
            blocks.append("volume")
        if macd_l == "Bearish":
            blocks.append("MACD bearish")
        if rsi_l == "Overbought":
            blocks.append("RSI overbought")
        if blocks:
            label = "BUY BLOCKED"
            color = "#d29922"
            base_reason = f"Assistant block: {', '.join(blocks)} | {base_reason}"

    detail = base_reason
    if chg is not None:
        detail = f"ML {chg:+.2f}% (need ≤{buy_thr:.2f}%) · MACD {macd_l} · RSI {rsi_l} · {detail}"
    return label, color, detail[:200]


def amount_left_to_trade(
    free_usdt: float,
    max_exposure: float,
    current_exposure: float,
) -> float:
    if max_exposure and max_exposure > 0:
        return max(0.0, min(free_usdt, max_exposure - current_exposure))
    return max(0.0, free_usdt * 0.75)


def _venue_match(trade: Dict, venue: str) -> bool:
    ex = (trade.get("exchange") or "binance").lower()
    return ex == venue.lower()


def venue_realized_pnl(closed: List[Dict], venue: str) -> float:
    return sum(
        float(t.get("profit_loss") or 0)
        for t in closed
        if _venue_match(t, venue)
    )


def venue_daily_realized(closed: List[Dict], venue: str, today_str: str) -> float:
    return sum(
        float(t.get("profit_loss") or 0)
        for t in closed
        if _venue_match(t, venue) and (t.get("close_time") or "").startswith(today_str)
    )


def venue_unrealized_pnl(open_trades: List[Dict], venue: str) -> float:
    return sum(
        float(t.get("_unrealized") or 0)
        for t in open_trades
        if _venue_match(t, venue)
    )


def legacy_holding_status(
    *,
    has_open_position: bool,
    value_usd: float,
    has_price: bool,
) -> str:
    if not has_price:
        return "WAIT CONFIRMATION"
    if has_open_position:
        return "LEGACY HOLD"
    if value_usd >= 10.0:
        return "LEGACY EXIT CANDIDATE"
    return "LEGACY HOLD"


def volume_label(volume_ratio) -> str:
    if volume_ratio is None:
        return "—"
    try:
        vr = float(volume_ratio)
    except (TypeError, ValueError):
        return "—"
    if vr >= 1.0:
        return "HIGH"
    return f"{vr:.2f}×"


def format_quote_volume(volume) -> str:
    """Scanner 24h quote (USDT) volume for operator tables."""
    try:
        v = float(volume or 0)
    except (TypeError, ValueError):
        return "—"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def scanner_trend_for_symbol(symbol: str, acts: dict, opp_change=None) -> str:
    """Trend for scanner row — engine data first, else 24h change sign."""
    sym = str(symbol or "").upper()
    rec = (acts or {}).get(sym)
    if rec is not None and getattr(rec, "trend_ok", None) is not None:
        return trend_label(rec.trend_ok)
    if opp_change is not None:
        try:
            return "UP" if float(opp_change) >= 0 else "DOWN"
        except (TypeError, ValueError):
            pass
    return "—"


def sort_scanner_opportunities(opps: List[dict]) -> List[dict]:
    """Score desc, then quote volume desc."""
    def _key(o):
        sc = float(o.get("score") or 0)
        vol = float(o.get("volume") or 0)
        return (sc, vol)
    return sorted(opps, key=_key, reverse=True)


def build_scanner_table_rows(opps: List[dict], acts: dict) -> List[Dict[str, str]]:
    """Live scanner operator table: Coin, Volatility, Volume, Trend, Score."""
    rows: List[Dict[str, str]] = []
    for o in sort_scanner_opportunities(opps):
        sym = o.get("symbol") or "—"
        rows.append({
            "Coin": sym.replace("USDT", ""),
            "Volatility": f"{float(o.get('volatility') or 0):.1f}%",
            "Volume": format_quote_volume(o.get("volume")),
            "Trend": scanner_trend_for_symbol(sym, acts, o.get("change")),
            "Score": str(int(o.get("score") or 0)),
        })
    return rows


def build_active_trades_table_rows(
    trades: List[Dict],
    price_fn,
    stop_loss_fn,
    take_profit_fn,
    fmt_pnl,
    *,
    include_reconciliation: bool = False,
) -> List[Dict[str, str]]:
    """Operator table: Coin, Entry, Current, PnL, Target, Stop (+ optional Reconcile)."""
    rows: List[Dict[str, str]] = []
    for ot in trades:
        coin = ot.get("coin", "—")
        ep = float(ot.get("entry_price") or 0)
        side = ot.get("side", "BUY")
        cp = ot.get("_cur_price")
        if cp is None and price_fn:
            cp = price_fn(coin)
        cp_f = float(cp) if cp else None
        sl = ot.get("stop_loss") or (stop_loss_fn(ep, side) if ep else None)
        tp = ot.get("take_profit") or (take_profit_fn(ep, side) if ep else None)
        u = ot.get("_unrealized")
        if u is None and cp_f and ep:
            inv = float(ot.get("invested") or 0)
            u = ((cp_f - ep) / ep * inv if side == "BUY"
                 else (ep - cp_f) / ep * inv)
        row: Dict[str, str] = {
            "Coin": coin,
            "Entry": f"${ep:.4f}" if ep else "—",
            "Current": f"${cp_f:.4f}" if cp_f else "—",
            "PnL": fmt_pnl(u) if u is not None else "—",
            "Target": f"${float(tp):.4f}" if tp else "—",
            "Stop": f"${float(sl):.4f}" if sl else "—",
        }
        if include_reconciliation:
            rs = ot.get("reconciliation_status")
            row["Reconcile"] = str(rs) if rs else "—"
        rows.append(row)
    return rows


def _parse_rotation_list(token: str) -> List[str]:
    token = (token or "").strip()
    if not token or token in ("-", "[]", "None"):
        return []
    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
        return [p for p in parts if p and p != "-"]
    return [token.strip("'\"")]


def parse_rotation_events(
    activities: List[Dict],
    *,
    limit: int = 20,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Parse [ROTATE] activity into Added / Removed operator tables."""
    import re
    added_rows: List[Dict[str, str]] = []
    removed_rows: List[Dict[str, str]] = []
    rot = [a for a in activities if "[ROTATE]" in (a.get("message") or "")]
    for a in reversed(rot[-limit:]):
        msg = a.get("message") or ""
        ts = (a.get("time") or "")[:19].replace("T", " ")
        m_add = re.search(r"added=([^ ]+)", msg)
        m_drop = re.search(r"dropped=([^ ]+)", msg)
        for sym in _parse_rotation_list(m_add.group(1) if m_add else ""):
            added_rows.append({
                "Time": ts or "—",
                "Coin": sym.replace("USDT", ""),
            })
        for sym in _parse_rotation_list(m_drop.group(1) if m_drop else ""):
            removed_rows.append({
                "Time": ts or "—",
                "Coin": sym.replace("USDT", ""),
            })
    return added_rows, removed_rows


def trend_label(trend_ok) -> str:
    if trend_ok is True:
        return "UP"
    if trend_ok is False:
        return "DOWN"
    return "—"
