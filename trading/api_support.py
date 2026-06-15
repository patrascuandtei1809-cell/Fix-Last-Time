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
    hb = load_heartbeats()
    if hb["warnings"]:
        warnings.extend(hb["warnings"])
    return {
        "ok": True,
        "service": "alphatrade-api",
        "mode": "read-only",
        "at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "heartbeats": hb["data"],
        "warnings": warnings,
    }
