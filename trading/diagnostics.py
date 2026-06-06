"""AlphaTrade diagnostics — OBSERVABILITY + SAFETY ONLY.

This module NEVER changes strategy logic, thresholds, gates, or sizing. It only
*observes* what the bot decides and *reports* it, plus two safe maintenance
helpers:

  1. Decision journal  — records every per-symbol decision each cycle (signal,
     score, confidence, regime, block reason, timestamp) and aggregates the
     reasons so we can answer "WHY NO TRADE?" and "Top 10 reasons".
  2. reconcile_ghost_trades() — closes LOCAL open trades that no longer exist on
     Binance (operator-safe, conservative: only the clear "no balance" case).
  3. preflight_checks() — verifies API connection, balances, minNotional,
     stepSize, and quantity precision per symbol.
  4. trade_frequency_stats() — trades today, avg/day, last trade, mins since.
  5. build_report() — a plain-text "Top 10 reasons the bot is not trading".

Run on the droplet:  cd trading && python3 diagnostics.py
"""
from __future__ import annotations

import threading
from collections import deque, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Decision journal (thread-safe, in-process — shared with the orchestrator
# thread and the Streamlit UI which live in the SAME process).
# ─────────────────────────────────────────────────────────────────────────────
_LOCK = threading.RLock()
_MAX_DECISIONS = 3000

_decisions: deque = deque(maxlen=_MAX_DECISIONS)   # list of decision dicts
_block_counter: Counter = Counter()                # category -> count (blocks only)
_cycle_count: int = 0
_traded_count: int = 0
_last_cycle_at: Optional[datetime] = None
# Latest per-symbol decision, for the "current reason" panel.
_latest_by_symbol: Dict[str, Dict] = {}


def _categorize(raw: str) -> str:
    """Bucket a free-text block reason into a stable category so counts mean
    something. Falls back to a trimmed snippet."""
    r = (raw or "").lower()
    if not r:
        return "Other"
    if "waiting for api keys" in r or "no authenticated client" in r:
        return "Not connected (no API keys)"
    if "emergency stop" in r:
        return "Emergency stop active"
    if "daily loss" in r:
        return "Daily-loss auto-halt"
    if "cooldown" in r:
        return "Per-symbol cooldown"
    if "direction lock" in r:
        return "Direction lock (need opposite signal)"
    if "concentration cap" in r:
        return "Per-symbol concentration cap"
    if "global open-trade cap" in r:
        return "Global max-open cap"
    if "symbol cap" in r or "max open trades reached for" in r or "no stacking" in r:
        return "Per-symbol max-open cap"
    if "session trade limit" in r:
        return "Per-symbol session limit"
    if "spending limit" in r or "exposure" in r:
        return "Spending / exposure limit"
    if "throttle" in r:
        return "Global throttle (time between trades)"
    if "min notional" in r or "binance min" in r or "too small" in r or \
       ("notional" in r and "min" in r):
        return "Account too small (min notional)"
    if "balance fetch failed" in r:
        return "Balance fetch failed"
    if "order failed" in r or "order rejected" in r or "-2010" in r:
        return "Order rejected by Binance"
    # ── 20-Minute Dip engine reasons ─────────────────────────────────────────
    if "not connected" in r or "no api key" in r:
        return "Not connected (no API keys)"
    if "safe mode" in r:
        return "Safe mode ON (no new entries)"
    if "position already open (manual)" in r or "will not manage" in r:
        return "Manual position (not managed)"
    if "waiting for dip" in r or "dip not deep enough" in r or "20m change" in r:
        return "Waiting for dip (target not reached)"
    if "volume too low" in r:
        return "Volume too low (below multiple)"
    if "trend filter" in r or "waiting for upturn" in r:
        return "Trend filter (no upturn yet)"
    if "insufficient balance" in r:
        return "Insufficient balance"
    if "not enough candle data" in r or "insufficient data" in r:
        return "Insufficient candle data"
    if "risk gate" in r:
        return "Global risk gate"
    if "exchange error" in r or "fetch failed" in r:
        return "Exchange error"
    if "engine error" in r:
        return "Engine error"
    if "no weighted edge" in r or "score=0" in r:
        return "No weighted edge (score 0 / risk veto)"
    if "below threshold" in r or "neither entry path" in r:
        return "Below score/confidence threshold"
    if "not top-ranked" in r or "another symbol scored higher" in r:
        return "Not top-ranked (lost cross-symbol rank)"
    if "hold" in r or "no directional signal" in r:
        return "HOLD (no directional signal)"
    if "gpt" in r:
        return "GPT veto"
    return (raw or "Other")[:48]


def _derive_reason(snap: Dict, *, score_threshold: int, confidence_floor: int,
                   winner_symbol: Optional[str], throttle_left: int,
                   n_open: int, cap: int, gpt_block: str,
                   traded: bool) -> Tuple[str, Optional[str], bool]:
    """Compute the EXACT reason this symbol did/didn't trade this cycle.

    Returns (reason_text, category_or_None, traded_bool). category is None when
    the symbol actually traded (not a block)."""
    sym   = snap.get("symbol")
    sig   = (snap.get("signal") or "").upper()
    score = int(snap.get("score") or 0)
    conf  = int(snap.get("confidence") or 0)
    wblk  = (snap.get("worker_block") or "").strip()

    if sig not in ("BUY", "SELL"):
        return "HOLD — no directional signal", "HOLD (no directional signal)", False
    if score <= 0:
        return ("No weighted edge (score=0 / hard risk veto)",
                "No weighted edge (score 0 / risk veto)", False)
    if not (score >= score_threshold or conf >= confidence_floor):
        return (f"Below threshold: score {score} < {score_threshold} "
                f"AND confidence {conf} < {confidence_floor}",
                "Below score/confidence threshold", False)

    # Candidate QUALIFIED. From here on, only one symbol per cycle can win.
    if winner_symbol and sym != winner_symbol:
        return ("Qualified but not top-ranked — another symbol scored higher "
                "this cycle", "Not top-ranked (lost cross-symbol rank)", False)

    if sym == winner_symbol:
        if traded:
            return "TRADED ✅", None, True
        if gpt_block:
            return gpt_block, "GPT veto", False
        if n_open >= cap:
            return (f"Global max open trades reached ({n_open}/{cap})",
                    "Global max-open cap", False)
        if throttle_left > 0:
            return (f"Global throttle — {throttle_left}s until next trade allowed",
                    "Global throttle (time between trades)", False)
        if wblk:
            return wblk, _categorize(wblk), False
        return ("Blocked at order placement (per-symbol / global / sizing / "
                "balance gate)", "Blocked at order placement", False)

    return "Qualified — awaiting selection", "Other", False


def record_cycle(*, snaps: List[Dict], score_threshold: int,
                 confidence_floor: int, winner_symbol: Optional[str],
                 throttle_sec: int, last_global_trade_at: float,
                 n_open: int, cap: int, gpt_block: str, traded: bool) -> None:
    """Called once per orchestrator cycle. Pure observation — never raises out."""
    global _cycle_count, _traded_count, _last_cycle_at
    try:
        import time as _t
        secs_since = _t.time() - (last_global_trade_at or 0)
        throttle_left = max(0, int(throttle_sec) - int(secs_since))
        now = datetime.now()
        with _LOCK:
            _cycle_count += 1
            _last_cycle_at = now
            if traded:
                _traded_count += 1
            for snap in snaps:
                reason, cat, did_trade = _derive_reason(
                    snap, score_threshold=score_threshold,
                    confidence_floor=confidence_floor,
                    winner_symbol=winner_symbol, throttle_left=throttle_left,
                    n_open=n_open, cap=cap, gpt_block=gpt_block,
                    traded=traded,
                )
                rec = {
                    "ts":         now,
                    "symbol":     snap.get("symbol"),
                    "signal":     (snap.get("signal") or "—"),
                    "score":      int(snap.get("score") or 0),
                    "confidence": int(snap.get("confidence") or 0),
                    "regime":     snap.get("regime") or "",
                    "reason":     reason,
                    "category":   cat or "TRADED",
                    "blocked":    not did_trade,
                }
                _decisions.append(rec)
                _latest_by_symbol[snap.get("symbol")] = rec
                if not did_trade and cat:
                    _block_counter[cat] += 1
    except Exception as e:
        print(f"[DIAG] record_cycle error: {e}", flush=True)


def record_dip_decision(*, symbol: str, signal: str, reason: str,
                        traded: bool, blocked: bool,
                        change_pct: Optional[float] = None,
                        volume_ratio: Optional[float] = None) -> None:
    """Record ONE per-symbol decision from the 20-Minute Dip live engine.

    The dip path does NOT use the legacy score/threshold model, so it feeds its
    own ActivityRecord-derived snapshot straight into the same journal that
    powers the 'WHY NO TRADE?' panel. Pure observation — never raises out."""
    try:
        now = datetime.now()
        cat = None if traded else _categorize(reason)
        rec = {
            "ts":           now,
            "symbol":       symbol,
            "signal":       (signal or "—").upper(),
            "score":        0,
            "confidence":   0,
            "regime":       "",
            "reason":       reason or "",
            "category":     cat or "TRADED",
            "blocked":      bool(blocked),
            "change_pct":   change_pct,
            "volume_ratio": volume_ratio,
        }
        with _LOCK:
            _decisions.append(rec)
            _latest_by_symbol[symbol] = rec
            if blocked and cat:
                _block_counter[cat] += 1
    except Exception as e:
        print(f"[DIAG] record_dip_decision error: {e}", flush=True)


def record_dip_cycle(traded: bool) -> None:
    """Bump the cycle counters once per orchestrator cycle (dip path)."""
    global _cycle_count, _traded_count, _last_cycle_at
    try:
        with _LOCK:
            _cycle_count += 1
            _last_cycle_at = datetime.now()
            if traded:
                _traded_count += 1
    except Exception as e:
        print(f"[DIAG] record_dip_cycle error: {e}", flush=True)


# ── Journal getters (for the dashboard + report) ────────────────────────────
def get_latest_by_symbol() -> Dict[str, Dict]:
    with _LOCK:
        return dict(_latest_by_symbol)


def get_recent_decisions(limit: int = 50,
                         symbol: Optional[str] = None) -> List[Dict]:
    with _LOCK:
        items = list(_decisions)
    if symbol:
        items = [d for d in items if d.get("symbol") == symbol]
    return items[-limit:][::-1]   # newest first


def get_block_summary(top: int = 10) -> List[Dict]:
    """Top block reasons by frequency: [{category, count, pct}], newest-data."""
    with _LOCK:
        total = sum(_block_counter.values())
        items = _block_counter.most_common(top)
    return [
        {"category": c, "count": n,
         "pct": (n / total * 100.0) if total else 0.0}
        for c, n in items
    ]


def get_cycle_stats() -> Dict:
    with _LOCK:
        return {
            "cycles":      _cycle_count,
            "traded":      _traded_count,
            "last_cycle":  _last_cycle_at,
            "total_blocks": sum(_block_counter.values()),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Trade-frequency statistics (reads local trade journal via bot persistence)
# ─────────────────────────────────────────────────────────────────────────────
def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        try:
            return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def trade_frequency_stats() -> Dict:
    """trades today, average trades/day, last trade time, minutes since last."""
    import bot as _bot
    trades = _bot.load_trades()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    open_dts = [_parse_dt(t.get("open_time")) for t in trades]
    open_dts = [d for d in open_dts if d is not None]

    total = len(open_dts)
    today_count = sum(1 for d in open_dts if d.strftime("%Y-%m-%d") == today)
    last_dt = max(open_dts) if open_dts else None
    first_dt = min(open_dts) if open_dts else None

    if first_dt:
        span_days = max(1, (now.date() - first_dt.date()).days + 1)
        avg_per_day = total / span_days
    else:
        avg_per_day = 0.0

    mins_since = ((now - last_dt).total_seconds() / 60.0) if last_dt else None

    open_count = sum(1 for t in trades if t.get("status") == "open")
    bot_count  = sum(1 for t in trades if t.get("type") == "bot")
    manual_count = total - bot_count

    return {
        "total_trades":   total,
        "trades_today":   today_count,
        "avg_per_day":    round(avg_per_day, 2),
        "last_trade_at":  last_dt,
        "minutes_since_last": round(mins_since, 1) if mins_since is not None else None,
        "open_trades":    open_count,
        "bot_trades":     bot_count,
        "manual_trades":  manual_count,
        "first_trade_at": first_dt,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pre-trade verification (connection / balance / minNotional / stepSize / prec)
# ─────────────────────────────────────────────────────────────────────────────
def preflight_checks(exchange, symbols: List[str]) -> Dict:
    """Verify the exchange plumbing needed to place a REAL order.

    `exchange` is an exchanges.base.Exchange (e.g. BinanceExchange). On Replit
    api.binance.com is geo-blocked (HTTP 451), so this is meant to run on the
    droplet. It degrades gracefully when there is no client / no connection."""
    result: Dict = {"ok": False, "connected": False, "usdt_free": None,
                    "errors": [], "symbols": {}}

    client = getattr(exchange, "client", None)
    if exchange is None or client is None:
        result["errors"].append("No authenticated client — connect a LIVE "
                                 "Binance API key first.")
        return result

    # 1. Connection
    try:
        ok_conn = client.test_connection()
        result["connected"] = bool(ok_conn)
        if not ok_conn:
            result["errors"].append("test_connection() returned False.")
    except Exception as e:
        result["errors"].append(f"Connection failed: {e}")
        return result

    # 2. USDT balance
    try:
        bal = exchange.get_balance("USDT")
        result["usdt_free"] = float(bal.get("free", 0) or 0)
    except Exception as e:
        result["errors"].append(f"Balance fetch failed: {e}")

    # 3. Per-symbol filters + precision sanity
    for sym in symbols:
        s: Dict = {}
        try:
            filt = exchange.get_symbol_filters(sym)
            s["min_notional"] = filt.get("min_notional")
            s["step_size"]    = filt.get("step_size")
            s["min_qty"]      = filt.get("min_qty")
            # Sample: what qty would a $10 order round to? Confirms precision.
            try:
                px = exchange.get_price(sym)
                s["price"] = px
                raw_qty = (10.0 / px) if px else 0.0
                s["sample_qty_$10"] = exchange.round_quantity(sym, raw_qty)
                s["meets_min_qty"] = (s["sample_qty_$10"] >= (s["min_qty"] or 0))
            except Exception as e:
                s["price_error"] = str(e)
            s["ok"] = True
        except Exception as e:
            s["ok"] = False
            s["error"] = str(e)
            result["errors"].append(f"{sym}: {e}")
        result["symbols"][sym] = s

    result["ok"] = result["connected"] and not result["errors"]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Ghost-trade reconciliation
# ─────────────────────────────────────────────────────────────────────────────
# A LOCAL open BUY trade represents holding the base asset on Binance. If the
# Binance balance for that base asset is effectively ZERO (dust), the position
# does NOT exist on Binance — it's a ghost (sold elsewhere, never filled, or a
# stale local record). We mark it closed. We CLOSE AT ENTRY PRICE (P&L = 0)
# because the true outcome is unknown — inventing a P&L from current price would
# be fake P&L. Reason is annotated RECONCILED so it's auditable.
#
# CONSERVATIVE: we only auto-close the CLEAR "no balance" case. If the balance
# is partial (some but less than recorded), we LOG a mismatch warning but do NOT
# auto-close, to avoid ever closing a position that really exists.
def reconcile_ghost_trades(exchange, dry_run: bool = False) -> Dict:
    import bot as _bot

    out: Dict = {"checked": 0, "ghosts": [], "mismatches": [], "closed": [],
                 "errors": [], "dry_run": dry_run}

    client = getattr(exchange, "client", None)
    if exchange is None or client is None:
        out["errors"].append("No authenticated client — cannot reconcile.")
        return out

    open_trades = [t for t in _bot.load_trades() if t.get("status") == "open"]
    out["checked"] = len(open_trades)
    if not open_trades:
        return out

    # Pull all balances once.
    try:
        balances = client.get_all_balances()   # {asset: {free, locked, total}}
    except Exception as e:
        out["errors"].append(f"get_all_balances failed: {e}")
        return out

    def _base(sym: str) -> str:
        for q in ("USDT", "BUSD", "USDC"):
            if sym.upper().endswith(q):
                return sym[: -len(q)]
        return sym

    # Sum recorded qty per base asset (across all open BUY trades) so we can
    # compare against the real Binance holding.
    recorded_by_base: Dict[str, float] = {}
    for t in open_trades:
        if (t.get("side") or "").upper() != "BUY":
            continue
        b = _base(t.get("coin", ""))
        recorded_by_base[b] = recorded_by_base.get(b, 0.0) + float(t.get("quantity") or 0)

    for t in open_trades:
        side = (t.get("side") or "").upper()
        sym  = t.get("coin", "")
        b    = _base(sym)
        rec_qty = float(t.get("quantity") or 0)

        # We only reconcile spot BUY (long) positions — a SELL/short isn't a
        # base-asset holding and can't be verified this way.
        if side != "BUY":
            continue

        real = balances.get(b, {}) or {}
        real_total = float(real.get("total", 0) or 0)

        # Dust threshold = essentially NOTHING on Binance: at or below the
        # smallest tradable quantity (or a tiny epsilon if the exchange reports
        # no min_qty). It is NEVER scaled by the recorded qty — a partial
        # holding is a MISMATCH (left open for review), not a ghost. This is the
        # only case we auto-close, so we can never close a position that still
        # has a real balance on Binance.
        try:
            min_qty = float(exchange.get_symbol_filters(sym).get("min_qty") or 0)
        except Exception:
            min_qty = 0.0
        dust = min_qty if min_qty > 0 else 1e-8

        if real_total <= dust:
            # GHOST — no meaningful balance on Binance for this base asset.
            ghost = {"id": t.get("id"), "symbol": sym, "recorded_qty": rec_qty,
                     "binance_total": real_total, "entry": t.get("entry_price")}
            out["ghosts"].append(ghost)
            if not dry_run:
                entry = float(t.get("entry_price") or 0)
                closed = _bot.close_trade(
                    t.get("id"), entry,
                    f"RECONCILED — ghost trade: no {b} balance on Binance "
                    f"(recorded {rec_qty}, Binance total {real_total}). "
                    f"Closed at entry (P&L unknown → 0).",
                )
                if closed:
                    out["closed"].append(t.get("id"))
                    try:
                        _bot.log_activity(
                            "WARNING",
                            f"🧹 RECONCILED ghost {sym} #{t.get('id')} — no {b} "
                            f"balance on Binance (recorded {rec_qty}, real "
                            f"{real_total}). Marked closed at entry, P&L=0.")
                    except Exception:
                        pass
        elif real_total < recorded_by_base.get(b, 0) * 0.95:
            # Partial mismatch — flag for review, do NOT auto-close.
            out["mismatches"].append({
                "symbol": sym, "id": t.get("id"), "recorded_qty": rec_qty,
                "binance_total": real_total,
                "recorded_total_for_base": recorded_by_base.get(b, 0),
            })

    if out["mismatches"]:
        try:
            _bot.log_activity(
                "WARNING",
                f"⚠️ Reconcile: {len(out['mismatches'])} open trade(s) have a "
                f"PARTIAL balance mismatch vs Binance — left OPEN for review.")
        except Exception:
            pass

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Text report — "Top 10 reasons the bot is not trading"
# ─────────────────────────────────────────────────────────────────────────────
def build_report(exchange=None, symbols: Optional[List[str]] = None) -> str:
    symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    lines: List[str] = []
    add = lines.append

    add("=" * 70)
    add("ALPHATRADE — BOT TRADING DIAGNOSTIC REPORT")
    add(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    add("=" * 70)

    # 1. Frequency stats
    try:
        f = trade_frequency_stats()
        add("\n── TRADE FREQUENCY ─────────────────────────────────────────")
        add(f"  Total trades (all time): {f['total_trades']}")
        add(f"  Trades today:            {f['trades_today']}")
        add(f"  Average trades/day:      {f['avg_per_day']}")
        add(f"  Open trades now:         {f['open_trades']}")
        add(f"  Bot / manual:            {f['bot_trades']} / {f['manual_trades']}")
        if f["last_trade_at"]:
            add(f"  Last trade:              {f['last_trade_at'].isoformat(timespec='seconds')}")
            add(f"  Minutes since last:      {f['minutes_since_last']}")
        else:
            add("  Last trade:              NONE — bot has never traded")
    except Exception as e:
        add(f"  [frequency stats error: {e}]")

    # 2. Decision cycle stats
    cs = get_cycle_stats()
    add("\n── DECISION ENGINE (this run) ──────────────────────────────")
    add(f"  Orchestrator cycles observed: {cs['cycles']}")
    add(f"  Cycles that traded:           {cs['traded']}")
    add(f"  Total block events:           {cs['total_blocks']}")
    if cs["last_cycle"]:
        add(f"  Last cycle:                   {cs['last_cycle'].isoformat(timespec='seconds')}")
    if cs["cycles"] == 0:
        add("  NOTE: no cycles observed yet this run — start the bot and let it "
            "run a few minutes, then re-run this report.")

    # 3. Top 10 block reasons
    add("\n── TOP 10 REASONS THE BOT IS NOT TRADING ───────────────────")
    summary = get_block_summary(top=10)
    if not summary:
        add("  (no block events recorded yet)")
    else:
        for i, row in enumerate(summary, 1):
            add(f"  {i:2}. {row['category']:<42} {row['count']:>5}  "
                f"({row['pct']:.1f}%)")

    # 4. Current per-symbol reason
    add("\n── CURRENT PER-SYMBOL DECISION ─────────────────────────────")
    latest = get_latest_by_symbol()
    if not latest:
        add("  (no decisions recorded yet)")
    else:
        for sym, d in latest.items():
            add(f"  {sym:<9} {d['signal']:<5} score={d['score']:<3} "
                f"conf={d['confidence']:<3} → {d['reason']}")

    # 5. Pre-flight verification (only if an exchange w/ client is supplied)
    add("\n── PRE-FLIGHT VERIFICATION ─────────────────────────────────")
    if exchange is None or getattr(exchange, "client", None) is None:
        add("  SKIPPED — no connected client. On Replit api.binance.com is "
            "geo-blocked (451); run this on the DigitalOcean droplet with a "
            "connected key for a live check.")
    else:
        pf = preflight_checks(exchange, symbols)
        add(f"  Connected:  {pf['connected']}")
        add(f"  USDT free:  {pf['usdt_free']}")
        for sym, s in pf["symbols"].items():
            if s.get("ok"):
                add(f"  {sym:<9} minNotional=${s.get('min_notional')} "
                    f"step={s.get('step_size')} minQty={s.get('min_qty')} "
                    f"$10→qty={s.get('sample_qty_$10')} "
                    f"meetsMinQty={s.get('meets_min_qty')}")
            else:
                add(f"  {sym:<9} ERROR: {s.get('error')}")
        if pf["errors"]:
            add("  ERRORS:")
            for e in pf["errors"]:
                add(f"    - {e}")

    add("\n" + "=" * 70)
    add("NOTE: This report is diagnostic only. A backtest of the current "
        "strategy showed NO positive edge — trading more often will not by "
        "itself make it profitable. Fix the edge before increasing frequency.")
    add("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    # CLI: print the report. Tries to build a connected exchange for the live
    # pre-flight section; degrades gracefully if creds/connection are absent.
    ex = None
    try:
        import bot as _bot
        b = _bot.get_bot()
        if b and getattr(b, "workers", None):
            ex = next(iter(b.workers.values())).exchange
    except Exception:
        ex = None
    print(build_report(exchange=ex))
