"""GPT Advisor — non-blocking secondary AI for HYBRID MODE.

Design contract (HYBRID MODE spec):
  - Rule-based strategy is PRIMARY and always executes.
  - GPT (gpt-4o-mini) is SECONDARY, called ONLY when:
      * rule confidence < 60, OR
      * rule signal is HOLD on a moving market.
  - Throttled to ≤ 1 call per symbol per `MIN_INTERVAL_SEC` (default 10s).
  - ENTIRELY ASYNC: tick() reads the last cached verdict instantly and
    schedules a background refresh; it NEVER blocks waiting for OpenAI.
  - If OPENAI_API_KEY is missing, the advisor degrades silently — every
    call returns None and the rule signal is used unchanged.

Public API:
  advisor = get_advisor()
  cached  = advisor.get_cached(symbol)           # instant — dict or None
  advisor.maybe_request(symbol, snapshot)        # non-blocking; spawns thread
  advisor.status()                               # {"active": bool, "last_ts": float, "last_symbol": str, "enabled": bool}
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

# Throttling + cache lifetime
MIN_INTERVAL_SEC = 10.0   # min seconds between calls for the SAME symbol
CACHE_TTL_SEC    = 30.0   # verdict is considered fresh for this long
TIMEOUT_SEC      = 6.0    # hard cap on each OpenAI call
ACTIVE_WINDOW    = 20.0   # status shows "ACTIVE" if any call in this window
MODEL            = "gpt-4o-mini"


@dataclass
class GPTVerdict:
    symbol:     str
    decision:   str            # "BUY" | "SELL" | "HOLD"
    confidence: int            # 0..100
    reason:     str
    ts:         float          # unix seconds when produced

    def is_fresh(self, ttl: float = CACHE_TTL_SEC) -> bool:
        return (time.time() - self.ts) <= ttl

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["age_sec"] = round(time.time() - self.ts, 1)
        return d


class GPTAdvisor:
    def __init__(self) -> None:
        self._lock           = threading.Lock()
        self._cache:         Dict[str, GPTVerdict] = {}
        self._last_call_at:  Dict[str, float]      = {}   # per-symbol last attempt
        self._in_flight:     Dict[str, bool]       = {}
        self._client                                = None
        self._enabled                               = bool(os.environ.get("OPENAI_API_KEY"))
        self._last_global_ts:  float = 0.0
        self._last_symbol:     str   = ""
        self._total_calls:     int   = 0
        self._total_errors:    int   = 0
        self._last_error:      str   = ""

    # ── client lazy init (avoid import cost when disabled) ───────────────
    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._enabled:
            return None
        try:
            from openai import OpenAI
            self._client = OpenAI(timeout=TIMEOUT_SEC)
            return self._client
        except Exception as e:
            self._last_error = f"client init: {e}"
            self._enabled    = False
            return None

    # ── public: instant cache read ───────────────────────────────────────
    def get_cached(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            v = self._cache.get(symbol)
        if v is None or not v.is_fresh():
            return None
        return v.to_dict()

    # ── public: schedule non-blocking refresh ────────────────────────────
    def maybe_request(self, symbol: str, snapshot: Dict[str, Any]) -> None:
        """Spawn background thread to query GPT — returns immediately.

        Throttled: skipped if last call for this symbol was < MIN_INTERVAL_SEC
        ago, or if a call is already in flight, or if disabled.
        """
        if not self._enabled:
            return
        now = time.time()
        with self._lock:
            if self._in_flight.get(symbol):
                return
            last = self._last_call_at.get(symbol, 0.0)
            if (now - last) < MIN_INTERVAL_SEC:
                return
            self._in_flight[symbol]    = True
            self._last_call_at[symbol] = now
        t = threading.Thread(
            target=self._run_call, args=(symbol, snapshot),
            daemon=True, name=f"gpt-advisor-{symbol}",
        )
        t.start()

    # ── public: status for dashboard badge ───────────────────────────────
    def status(self) -> Dict[str, Any]:
        with self._lock:
            last_ts     = self._last_global_ts
            last_sym    = self._last_symbol
            errors      = self._total_errors
            calls       = self._total_calls
            err         = self._last_error
        active = self._enabled and (time.time() - last_ts) <= ACTIVE_WINDOW
        return {
            "enabled":     self._enabled,
            "active":      active,
            "last_ts":     last_ts,
            "last_symbol": last_sym,
            "total_calls": calls,
            "errors":      errors,
            "last_error":  err,
            "model":       MODEL,
        }

    # ── internals ────────────────────────────────────────────────────────
    def _run_call(self, symbol: str, snapshot: Dict[str, Any]) -> None:
        try:
            verdict = self._call_openai(symbol, snapshot)
            if verdict is not None:
                with self._lock:
                    self._cache[symbol]   = verdict
                    self._last_global_ts  = verdict.ts
                    self._last_symbol     = symbol
                    self._total_calls    += 1
                print(f"[GPT] {symbol} {verdict.decision} "
                      f"conf={verdict.confidence} reason={verdict.reason[:80]}",
                      flush=True)
        except Exception as e:
            with self._lock:
                self._total_errors += 1
                self._last_error    = str(e)[:200]
            print(f"[GPT] {symbol} ERROR {e}", flush=True)
        finally:
            with self._lock:
                self._in_flight[symbol] = False

    def _call_openai(self, symbol: str, snap: Dict[str, Any]) -> Optional[GPTVerdict]:
        client = self._get_client()
        if client is None:
            return None
        prompt = (
            "You are a fast scalping co-pilot. Given the snapshot below, "
            "respond with a JSON object only: "
            '{"decision":"BUY"|"SELL"|"HOLD","confidence":0-100,"reason":"<short>"}. '
            "Rules: target 0.3-0.7% moves over 1-3 min. Bias toward action; "
            "only return HOLD if the market is truly motionless. Keep reason <120 chars."
        )
        user_msg = json.dumps({"symbol": symbol, **snap}, default=str)
        try:
            resp = client.chat.completions.create(
                model       = MODEL,
                messages    = [
                    {"role": "system", "content": prompt},
                    {"role": "user",   "content": user_msg},
                ],
                temperature      = 0.2,
                max_tokens       = 80,
                response_format  = {"type": "json_object"},
                timeout          = TIMEOUT_SEC,
            )
        except Exception as e:
            raise RuntimeError(f"openai call failed: {e}")
        try:
            content = resp.choices[0].message.content or "{}"
            data    = json.loads(content)
        except Exception as e:
            raise RuntimeError(f"parse failed: {e}")
        decision = str(data.get("decision", "HOLD")).upper().strip()
        if decision not in ("BUY", "SELL", "HOLD"):
            decision = "HOLD"
        try:
            conf = int(data.get("confidence", 0))
        except Exception:
            conf = 0
        conf   = max(0, min(100, conf))
        reason = str(data.get("reason", ""))[:200]
        return GPTVerdict(symbol=symbol, decision=decision,
                          confidence=conf, reason=reason, ts=time.time())


    # ── public: GLOBAL analyst — SMART AI SCALPING BOT (May 28, 2026) ──────
    # Replaces the old `rank_opportunities` ranker. GPT now sees the full
    # per-symbol snapshot for BTC/ETH/SOL together and returns a single
    # decision:
    #   {action: TRADE|NO_TRADE, symbol, direction, probability, confidence,
    #    risk_level: LOW|MEDIUM|HIGH, reason}
    # Throttled to GPT_MIN_SECONDS_BETWEEN_CALLS (10 s). Cached for the
    # throttle window. Falls back to None on error/disabled so orchestrator
    # can decide its own policy (never hard-block the bot).
    def rank_opportunities(self, candidates: list) -> Optional[Dict[str, Any]]:
        """Backward-compat shim. New consumers should call analyze_global()."""
        return self.analyze_global(candidates)

    def analyze_global(self, payloads: list) -> Optional[Dict[str, Any]]:
        if not self._enabled or not payloads:
            return None
        now = time.time()
        if not hasattr(self, "_last_rank_at"):
            self._last_rank_at = 0.0
            self._rank_cache:  Optional[Dict[str, Any]] = None
            self._rank_key:    str = ""
        # True global throttle (10 s per spec). Within the window we NEVER call
        # the API again. Return last cached decision IF its symbol is still in
        # play (or action was NO_TRADE — still valid).
        if (now - self._last_rank_at) < 10.0:
            if self._rank_cache:
                _csym = self._rank_cache.get("symbol", "NONE")
                if _csym == "NONE" or _csym in {p.get("symbol") for p in payloads}:
                    return self._rank_cache
            return None
        client = self._get_client()
        if client is None:
            return None
        # Set throttle BEFORE call so failures/invalid responses also back off.
        self._last_rank_at = now

        items = [{
            "symbol":     p.get("symbol"),
            "signal":     p.get("signal"),
            "regime":     p.get("regime"),
            "score":      p.get("score"),
            "confidence": p.get("confidence"),
            "price":      p.get("price"),
            "breakdown":  p.get("breakdown") or {},
        } for p in payloads[:3]]

        prompt = (
            "You are an EARLY REVERSAL crypto scalping FILTER.\n"
            "You receive structured market data for BTCUSDT, ETHUSDT, SOLUSDT.\n"
            "You DO NOT predict the future. You evaluate the CURRENT structure\n"
            "and reject low-quality reversal setups.\n\n"
            "Your job:\n"
            " - compare ALL symbols\n"
            " - choose the single best short-term REVERSAL (RSI extreme + "
            "wick rejection + volume spike + MACD hist sign-flip) for the "
            "next 1-3 minutes\n"
            " - reject setups with no edge (chop, no volume, no extreme)\n\n"
            "Return JSON ONLY:\n"
            "{\"action\":\"TRADE|NO_TRADE\","
            "\"symbol\":\"BTCUSDT|ETHUSDT|SOLUSDT|NONE\","
            "\"direction\":\"BUY|SELL|NONE\","
            "\"probability\":0-100,\"confidence\":0-100,"
            "\"risk_level\":\"LOW|MEDIUM|HIGH\","
            "\"reason\":\"short clear reason\"}\n\n"
            "Rules: probability = your confidence in a SHORT-TERM REVERSAL, "
            "NOT a trend prediction. If <55 → NO_TRADE. Reject sideways chop, "
            "no-volume bars, DEAD regime. Keep reason <120 chars."
        )
        try:
            resp = client.chat.completions.create(
                model           = MODEL,
                messages        = [{"role": "system", "content": prompt},
                                   {"role": "user",   "content": json.dumps(items)}],
                temperature     = 0.2,
                max_tokens      = 140,
                response_format = {"type": "json_object"},
                timeout         = TIMEOUT_SEC,
            )
            data       = json.loads(resp.choices[0].message.content or "{}")
            action     = str(data.get("action", "NO_TRADE")).upper().strip()
            symbol     = str(data.get("symbol", "NONE")).upper().strip()
            direction  = str(data.get("direction", "NONE")).upper().strip()
            risk_level = str(data.get("risk_level", "MEDIUM")).upper().strip()
            reason     = str(data.get("reason", ""))[:200]
            try:    prob = int(float(data.get("probability", 0)))
            except (TypeError, ValueError): prob = 0
            try:    conf = int(float(data.get("confidence", 0)))
            except (TypeError, ValueError): conf = 0
            prob = max(0, min(100, prob))
            conf = max(0, min(100, conf))
        except Exception as e:
            with self._lock:
                self._total_errors += 1
                self._last_error    = f"global: {e}"[:200]
            return None

        # Normalize / validate
        if action not in {"TRADE", "NO_TRADE"}:        action = "NO_TRADE"
        if action == "NO_TRADE":
            symbol, direction = "NONE", "NONE"
        else:
            if symbol not in {p.get("symbol") for p in payloads}:
                # GPT made up a symbol — treat as NO_TRADE.
                action, symbol, direction = "NO_TRADE", "NONE", "NONE"
                reason = f"(rejected: unknown symbol) {reason}"[:200]
            if direction not in {"BUY", "SELL"}:
                action, symbol, direction = "NO_TRADE", "NONE", "NONE"
                reason = f"(rejected: bad direction) {reason}"[:200]
        if risk_level not in {"LOW", "MEDIUM", "HIGH"}: risk_level = "MEDIUM"

        out = {
            "action":      action,
            "symbol":      symbol,
            "direction":   direction,
            "probability": prob,
            "confidence":  conf,
            "risk_level":  risk_level,
            "reason":      reason,
            # Keep legacy key so any old caller still works.
            "probability_next_move": prob,
        }
        self._rank_cache = out
        self._rank_key   = "|".join(sorted(f"{p['symbol']}:{p.get('score',0)}"
                                           for p in payloads))
        with self._lock:
            self._total_calls += 1
            self._last_global_ts = now
            self._last_symbol    = f"GLOBAL→{symbol}/{action}"
        return out


# Singleton accessor
_advisor: Optional[GPTAdvisor] = None
_singleton_lock = threading.Lock()


def get_advisor() -> GPTAdvisor:
    global _advisor
    with _singleton_lock:
        if _advisor is None:
            _advisor = GPTAdvisor()
        return _advisor
