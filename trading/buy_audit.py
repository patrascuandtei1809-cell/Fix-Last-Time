"""BUY decision audit — observability only.

Records every symbol the live DipLiveEngine evaluates each cycle so operators
can see why scored scanner picks are not becoming BUY orders. Never places
orders or changes strategy thresholds.
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DIR = Path(__file__).resolve().parent
DATA_DIR = _DIR / "data"
LIVE_AUDIT_PATH = DATA_DIR / "live_decision_audit.json"
BUY_AUDIT_PATH = DATA_DIR / "buy_audit.json"

_HISTORY_CAP = 500
_LOCK = threading.RLock()
_cycle_decisions: List[Dict[str, Any]] = []
_cycle_started_at: Optional[str] = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_block_reason(reason: str, decision: str = "", traded: bool = False) -> str:
    """Map engine text → stable rejection code for dashboards."""
    if traded and str(decision or "").upper() == "BUY":
        return "executed"
    r = (reason or "").lower()
    d = str(decision or "").upper()
    if "emergency stop" in r:
        return "emergency_stop"
    if "safe mode" in r:
        return "safe_mode"
    if "not connected" in r or "no api key" in r:
        return "not_connected"
    if "volume too low" in r or ("volume" in r and "low" in r):
        return "volume_too_low"
    if "trend filter" in r or "waiting for upturn" in r:
        return "trend_failed"
    if "cooldown" in r:
        return "cooldown_active"
    if "position already open" in r:
        return "position_already_open"
    if "max open" in r or "global max-open" in r or "no stacking" in r:
        return "max_open_trades_reached"
    if "spending limit" in r:
        return "spending_limit"
    if "insufficient balance" in r or "need ≥" in r:
        return "insufficient_balance"
    if "min notional" in r or "min_trade" in r:
        return "min_notional_failed"
    if "precision" in r or "step size" in r or "lot size" in r:
        return "precision_failed"
    if "risk gate" in r or "daily loss" in r or "exposure" in r:
        return "risk_manager_block"
    if "buy order failed" in r or "buy order rejected" in r:
        return "order_rejected"
    if "binance auto-buy blocked" in r or "not in pinned majors" in r:
        return "venue_rule_block"
    if "dip engine error" in r or d == "SKIP" and "error" in r:
        return "engine_error"
    if d == "HOLD" and r.startswith("holding") and ("tp " in r or "sl " in r):
        return "managing_open_position"
    if d == "HOLD" and ("change" in r or "threshold" in r or "waiting" in r):
        return "threshold_not_met"
    if d in ("SELL", "STOP_LOSS", "TAKE_PROFIT"):
        return "managing_open_position"
    return "other"


def _reached_buy_stage(decision: str, traded: bool, block_code: str,
                       change_pct: Optional[float], buy_threshold: float) -> bool:
    if traded or str(decision or "").upper() == "BUY":
        return True
    if block_code in {
        "executed", "risk_manager_block", "insufficient_balance",
        "min_notional_failed", "precision_failed", "order_rejected",
        "venue_rule_block",
    }:
        return True
    if change_pct is not None and buy_threshold is not None:
        try:
            if float(change_pct) <= float(buy_threshold):
                return True
        except (TypeError, ValueError):
            pass
    return False


def begin_cycle() -> None:
    """Mark the start of a new orchestrator dip cycle."""
    global _cycle_decisions, _cycle_started_at
    with _LOCK:
        _cycle_decisions = []
        _cycle_started_at = _utcnow_iso()


def _safe_strategy_analysis(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Store only a diagnostic, explicitly non-executable analysis payload."""
    if not isinstance(value, dict):
        return {}
    analysis = dict(value)
    analysis["mode"] = "shadow"
    analysis["executable"] = False
    analysis["would_trade"] = bool(analysis.get("would_trade", False))
    return analysis


def record_from_activity(
    *,
    exchange: str,
    symbol: str,
    rec,
    score: Optional[int] = None,
    settings=None,
    open_trades: Optional[List[Dict]] = None,
    cooldown_store=None,
    strategy_analysis: Optional[Dict[str, Any]] = None,
) -> None:
    """Record one engine evaluation (called from bot after evaluate())."""
    sym = str(symbol or "").upper()
    reason = str(getattr(rec, "reason", "") or "")
    decision = str(getattr(rec, "decision", "HOLD") or "HOLD")
    traded = bool(getattr(rec, "traded", False))
    block_code = classify_block_reason(reason, decision, traded)
    buy_thr = float(getattr(rec, "buy_threshold", 0) or 0)
    chg = getattr(rec, "change_pct", None)

    position_open = False
    for t in open_trades or []:
        if (t.get("coin") or "").upper() == sym and t.get("status", "open") == "open":
            position_open = True
            break

    cooldown_active = False
    if cooldown_store is not None and settings is not None:
        try:
            from live_engine import cooldown_block
            state = cooldown_store.get(sym)
            cooldown_active, _ = cooldown_block(settings, state)
        except Exception:
            pass

    min_notional = None
    if settings is not None:
        try:
            min_notional = float(
                getattr(settings, "min_trade_size_usdt", 10.0) or 10.0
            )
        except Exception:
            min_notional = 10.0

    max_open_reached = block_code == "max_open_trades_reached"

    entry = {
        "timestamp": _utcnow_iso(),
        "exchange": str(exchange or "binance").lower(),
        "symbol": sym,
        "score": score,
        "price": getattr(rec, "price", None),
        "change_pct": chg,
        "buy_threshold_pct": buy_thr,
        "decision": decision,
        "buy_allowed": traded and decision == "BUY",
        "exact_block_reason": block_code,
        "exact_rejection_reason": block_code,
        "rejection_detail": reason[:300] if reason else "",
        "free_balance": getattr(rec, "free_usdt", None),
        "min_notional": min_notional,
        "max_open_reached": max_open_reached,
        "position_open": position_open,
        "cooldown_active": cooldown_active,
        "volume_ratio": getattr(rec, "volume_ratio", None),
        "trend_ok": getattr(rec, "trend_ok", None),
        "traded": traded,
        "reached_buy_stage": _reached_buy_stage(
            decision, traded, block_code, chg, buy_thr),
        "strategy_analysis": _safe_strategy_analysis(strategy_analysis),
    }
    with _LOCK:
        _cycle_decisions.append(entry)


def _summarize(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    counter = Counter(
        d.get("exact_block_reason") or "other" for d in decisions
    )
    top = [{"reason": k, "count": v} for k, v in counter.most_common(15)]
    evaluated = len(decisions)
    executed = sum(1 for d in decisions if d.get("traded"))
    reached = sum(1 for d in decisions if d.get("reached_buy_stage"))
    rejected = sum(
        1 for d in decisions
        if (
            not d.get("traded")
            and d.get("decision") not in ("SELL", "STOP_LOSS")
            and d.get("exact_block_reason") != "managing_open_position"
        )
    )
    return {
        "evaluated": evaluated,
        "reached_buy_stage": reached,
        "rejected": rejected,
        "executed": executed,
        "top_block_reasons": top,
    }


def _load_history() -> List[Dict[str, Any]]:
    if not BUY_AUDIT_PATH.is_file():
        return []
    try:
        raw = json.loads(BUY_AUDIT_PATH.read_text(encoding="utf-8"))
        items = raw.get("history") if isinstance(raw, dict) else raw
        return list(items or []) if isinstance(items, list) else []
    except Exception:
        return []


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def finish_cycle() -> None:
    """Persist latest cycle + append rolling history."""
    with _LOCK:
        decisions = list(_cycle_decisions)
        started = _cycle_started_at or _utcnow_iso()
        summary = _summarize(decisions)

        live_payload = {
            "updated_at": _utcnow_iso(),
            "cycle_started_at": started,
            "summary": summary,
            "decisions": decisions,
            "mexc_rejected": [
                d for d in decisions
                if d.get("exchange") == "mexc" and not d.get("buy_allowed")
            ],
            "binance_rejected": [
                d for d in decisions
                if d.get("exchange") == "binance" and not d.get("buy_allowed")
            ],
        }
        try:
            _write_json(LIVE_AUDIT_PATH, live_payload)
        except Exception as exc:
            print(f"[BUY-AUDIT] live_decision_audit write failed: {exc}", flush=True)

        history = _load_history()
        for d in decisions:
            history.append(d)
        history = history[-_HISTORY_CAP:]
        buy_payload = {
            "updated_at": _utcnow_iso(),
            "summary": summary,
            "latest_cycle": live_payload,
            "history": history,
        }
        try:
            _write_json(BUY_AUDIT_PATH, buy_payload)
        except Exception as exc:
            print(f"[BUY-AUDIT] buy_audit write failed: {exc}", flush=True)


def load_live_audit() -> Dict[str, Any]:
    if not LIVE_AUDIT_PATH.is_file():
        return {}
    try:
        return json.loads(LIVE_AUDIT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_buy_audit() -> Dict[str, Any]:
    if not BUY_AUDIT_PATH.is_file():
        return {}
    try:
        return json.loads(BUY_AUDIT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record_engine_error(
    *,
    exchange: str,
    symbol: str,
    error: str,
    score: Optional[int] = None,
    settings=None,
    open_trades: Optional[List[Dict]] = None,
    cooldown_store=None,
    strategy_analysis: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a dip-engine crash without changing trading behaviour."""
    if strategy_analysis is None:
        strategy_analysis = {
            "mode": "shadow",
            "executable": False,
            "version": "shadow-v1",
            "status": "unavailable",
            "recommended_strategy": "unknown",
            "market_regime": "unknown",
            "total_score": None,
            "confidence": 0.0,
            "risk_level": "unknown",
            "would_trade": False,
            "components": {},
            "assessments": [],
            "reasons": [],
            "warnings": ["Live engine failed before shadow analysis"],
        }
    reason = f"Dip engine error: {error}"
    stub = type(
        "_StubRec",
        (),
        {
            "reason": reason,
            "decision": "SKIP",
            "traded": False,
            "price": None,
            "change_pct": None,
            "volume_ratio": None,
            "trend_ok": None,
            "free_usdt": None,
            "buy_threshold": float(
                getattr(settings, "dip_buy_pct", 0) or 0
            ) if settings else 0,
        },
    )()
    record_from_activity(
        exchange=exchange,
        symbol=symbol,
        rec=stub,
        score=score,
        settings=settings,
        open_trades=open_trades,
        cooldown_store=cooldown_store,
        strategy_analysis=strategy_analysis,
    )


def audit_display_rows(limit: int = 50) -> List[Dict[str, Any]]:
    """Flatten latest decisions for dashboard tables."""
    live = load_live_audit()
    decisions = list(live.get("decisions") or [])
    rows = []
    for d in reversed(decisions[-limit:]):
        rows.append({
            "Time": (d.get("timestamp") or "")[:19].replace("T", " "),
            "Ex": d.get("exchange", "—"),
            "Symbol": (d.get("symbol") or "—").replace("USDT", ""),
            "Score": (
                str(d.get("score")) if d.get("score") is not None else "—"
            ),
            "ML %": (
                f"{float(d['change_pct']):+.2f}%"
                if d.get("change_pct") is not None else "—"
            ),
            "Decision": d.get("decision", "—"),
            "Block": d.get("exact_block_reason", "—"),
            "Vol": (
                f"{float(d['volume_ratio']):.2f}×"
                if d.get("volume_ratio") is not None else "—"
            ),
            "Trend": (
                "✓" if d.get("trend_ok") is True
                else "✗" if d.get("trend_ok") is False else "—"
            ),
            "Detail": (d.get("rejection_detail") or "")[:80],
        })
    return rows


def audit_history_rows(limit: int = 100) -> List[Dict[str, Any]]:
    """Rolling history rows for the BUY AUDIT tab."""
    payload = load_buy_audit()
    history = list(payload.get("history") or [])
    rows = []
    for d in reversed(history[-limit:]):
        rows.append({
            "Time": (d.get("timestamp") or "")[:19].replace("T", " "),
            "Ex": d.get("exchange", "—"),
            "Symbol": (d.get("symbol") or "—").replace("USDT", ""),
            "Score": (
                str(d.get("score")) if d.get("score") is not None else "—"
            ),
            "Price": (
                f"{float(d['price']):.6g}"
                if d.get("price") is not None else "—"
            ),
            "ML %": (
                f"{float(d['change_pct']):+.2f}%"
                if d.get("change_pct") is not None else "—"
            ),
            "Decision": d.get("decision", "—"),
            "Allowed": "✓" if d.get("buy_allowed") else "✗",
            "Block": d.get("exact_block_reason", "—"),
            "Balance": (
                f"${float(d['free_balance']):.2f}"
                if d.get("free_balance") is not None else "—"
            ),
            "Min$": (
                f"${float(d['min_notional']):.2f}"
                if d.get("min_notional") is not None else "—"
            ),
            "MaxOpen": "✓" if d.get("max_open_reached") else "—",
            "PosOpen": "✓" if d.get("position_open") else "—",
            "Cooldown": "✓" if d.get("cooldown_active") else "—",
            "Vol": (
                f"{float(d['volume_ratio']):.2f}×"
                if d.get("volume_ratio") is not None else "—"
            ),
            "Trend": (
                "✓" if d.get("trend_ok") is True
                else "✗" if d.get("trend_ok") is False else "—"
            ),
            "Detail": (d.get("rejection_detail") or "")[:60],
        })
    return rows


def strategy_intelligence_rows(limit: int = 50) -> List[Dict[str, Any]]:
    """Latest shadow recommendations for the dashboard."""
    live = load_live_audit()
    decisions = list(live.get("decisions") or [])
    rows = []
    for decision in reversed(decisions[-limit:]):
        analysis = decision.get("strategy_analysis") or {}
        if not analysis:
            continue
        snapshot = analysis.get("snapshot") or {}
        components = analysis.get("components") or {}
        component_text = " · ".join(
            f"{name} {float(value.get('score') or 0):.0f}"
            for name, value in components.items()
            if isinstance(value, dict)
        )
        spread = snapshot.get("spread_pct")
        cross = snapshot.get("cross_exchange_spread_pct")
        rows.append({
            "Time": (decision.get("timestamp") or "")[:19].replace("T", " "),
            "Ex": decision.get("exchange", "—"),
            "Symbol": (decision.get("symbol") or "—").replace("USDT", ""),
            "Strategy": analysis.get("recommended_strategy", "unknown"),
            "Regime": analysis.get("market_regime", "unknown"),
            "Score": (
                f"{float(analysis['total_score']):.1f}"
                if analysis.get("total_score") is not None else "—"
            ),
            "Risk": analysis.get("risk_level", "unknown"),
            "Shadow signal": "YES" if analysis.get("would_trade") else "NO",
            "Spread": f"{float(spread):.3f}%" if spread is not None else "—",
            "Cross-ex": f"{float(cross):.3f}%" if cross is not None else "—",
            "Components": component_text[:180] or "—",
        })
    return rows


def rejected_symbols(exchange: str) -> List[str]:
    live = load_live_audit()
    out = []
    for d in live.get("decisions") or []:
        if (
            str(d.get("exchange") or "").lower() == exchange.lower()
            and not d.get("buy_allowed")
        ):
            sym = str(d.get("symbol") or "").replace("USDT", "")
            if sym and sym not in out:
                out.append(sym)
    return out
