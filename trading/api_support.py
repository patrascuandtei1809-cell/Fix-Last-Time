"""Read-only data access for the AlphaTrade FastAPI dashboard API.

Reads existing JSON files only. Never places orders, writes files, or imports
the trading orchestrator. Missing files yield clear warnings — no fabrication.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dashboard_support as ds

_DIR = Path(__file__).resolve().parent
DATA_DIR = _DIR / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"
TRADES_DIR = DATA_DIR / "trades"
ACTIVITY_PATH = DATA_DIR / "activity.json"
SCANNER_PATH = DATA_DIR / "multi_exchange_opportunities.json"
HEARTBEATS_DIR = DATA_DIR / "heartbeats"

# Keys stripped from settings API responses (never expose secrets via API).
_REDACT_SETTINGS_KEYS = frozenset({
    "tg_token", "tg_chat_id", "api_key", "api_secret",
    "openai_api_key", "OPENAI_API_KEY",
})


def _read_json(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not path.is_file():
        return None, f"File not found: {path}"
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f), None
    except Exception as exc:
        return None, f"Failed to read {path}: {exc}"


def _warning(code: str, message: str, path: Optional[str] = None) -> Dict[str, Any]:
    w: Dict[str, Any] = {"code": code, "message": message}
    if path:
        w["path"] = path
    return w


def load_settings() -> Dict[str, Any]:
    data, err = _read_json(SETTINGS_PATH)
    warnings: List[Dict[str, Any]] = []
    if err:
        warnings.append(_warning("settings_missing", err, str(SETTINGS_PATH)))
        return {"ok": False, "warnings": warnings, "data": {}}
    out = dict(data or {})
    for k in list(out.keys()):
        if k in _REDACT_SETTINGS_KEYS:
            out[k] = "[redacted]"
    return {"ok": True, "warnings": warnings, "data": out}


def _load_trade_files() -> Tuple[List[Dict], List[Dict[str, Any]]]:
    warnings: List[Dict[str, Any]] = []
    if not TRADES_DIR.is_dir():
        warnings.append(_warning(
            "trades_dir_missing",
            ds.trades_dir_missing_message(),
            str(TRADES_DIR),
        ))
        return [], warnings
    files = sorted(TRADES_DIR.glob("*.json"))
    if not files:
        warnings.append(_warning(
            "trades_empty",
            "Trade history directory exists but contains no *.json files yet.",
            str(TRADES_DIR),
        ))
        return [], warnings
    trades: List[Dict] = []
    for fp in files:
        chunk, err = _read_json(fp)
        if err:
            warnings.append(_warning("trade_file_read_error", err, str(fp)))
            continue
        if isinstance(chunk, list):
            trades.extend(chunk)
        else:
            warnings.append(_warning(
                "trade_file_invalid",
                f"Expected JSON array in {fp.name}",
                str(fp),
            ))
    return trades, warnings


def _exchange_bucket(trade: Dict) -> str:
    ex = (trade.get("exchange") or "binance").lower()
    return "mexc" if "mexc" in ex else "binance"


def load_trades_history(
    exchange: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    trades, warnings = _load_trade_files()
    if exchange:
        ex = exchange.lower()
        trades = [t for t in trades if _exchange_bucket(t) == ex]
    if status:
        trades = [t for t in trades if t.get("status") == status]
    return {
        "ok": True,
        "warnings": warnings,
        "count": len(trades),
        "data": trades,
    }


def load_trades_open() -> Dict[str, Any]:
    result = load_trades_history(status="open")
    by_ex = ds.group_trades_by_exchange(result["data"])
    result["by_exchange"] = {
        "binance": by_ex.get("binance", []),
        "mexc": by_ex.get("mexc", []),
    }
    return result


def load_trades_closed() -> Dict[str, Any]:
    result = load_trades_history(status="closed")
    by_ex = ds.group_trades_by_exchange(result["data"])
    result["by_exchange"] = {
        "binance": by_ex.get("binance", []),
        "mexc": by_ex.get("mexc", []),
    }
    return result


def load_activity(limit: int = 300) -> Dict[str, Any]:
    data, err = _read_json(ACTIVITY_PATH)
    warnings: List[Dict[str, Any]] = []
    if err:
        warnings.append(_warning("activity_missing", err, str(ACTIVITY_PATH)))
        return {"ok": False, "warnings": warnings, "count": 0, "data": []}
    entries = list(data or [])
    if not isinstance(entries, list):
        warnings.append(_warning(
            "activity_invalid",
            "activity.json must be a JSON array",
            str(ACTIVITY_PATH),
        ))
        return {"ok": False, "warnings": warnings, "count": 0, "data": []}
    tail = entries[-limit:] if limit > 0 else entries
    return {"ok": True, "warnings": warnings, "count": len(tail), "data": tail}


def load_scanner() -> Dict[str, Any]:
    payload = ds.load_scanner_payload()
    warnings: List[Dict[str, Any]] = []
    if not payload:
        warnings.append(_warning(
            "scanner_missing",
            ds.scanner_file_missing_message(),
            str(SCANNER_PATH),
        ))
        return {"ok": False, "warnings": warnings, "data": {}}
    return {"ok": True, "warnings": warnings, "data": payload}


def load_heartbeats() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {}
    if not HEARTBEATS_DIR.is_dir():
        warnings.append(_warning(
            "heartbeats_dir_missing",
            f"Heartbeats directory not found: {HEARTBEATS_DIR}",
            str(HEARTBEATS_DIR),
        ))
        return {"ok": False, "warnings": warnings, "data": out}
    files = sorted(HEARTBEATS_DIR.glob("*.json"))
    if not files:
        warnings.append(_warning(
            "heartbeats_empty",
            "No heartbeat JSON files yet (bot/scanner/dashboard not writing).",
            str(HEARTBEATS_DIR),
        ))
    for fp in files:
        name = fp.stem
        data, err = _read_json(fp)
        if err:
            warnings.append(_warning("heartbeat_read_error", err, str(fp)))
            continue
        out[name] = data
    return {"ok": bool(out) or not warnings, "warnings": warnings, "data": out}


def load_performance() -> Dict[str, Any]:
    trades, warnings = _load_trade_files()
    closed = [t for t in trades if t.get("status") == "closed"]
    summary = ds.summarize_closed(closed)
    by_ex = ds.group_trades_by_exchange(closed)
    ex_stats = {}
    for ex, items in by_ex.items():
        ex_stats[ex] = ds.summarize_closed(items)
    sym_df = ds.performance_by_symbol(closed, top_n=20)
    hour_df = ds.performance_by_hour(closed)
    return {
        "ok": True,
        "warnings": warnings,
        "data": {
            "summary": summary,
            "by_exchange": ex_stats,
            "by_symbol": sym_df.to_dict(orient="records") if len(sym_df) else [],
            "by_hour_utc": hour_df.to_dict(orient="records") if len(hour_df) else [],
            "closed_count": len(closed),
            "total_trades": len(trades),
        },
    }


def _heartbeat_status(name: str, max_age_sec: float) -> Dict[str, Any]:
    """Read one process heartbeat with stale flag via heartbeats.read()."""
    try:
        from heartbeats import read as hb_read
        data = hb_read(name, max_age_sec=max_age_sec)
    except Exception:
        data = None
    if not data:
        return {"name": name, "present": False, "stale": True, "data": None}
    return {
        "name": name,
        "present": True,
        "stale": bool(data.get("stale")),
        "data": data,
    }


def build_health() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    files = {
        "settings": SETTINGS_PATH.is_file(),
        "activity": ACTIVITY_PATH.is_file(),
        "scanner": SCANNER_PATH.is_file(),
        "trades_dir": TRADES_DIR.is_dir(),
    }
    trade_files = list(TRADES_DIR.glob("*.json")) if TRADES_DIR.is_dir() else []
    files["trade_file_count"] = len(trade_files)
    heartbeats_out = {
        "bot": _heartbeat_status("bot", 120.0),
        "scanner": _heartbeat_status("scanner", 300.0),
        "dashboard": _heartbeat_status("dashboard", 300.0),
    }
    for hb in heartbeats_out.values():
        if not hb["present"]:
            warnings.append(_warning(
                "heartbeat_missing",
                f"No heartbeat file for {hb['name']}",
            ))
        elif hb["stale"]:
            warnings.append(_warning(
                "heartbeat_stale",
                f"Heartbeat for {hb['name']} is stale",
            ))
    return {
        "ok": True,
        "service": "alphatrade-api",
        "mode": "read-only",
        "at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "heartbeats": heartbeats_out,
        "warnings": warnings,
    }


# ── Phase D1 serializers / endpoints ─────────────────────────────────────────

DECISIONS_PATH = DATA_DIR / "decisions.json"
_CORE_MARKETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _fmt_pnl(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"+${v:.4f}" if v >= 0 else f"-${abs(v):.4f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _redact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    for k in list(out.keys()):
        if k in _REDACT_SETTINGS_KEYS:
            out[k] = "[redacted]"
    return out


def load_wallet_binance() -> Dict[str, Any]:
    from api_wallet import fetch_binance_wallet
    data, warnings = fetch_binance_wallet()
    return {"ok": True, "warnings": warnings, "data": data}


def load_wallet_mexc() -> Dict[str, Any]:
    from api_wallet import fetch_mexc_wallet
    trades, tw = _load_trade_files()
    open_trades = [t for t in trades if t.get("status") == "open"]
    data, warnings = fetch_mexc_wallet(open_trades)
    warnings = tw + warnings
    return {"ok": True, "warnings": warnings, "data": data}


def load_wallet_summary() -> Dict[str, Any]:
    from api_wallet import wallet_summary
    data, warnings = wallet_summary()
    return {"ok": True, "warnings": warnings, "data": data}


def load_trades_history_rows() -> Dict[str, Any]:
    trades, warnings = _load_trade_files()
    rows = ds.build_history_rows(trades, _fmt_pnl, _fmt_pct)
    return {"ok": True, "warnings": warnings, "count": len(rows), "data": rows}


def load_scanner_filtered(exchange: Optional[str] = None) -> Dict[str, Any]:
    payload = ds.load_scanner_payload()
    warnings: List[Dict[str, Any]] = []
    if not payload:
        warnings.append(_warning(
            "scanner_missing",
            ds.scanner_file_missing_message(),
            str(SCANNER_PATH),
        ))
        return {"ok": False, "warnings": warnings, "data": {}}
    opps = list(payload.get("opportunities") or [])
    if exchange:
        ex = exchange.lower()
        opps = [o for o in opps if str(o.get("exchange", "")).lower() == ex]
    out = dict(payload)
    out["opportunities"] = opps
    out["count"] = len(opps)
    return {"ok": True, "warnings": warnings, "data": out}


def load_scanner_status() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    hb = _heartbeat_status("scanner", 300.0)
    payload = ds.load_scanner_payload()
    last_run = payload.get("updated_at") if payload else None
    daemon = False
    try:
        import scanner as sc
        daemon = sc.is_daemon_running()
    except Exception:
        pass
    if not hb["present"]:
        warnings.append(_warning("scanner_heartbeat_missing", "No scanner heartbeat yet."))
    age_sec = None
    if hb.get("data") and hb["data"].get("ts"):
        try:
            import time
            age_sec = round(time.time() - float(hb["data"]["ts"]), 1)
        except Exception:
            pass
    return {
        "ok": True,
        "warnings": warnings,
        "data": {
            "daemon_running": daemon,
            "heartbeat": hb,
            "heartbeat_age_sec": age_sec,
            "last_run": last_run,
            "scanner_file_present": bool(payload),
        },
    }


def load_scanner_config() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    settings_data, err = _read_json(SETTINGS_PATH)
    persisted = dict(settings_data or {}) if not err else {}
    if err:
        warnings.append(_warning("settings_missing", err, str(SETTINGS_PATH)))
    payload = ds.load_scanner_payload()
    scan_cfg = dict(payload.get("config") or {}) if payload else {}
    gr = persisted.get("global_risk") or {}
    return {
        "ok": True,
        "warnings": warnings,
        "data": {
            "exchange_mode": persisted.get("exchange_mode", "mexc"),
            "use_scanner_symbols": bool(persisted.get("use_scanner_symbols", False)),
            "mexc_live_orders": bool(persisted.get("mexc_live_orders", False)),
            "max_open_trades_binance": gr.get("max_open_trades_binance", 3),
            "max_open_trades_mexc": gr.get("max_open_trades_mexc", 15),
            "max_open_trades_total": gr.get("max_open_trades_total", 18),
            "max_total_exposure_usdt": gr.get("max_total_exposure_usdt", 0),
            "scanner": scan_cfg,
        },
    }


def load_global_rules() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    try:
        import live_settings as ls
        s = ls.get_settings()
        data = {
            "buy_threshold_pct": float(s.buy_threshold_pct),
            "take_profit_pct": float(s.take_profit_pct),
            "stop_loss_pct": float(s.stop_loss_pct),
            "reentry_cooldown_sec": int(s.reentry_cooldown_sec),
            "trend_filter_on": bool(s.trend_filter_on),
            "volume_filter_on": bool(s.volume_filter_on),
            "min_volume_multiple": float(s.min_volume_multiple),
        }
    except Exception as exc:
        warnings.append(_warning("live_settings_error", str(exc)))
        data = {
            "buy_threshold_pct": ds.GLOBAL_BUY_PCT,
            "take_profit_pct": ds.GLOBAL_TP_PCT,
            "stop_loss_pct": ds.GLOBAL_SL_PCT,
            "reentry_cooldown_sec": ds.GLOBAL_COOLDOWN_SEC,
            "trend_filter_on": True,
            "volume_filter_on": True,
            "min_volume_multiple": 1.0,
        }
    return {"ok": True, "warnings": warnings, "data": data}


def _connection_bools() -> Dict[str, bool]:
    binance = False
    mexc = False
    try:
        from secrets_store import load_credentials
        creds = load_credentials()
        if creds:
            try:
                from binance_client import BinanceClient
                c = BinanceClient(*creds)
                binance = bool(c.test_connection()[0])
            except ImportError:
                pass
    except Exception:
        pass
    try:
        from exchanges.mexc import load_mexc_credentials
        mexc = load_mexc_credentials() is not None
    except Exception:
        pass
    return {"binance": binance, "mexc": mexc}


def load_status_system() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    hb_bot = _heartbeat_status("bot", 120.0)
    hb_scan = _heartbeat_status("scanner", 300.0)
    hb_dash = _heartbeat_status("dashboard", 300.0)
    conns = _connection_bools()
    bot_running = hb_bot["present"] and not hb_bot["stale"]
    scanner_ok = hb_scan["present"] and not hb_scan["stale"]
    dashboard_ok = hb_dash["present"] and not hb_dash["stale"]
    return {
        "ok": True,
        "warnings": warnings,
        "data": {
            "bot_running": bot_running,
            "scanner_healthy": scanner_ok,
            "dashboard_healthy": dashboard_ok,
            "connections": conns,
        },
    }


def load_status_bot() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    hb = _heartbeat_status("bot", 120.0)
    if not hb["present"]:
        warnings.append(_warning("bot_heartbeat_missing", "Bot heartbeat not found."))
    extra = (hb.get("data") or {}) if hb.get("data") else {}
    trades, _ = _load_trade_files()
    open_count = sum(1 for t in trades if t.get("status") == "open")
    return {
        "ok": True,
        "warnings": warnings,
        "data": {
            "running": hb["present"] and not hb["stale"],
            "stale": hb["stale"],
            "last_tick": extra.get("at"),
            "workers": extra.get("workers"),
            "open_trades": extra.get("open_trades", open_count),
            "heartbeat": hb,
        },
    }


def load_markets_core() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    try:
        from binance_client import public_24h
    except ImportError as exc:
        warnings.append(_warning("binance_client_unavailable", str(exc)))
        return {"ok": False, "warnings": warnings, "data": {}}
    markets: Dict[str, Any] = {}
    for sym in _CORE_MARKETS:
        try:
            d = public_24h(sym)
            markets[sym] = {
                "price": d["price"],
                "change_pct": d["change_pct"],
                "high": d["high"],
                "low": d["low"],
                "volume": d["volume"],
                "quote_volume": d["quote_volume"],
            }
        except Exception as exc:
            warnings.append(_warning("market_fetch_error", f"{sym}: {exc}"))
    return {"ok": bool(markets), "warnings": warnings, "data": markets}


def _venue_from_trade(t: Dict) -> str:
    ex = (t.get("exchange") or "binance").lower()
    return "mexc" if "mexc" in ex else "binance"


def load_decisions(venue: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    hb = _heartbeat_status("bot", 120.0)
    bot_running = hb["present"] and not hb["stale"]
    items: List[Dict[str, Any]] = []

    if DECISIONS_PATH.is_file():
        raw, err = _read_json(DECISIONS_PATH)
        if err:
            warnings.append(_warning("decisions_read_error", err, str(DECISIONS_PATH)))
        elif isinstance(raw, list):
            items.extend(raw)

    try:
        import diagnostics as diag
        for d in diag.get_recent_decisions(limit=limit):
            items.append(dict(d))
    except Exception:
        pass

    if not items:
        act = load_activity(limit=500)
        for a in act.get("data") or []:
            msg = (a.get("message") or "").lower()
            if any(k in msg for k in ("decision", "skip", "buy", "sell", "hold", "block")):
                items.append({
                    "time": a.get("time"),
                    "level": a.get("level"),
                    "message": a.get("message"),
                    "source": "activity.json",
                })

    if venue:
        v = venue.lower()
        items = [
            i for i in items
            if (i.get("venue") or i.get("exchange") or _venue_from_trade(i)).lower() == v
            or (v == "binance" and not i.get("venue") and not i.get("exchange"))
        ]

    if not bot_running and not items:
        warnings.append(_warning(
            "bot_not_running",
            "Bot does not appear to be running; no live decisions available.",
        ))

    tail = items[:limit]
    return {
        "ok": True,
        "warnings": warnings,
        "count": len(tail),
        "bot_running": bot_running,
        "data": tail,
    }


def load_diagnostics_trades() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    try:
        import diagnostics as diag
        freq = diag.trade_frequency_stats()
        if freq.get("last_trade_at"):
            freq = dict(freq)
            lt = freq["last_trade_at"]
            if hasattr(lt, "isoformat"):
                freq["last_trade_at"] = lt.isoformat()
        if freq.get("first_trade_at"):
            ft = freq["first_trade_at"]
            if hasattr(ft, "isoformat"):
                freq["first_trade_at"] = ft.isoformat()
        blocks = diag.get_block_summary(top=10)
    except Exception as exc:
        warnings.append(_warning("diagnostics_error", str(exc)))
        freq, blocks = {}, []
    return {
        "ok": True,
        "warnings": warnings,
        "data": {"frequency": freq, "block_summary": blocks},
    }


def load_diagnostics_report() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    try:
        import diagnostics as diag
        report = diag.build_report()
    except Exception as exc:
        warnings.append(_warning("diagnostics_report_error", str(exc)))
        report = ""
    return {"ok": bool(report), "warnings": warnings, "data": {"report": report}}


def load_settings_snapshot() -> Dict[str, Any]:
    data, err = _read_json(SETTINGS_PATH)
    warnings: List[Dict[str, Any]] = []
    if err:
        warnings.append(_warning("settings_missing", err, str(SETTINGS_PATH)))
        return {"ok": False, "warnings": warnings, "data": {}}
    out = _redact_dict(dict(data or {}))
    return {"ok": True, "warnings": warnings, "data": out}


def load_performance_equity() -> Dict[str, Any]:
    trades, warnings = _load_trade_files()
    closed = sorted(
        [t for t in trades if t.get("status") == "closed" and t.get("close_time")],
        key=lambda t: t["close_time"],
    )
    cum = 0.0
    series: List[Dict[str, Any]] = []
    if closed:
        series.append({"time": closed[0].get("open_time") or closed[0]["close_time"], "equity": 0.0})
    for t in closed:
        cum += float(t.get("profit_loss") or 0)
        series.append({
            "time": t.get("close_time"),
            "equity": round(cum, 4),
            "trade_id": t.get("id"),
            "pnl": t.get("profit_loss"),
        })
    return {
        "ok": True,
        "warnings": warnings,
        "data": {
            "series": series,
            "cumulative_pnl": round(cum, 4),
            "closed_count": len(closed),
        },
    }


def load_performance_details() -> Dict[str, Any]:
    trades, warnings = _load_trade_files()
    closed = [t for t in trades if t.get("status") == "closed"]
    rows = ds.closed_trade_detail_rows(closed)
    return {"ok": True, "warnings": warnings, "count": len(rows), "data": rows}


def _serialize_marker_point(p) -> Dict[str, Any]:
    return {
        "x": str(p.x),
        "y": p.y,
        "trade_id": p.trade_id,
        "type": p.ttype,
        "raw_time": str(p.raw_time),
    }


def _serialize_marker_result(res) -> Dict[str, Any]:
    return {
        "trades_found": res.trades_found,
        "buy_drawn": res.buy_drawn,
        "sell_drawn": res.sell_drawn,
        "buy": [_serialize_marker_point(p) for p in res.buy],
        "sell": [_serialize_marker_point(p) for p in res.sell],
        "unmatched": [
            {
                "trade_id": u.trade_id,
                "kind": u.kind,
                "reason": u.reason,
                "out_of_range": u.out_of_range,
            }
            for u in res.unmatched
        ],
    }


def load_chart_candles(
    symbol: str,
    interval: str = "5m",
    venue: str = "binance",
    limit: int = 500,
) -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    sym = (symbol or "").upper()
    if not sym:
        return {"ok": False, "warnings": [_warning("symbol_required", "symbol query param required")], "data": []}
    limit = max(1, min(2000, int(limit)))
    try:
        if venue.lower() == "mexc":
            from exchanges.mexc import public_klines
            df = public_klines(sym, interval, limit=limit)
        else:
            try:
                from binance_client import public_klines
            except ImportError as exc:
                warnings.append(_warning("binance_client_unavailable", str(exc)))
                return {"ok": False, "warnings": warnings, "count": 0, "data": []}
            df = public_klines(sym, interval, limit=limit)
        candles = []
        for _, row in df.iterrows():
            candles.append({
                "open_time": str(row.get("open_time")),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
            })
        return {"ok": True, "warnings": warnings, "count": len(candles), "data": candles}
    except Exception as exc:
        warnings.append(_warning("candles_fetch_error", str(exc)))
        return {"ok": False, "warnings": warnings, "count": 0, "data": []}


def load_chart_markers(symbol: str) -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    sym = (symbol or "").upper()
    if not sym:
        return {"ok": False, "warnings": [_warning("symbol_required", "symbol query param required")], "data": {}}
    trades, tw = _load_trade_files()
    warnings.extend(tw)
    try:
        try:
            from binance_client import public_klines
        except ImportError as exc:
            warnings.append(_warning("binance_client_unavailable", str(exc)))
            return {"ok": False, "warnings": warnings, "data": {}}
        from chart_markers import build_trade_markers
        df = public_klines(sym, "5m", limit=500)
        candle_times = df["open_time"]
        res = build_trade_markers(trades, sym, candle_times)
        return {"ok": True, "warnings": warnings, "data": _serialize_marker_result(res)}
    except Exception as exc:
        warnings.append(_warning("markers_error", str(exc)))
        return {"ok": False, "warnings": warnings, "data": {}}
