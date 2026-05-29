"""One SymbolWorker per (exchange, symbol). LIVE Binance Mainnet only.

Every BUY/SELL goes through Exchange.place_*_order, which talks directly to
api.binance.com. No paper. No testnet. No simulated fills.
"""
from datetime import datetime
from typing import Optional, Dict, List, Callable

from exchanges.base import Exchange
from strategy import get_signal, get_indicators
from risk import RiskManager
from ai_engine import ai_decide


# ── ACTIVE SCALPER MODE — hardcoded post-entry & anti-idle constants ────────
# Per operator spec (FULL RESET). One mode, no profiles.
AS_BE_ARM_PCT          = 0.20    # arm breakeven SL at +0.20%
AS_MAX_RED_AFTER_ENTRY = 2       # exit on 2 consecutive red candles after entry
AS_ANTI_IDLE_MIN       = 5.0     # after 5 min idle → halve threshold
AS_FORCE_AFTER_MIN     = 10.0    # after 10 min idle → quarter threshold + force


class SymbolWorker:
    def __init__(
        self,
        exchange:        Exchange,
        symbol:          str,
        strategy:        str,
        risk_manager:    RiskManager,
        interval:        str   = "1m",
        price_threshold: float = 0.0001,    # 0.01% — ACTIVE SCALPER default
        on_log:          Optional[Callable] = None,
        on_state_update: Optional[Callable] = None,
        on_open_trade:   Optional[Callable] = None,
        on_close_trade:  Optional[Callable] = None,
        on_telegram:     Optional[Callable] = None,
        on_candidate:    Optional[Callable] = None,
        ai_assist:       bool = True,
        ai_aggressiveness: str = "Active Scalper",   # ignored — single mode
        manage_manual_trades: bool = False,          # OFF = never touch manual trades
    ):
        self.exchange   = exchange
        self.symbol     = symbol
        self.strategy   = strategy
        self.risk       = risk_manager
        self.interval   = interval
        self.threshold  = price_threshold
        self._base_threshold = price_threshold     # restored after anti-idle force
        self.ai_assist  = bool(ai_assist)
        # MANUAL TRADES PROTECTION: when False (default) the bot NEVER manages
        # (SL/TP/breakeven/red-candle exit) positions opened manually by the
        # operator. It only ever closes trades it opened itself (type=="bot").
        self.manage_manual_trades = bool(manage_manual_trades)
        # ACTIVE SCALPER MODE is the only profile; accept legacy arg for compat.
        self.ai_aggressiveness = "Active Scalper"

        self._on_log    = on_log    or (lambda *_a, **_kw: None)
        self._on_state  = on_state_update or (lambda *_a, **_kw: None)
        self._on_open   = on_open_trade  or (lambda t: t)
        self._on_close  = on_close_trade or (lambda *_a, **_kw: None)
        self._on_tg     = on_telegram   or (lambda *_a, **_kw: None)
        # SMART PRIORITY SCALPER: if set, tick() does NOT execute orders
        # itself — it computes signal+score and hands the candidate to the
        # orchestrator, which picks the cross-symbol winner. Signature:
        #   on_candidate(eval_dict) -> None
        self._on_candidate: Optional[Callable] = on_candidate
        # Last evaluation snapshot (signal/score/etc) for dashboard read.
        self._last_eval: Dict = {}

        # Per-symbol state
        self._created_at:     datetime = datetime.now()  # for cold-start idle force
        self._last_trade_at:  Optional[datetime] = None
        self._last_trade_dir: Optional[str] = None
        self._session_trades: int = 0
        self._last_block_reason: str = ""
        # Per-trade post-entry exit state — keyed by trade id.
        # Tracks bars seen since entry, consecutive red count, breakeven-armed.
        self._post_entry: Dict[str, Dict] = {}
        # Per-hour trade cap (legacy — kept for back-compat; ACTIVE SCALPER
        # MODE relies on max_per_symbol=1 + cooldown_seconds instead).
        self._recent_trade_times: List[datetime] = []

        # Fees-vs-TP sanity check at construction (Binance Spot ~0.2% round-trip).
        # Warn once if the configured take-profit barely clears fees.
        _tp = float(getattr(self.risk.settings, "take_profit_pct", 0) or 0)
        _sl = float(getattr(self.risk.settings, "stop_loss_pct",   0) or 0)
        if _tp > 0 and _tp < 0.30:
            print(f"[BOT] {self.symbol} ⚠️ TP={_tp:.2f}% barely clears Binance "
                  f"round-trip fees (~0.20%) — consider TP ≥ 0.30%", flush=True)
        if _sl > 0 and _tp > 0 and _tp < _sl:
            print(f"[BOT] {self.symbol} ⚠️ TP={_tp:.2f}% < SL={_sl:.2f}% — "
                  f"negative expectancy risk/reward", flush=True)

    # ── Public API used by orchestrator ──────────────────────────────────────
    def tick(self, all_open_trades: List[Dict],
             global_gate_fn: Callable[[float, str], tuple]) -> None:
        """One iteration of the strategy loop for this symbol."""
        tag = f"[{self.symbol}]"

        # 0. Auth-keys guard. The bot must never crash when credentials
        # disappear (e.g. operator cleared them). Print ONLY on state
        # transition (creds present → gone) to avoid log spam; the UI surfaces
        # the persistent state via block_reason.
        if getattr(self.exchange, "client", None) is None:
            if self._last_block_reason != "Waiting for API keys":
                print(f"[BOT] {self.symbol} Waiting for API keys", flush=True)
                self._log("WARNING", f"{tag} ⏸️ Waiting for API keys")
            self._on_state(self.symbol, block_reason="Waiting for API keys")
            self._last_block_reason = "Waiting for API keys"
            return

        # 1. Price
        try:
            price = self.exchange.get_price(self.symbol)
        except Exception as e:
            self._log("ERROR", f"{tag} price fetch failed: {e}")
            return
        self._on_state(self.symbol, price=price, tick=True)

        # 2. Manage open positions (SL/TP).
        # MANUAL TRADES PROTECTION: by default the bot only manages trades it
        # opened itself (type=="bot"). Manual trades are left untouched — no
        # SL/TP, breakeven, or red-candle exit — unless the operator turns ON
        # "Allow bot to manage manual trades" (manage_manual_trades=True).
        my_open = [
            t for t in all_open_trades
            if t.get("coin") == self.symbol
            and (self.manage_manual_trades or t.get("type") == "bot")
        ]
        # ACTIVE SCALPER hardcoded post-entry behavior (no profile lookup).
        _be_arm_pct   = AS_BE_ARM_PCT
        _max_red      = AS_MAX_RED_AFTER_ENTRY
        for trade in my_open:
            entry = trade["entry_price"]
            side  = trade["side"]
            pnl_now = ((price - entry) / entry * 100) if side == "BUY" \
                      else ((entry - price) / entry * 100)

            # ── PRO BE arm (per-trade, NO global SL mutation) ─────────────
            # Mark the trade as breakeven-armed FIRST so the SL check below
            # uses entry price as the floor for this specific trade only.
            # We never mutate risk.settings.stop_loss_pct — that would leak
            # to every other trade on the symbol.
            if side == "BUY" and _be_arm_pct > 0:
                _tid_be = str(trade.get("id"))
                _pe_be  = self._post_entry.setdefault(_tid_be, {
                    "armed_be": False, "red_count": 0, "last_bar_ts": None,
                })
                if (not _pe_be["armed_be"]) and pnl_now >= _be_arm_pct:
                    _pe_be["armed_be"] = True
                    # Annotate trade for transparency; SL enforcement is done
                    # per-trade below, NOT via risk.settings.
                    try:
                        trade["be_armed"]    = True
                        trade["be_arm_price"] = entry
                        trade["close_reason"] = (trade.get("close_reason") or "") + \
                            f" | BE-armed @ +{pnl_now:.2f}%"
                    except Exception:
                        pass
                    self._log("INFO", f"{tag} 🛡️ Breakeven armed @ +{pnl_now:.2f}% "
                                      f"(trade {_tid_be}) — SL floor now entry "
                                      f"${entry:.4f} (per-trade, no global change)")

            # ── SL/TP checks (per-trade BE overrides global SL for this trade) ──
            if side == "BUY" and self._post_entry.get(str(trade.get("id")), {}) \
                    .get("armed_be") and price <= entry:
                sl_hit, sl_msg = True, (f"Breakeven SL — price ${price:.4f} ≤ "
                                        f"entry ${entry:.4f} (BE armed)")
                tp_hit, tp_msg = self.risk.check_take_profit(entry, price, side)
            else:
                sl_hit, sl_msg = self.risk.check_stop_loss(entry, price, side)
                tp_hit, tp_msg = self.risk.check_take_profit(entry, price, side)

            self._log("INFO",
                f"{tag} 📌 Open {trade['id']} | {side} @ ${entry:.4f} | "
                f"Now ${price:.4f} | Δ {pnl_now:+.2f}%")
            if sl_hit:
                self._log("ORDER", f"{tag} 🔴 STOP LOSS | {trade['id']} | {sl_msg}")
                self._close_position(trade, price, sl_msg)
                return
            if tp_hit:
                self._log("ORDER", f"{tag} 🟢 TAKE PROFIT | {trade['id']} | {tp_msg}")
                self._close_position(trade, price, tp_msg)
                return

        # Stash BUY-side trades for red-candle / EMA9-break checks below
        # (needs df_ind which is fetched after this loop).
        _open_for_post = [t for t in my_open if t["side"] == "BUY"]

        # 3. Klines + signal
        try:
            df = self.exchange.get_klines(self.symbol, self.interval, limit=150)
        except Exception as e:
            self._log("ERROR", f"{tag} klines fetch failed: {e}")
            return
        try:
            df_ind = get_indicators(df)
            self._on_state(self.symbol, df=df_ind)
        except Exception:
            self._on_state(self.symbol, df=df)

        # ── PRO post-entry: red-candle count + EMA9-break exits ───────────
        # Now that we have df_ind, evaluate the deferred post-entry signals.
        if _open_for_post and (_be_arm_pct > 0 or _max_red > 0):
            _df_eval = df_ind if "df_ind" in locals() else df
            try:
                _last_bar = _df_eval.iloc[-1]
                # Bar identity — prefer open_time, fall back to close_time.
                # NEVER use len(df) — fixed-length kline windows don't change
                # length as new candles roll in, so the counter would freeze.
                _bar_ts   = (str(_last_bar.get("open_time", ""))
                             or str(_last_bar.get("close_time", "")))
                _is_red   = float(_last_bar["close"]) < float(_last_bar["open"])
                _ema9_now = float(_last_bar.get("ema9", 0) or 0)
                _close_n  = float(_last_bar["close"])
            except Exception:
                _last_bar = None; _bar_ts = ""; _is_red = False
                _ema9_now = 0.0; _close_n = price
            for _t in _open_for_post:
                _tid = str(_t.get("id"))
                _pe  = self._post_entry.setdefault(_tid, {
                    "armed_be": False, "red_count": 0, "last_bar_ts": None,
                })
                # Only react to NEW closed candle (avoid double-counting on every tick).
                if _bar_ts and _pe["last_bar_ts"] != _bar_ts:
                    _pe["last_bar_ts"] = _bar_ts
                    if _is_red:
                        _pe["red_count"] += 1
                    else:
                        _pe["red_count"] = 0
                    # N-red exit (ACTIVE SCALPER spec — exit on 2 consecutive reds).
                    if _max_red > 0 and _pe["red_count"] >= _max_red:
                        _msg = (f"ACTIVE SCALPER exit — {_pe['red_count']} consecutive "
                                f"red candles after entry (limit {_max_red})")
                        self._log("ORDER", f"{tag} 🔻 {_msg} | {_tid}")
                        self._close_position(_t, _close_n, _msg)
                        return
                # EMA9-break exit REMOVED — ACTIVE SCALPER spec uses ONLY
                # SL/TP + breakeven SL + N-red exit. No trailing EMA9 stop.

        # ── Anti-idle: dynamically lower threshold after idle periods ──────
        # ACTIVE SCALPER spec: "If no trade for 5–10 minutes → automatically
        # lower thresholds (be more aggressive)". Threshold is restored to its
        # base value as soon as a new trade fires (self._last_trade_at update).
        # FINAL STABLE MODE: measure idle from the LAST trade, or — if this
        # worker has never traded — from when it was created, so the 10-min
        # "force activity" rule also fires on a cold start (not only after the
        # first trade).
        _idle_ref  = self._last_trade_at or self._created_at
        _mins_idle = (datetime.now() - _idle_ref).total_seconds() / 60.0
        _force_idle = False
        if _mins_idle >= AS_FORCE_AFTER_MIN:
            self.threshold = self._base_threshold * 0.25   # 0.0025% — forced
            _force_idle = True
        elif _mins_idle >= AS_ANTI_IDLE_MIN:
            self.threshold = self._base_threshold * 0.5    # 0.005%
        else:
            self.threshold = self._base_threshold

        signal, reason, confidence = get_signal(df, self.strategy, threshold=self.threshold)
        if _force_idle and signal == "HOLD":
            # ACTIVE SCALPER spec: after 10+ min idle, actually FORCE an entry
            # attempt using the sign of the most recent candle. Risk + global
            # + balance gates still run below, so this only fires if everything
            # else is healthy.
            try:
                _last_pct = (float(df["close"].iloc[-1]) - float(df["close"].iloc[-2])) \
                            / float(df["close"].iloc[-2]) * 100
            except Exception:
                _last_pct = 0.0
            signal     = "BUY" if _last_pct >= 0 else "SELL"
            confidence = max(confidence, 35)
            reason = (f"⚡ FORCED MICRO-ENTRY {signal} — idle {_mins_idle:.0f} min, "
                      f"last candle {_last_pct:+.4f}%")
        if self.threshold != self._base_threshold:
            reason = f"[idle {_mins_idle:.1f}m, thresh×{self.threshold/self._base_threshold:.2f}] {reason}"
        self._on_state(self.symbol, signal=signal, reason=reason, confidence=confidence)
        # Concise per-symbol decision line (matches user-requested format)
        print(f"[BOT] {self.symbol} signal={signal} reason={reason}", flush=True)
        self._log("SIGNAL", f"{tag} 📊 [{self.strategy}] → {signal} | conf={confidence} | {reason}")

        # 3b. AI Decision Engine — extra decision layer (veto + optional override).
        # Reads RSI/MACD/Volume/EMA/momentum and refuses obvious dumps, pump-tops,
        # and flat markets. Never bypasses risk gates — those still run below.
        if self.ai_assist:
            try:
                _free = 0.0
                try:
                    _b = self.exchange.get_balance("USDT")
                    _free = float(_b.get("free", 0.0))
                except Exception:
                    _free = 0.0
                # Minutes since the worker last took a trade on this symbol —
                # drives the ULTRA / Aggressive "forced micro-entry" path.
                _mins_since = None
                if self._last_trade_at is not None:
                    _mins_since = (datetime.now() - self._last_trade_at).total_seconds() / 60.0
                _ai = ai_decide(
                    df_ind if "df_ind" in locals() else df,
                    strategy_signal       = signal,
                    strategy_reason       = reason,
                    open_positions_for_sym = my_open,
                    free_usdt             = _free,
                    aggressiveness        = self.ai_aggressiveness,
                    minutes_since_last_trade = _mins_since,
                )
                # Always log the AI line — operator wants to see every decision.
                print(f"[AI] {self.symbol} decision={_ai.decision} "
                      f"confidence={_ai.confidence} trend={_ai.trend} "
                      f"blocker={_ai.blocker or '-'} reason={_ai.reason}", flush=True)
                self._log("AI",
                    f"{tag} 🧠 [{self.ai_aggressiveness}] → {_ai.decision} | "
                    f"conf={_ai.confidence} | trend={_ai.trend} | {_ai.reason}")
                # AI overrides the strategy signal (it had it as a strong prior).
                signal, reason, confidence = _ai.decision, _ai.reason, _ai.confidence
                self._on_state(self.symbol, signal=signal, reason=reason,
                               confidence=confidence,
                               ai_decision=_ai.decision,
                               ai_confidence=_ai.confidence,
                               ai_reason=_ai.reason,
                               ai_trend=_ai.trend,
                               ai_signal_strength=_ai.signal_strength,
                               ai_why_bullets=_ai.why_bullets,
                               ai_blocker=_ai.blocker)
            except Exception as _e:
                # AI engine must NEVER break trading. Fall back to strategy signal.
                self._log("WARNING", f"{tag} AI engine error (falling back to "
                                     f"strategy signal): {_e}")

        # 3c. HYBRID MODE — non-blocking GPT advisor (secondary).
        # Consulted ONLY when the rule+AI verdict is weak (conf<60) or HOLD on
        # a moving market. Throttled to 1 call / 10s / symbol, runs in a
        # background thread, never blocks this tick. Reads the LAST cached
        # verdict instantly; the request fires off the refresh for next tick.
        try:
            from gpt_advisor import get_advisor as _get_gpt
            _gpt = _get_gpt()
            _gpt_status = _gpt.status()
            # Moving-market predicate: last 1m candle move ≥ flat_pct.
            # GPT is wasted on a truly motionless tape — skip the call there.
            try:
                _last_pct_abs = abs(
                    (float(df["close"].iloc[-1]) - float(df["close"].iloc[-2]))
                    / float(df["close"].iloc[-2]) * 100
                )
            except Exception:
                _last_pct_abs = 0.0
            _flat_pct = 0.005  # mirrors ai_engine.ACTIVE_SCALPER["flat_pct"]
            _moving = _last_pct_abs >= _flat_pct
            _should_consult = (
                _gpt_status["enabled"]
                and _moving
                and (signal == "HOLD" or confidence < 60)
            )
            if _should_consult:
                _snap = {
                    "price":      float(df["close"].iloc[-1]),
                    "pct_1m":     round((float(df["close"].iloc[-1]) - float(df["close"].iloc[-2]))
                                        / float(df["close"].iloc[-2]) * 100, 4),
                    "ema9":       float(df_ind["ema9"].iloc[-1])  if "df_ind" in locals() and "ema9"  in df_ind.columns else None,
                    "ema21":      float(df_ind["ema21"].iloc[-1]) if "df_ind" in locals() and "ema21" in df_ind.columns else None,
                    "rsi":        float(df_ind["rsi"].iloc[-1])   if "df_ind" in locals() and "rsi"   in df_ind.columns else None,
                    "rule_signal":     signal,
                    "rule_confidence": confidence,
                    "rule_reason":     reason[:160],
                    "open_for_symbol": len(my_open),
                }
                _gpt.maybe_request(self.symbol, _snap)
            _cached = _gpt.get_cached(self.symbol)
            if (_cached
                and _cached.get("decision") in ("BUY", "SELL")
                and int(_cached.get("confidence", 0)) > 60
                and (signal == "HOLD" or confidence < 60)):
                _old_sig = signal
                signal     = _cached["decision"]
                confidence = max(int(confidence), int(_cached["confidence"]))
                reason     = (f"🤖 GPT override ({_old_sig}→{signal}, "
                              f"gpt_conf={_cached['confidence']}) | {_cached.get('reason','')[:120]}")
                print(f"[GPT-OVERRIDE] {self.symbol} {_old_sig}→{signal} "
                      f"conf={_cached['confidence']}", flush=True)
                self._log("AI", f"{tag} 🤖 GPT advisor override → {signal} | {reason}")
                # Republish overridden signal so dashboard matches execution.
                self._on_state(self.symbol, signal=signal, reason=reason,
                               confidence=confidence)
            # Publish GPT state to dashboard regardless of override.
            self._on_state(self.symbol,
                           gpt_decision   = (_cached or {}).get("decision", ""),
                           gpt_confidence = (_cached or {}).get("confidence", 0),
                           gpt_reason     = (_cached or {}).get("reason", ""),
                           gpt_age_sec    = (_cached or {}).get("age_sec", -1),
                           gpt_enabled    = _gpt_status["enabled"],
                           gpt_active     = _gpt_status["active"])
        except Exception as _ge:
            # GPT must NEVER break trading.
            self._log("WARNING", f"{tag} GPT advisor error (ignored): {_ge}")

        # ── SMART PRIORITY SCALPER: compute opportunity score 0–100 ───────
        # Always computed (even on HOLD → score=0) so the dashboard shows it
        # and the orchestrator can rank across symbols.
        try:
            from strategy import score_market as _score_market
            from market_regime import classify_regime as _classify_regime
            _df_for_score = df_ind if "df_ind" in locals() else df
            _regime, _rtele = _classify_regime(_df_for_score)
            _score, _bd     = _score_market(_df_for_score, signal,
                                            confidence, regime=_regime)
            _bd["regime_tele"] = _rtele
        except Exception as _se:
            _score, _bd, _regime = 0, {"error": str(_se)[:80]}, "DEAD"
        self._last_eval = {
            "symbol":     self.symbol,
            "exchange":   self.exchange.name,
            "signal":     signal,
            "reason":     reason,
            "confidence": confidence,
            "score":      _score,
            "regime":     _regime,
            "breakdown":  _bd,
            "price":      price,
            "my_open":    my_open,
            "ts":         datetime.now(),
        }
        self._on_state(self.symbol, score=_score, score_breakdown=_bd,
                       regime=_regime)
        print(f"[SCAN] {self.symbol.replace('USDT','')} score={_score} "
              f"signal={signal} conf={confidence} regime={_regime} "
              f"atr%={_bd.get('atr_pct',0)}", flush=True)

        if signal == "HOLD":
            # Clear block_reason — HOLD is a strategy decision, not a risk gate.
            # The per-symbol overview card surfaces the HOLD reason via `last_reason`.
            self._on_state(self.symbol, block_reason="")
            # Still publish the (empty) candidate so the orchestrator sees
            # this symbol was evaluated; it will be filtered out by signal!=HOLD.
            if self._on_candidate is not None:
                self._on_candidate(self._last_eval)
            return

        # ── SMART PRIORITY SCALPER: defer execution to orchestrator ────────
        # If a candidate callback is wired, we DO NOT place an order here.
        # The orchestrator collects all candidates, picks the highest-scoring
        # one (subject to score_threshold + global throttle + max-open cap),
        # and calls execute_entry() on the winner only.
        if self._on_candidate is not None:
            self._on_candidate(self._last_eval)
            return

        # Fallback: no orchestrator candidate routing — execute inline.
        # Preserves backwards-compat with any caller that uses tick() solo.
        self.execute_entry(self._last_eval, all_open_trades, global_gate_fn)

    # ── Order-placement phase (called by orchestrator on winner only) ─────
    def execute_entry(self, ev: Dict, all_open_trades: List[Dict],
                      global_gate_fn: Callable[[float, str], tuple]) -> bool:
        """Run risk gates, sizing, place LIVE order, record trade.
        Returns True if an order was actually placed."""
        signal     = ev["signal"]
        reason     = ev["reason"]
        confidence = ev["confidence"]
        price      = ev["price"]
        my_open    = ev.get("my_open") or \
                     [t for t in all_open_trades if t.get("coin") == self.symbol]
        tag        = f"[{self.symbol}]"

        if signal not in ("BUY", "SELL"):
            return False

        # 4. Per-symbol gate
        ok, block = self.risk.can_open_trade(
            open_trades_for_symbol = my_open,
            symbol                 = self.symbol,
            new_signal             = signal,
            last_trade_at          = self._last_trade_at,
            last_trade_dir         = self._last_trade_dir,
            session_count          = self._session_trades,
        )
        if not ok:
            print(f"[BOT] {self.symbol} blocked reason={block} (per-symbol gate)", flush=True)
            self._log("INFO", f"{tag} ⏸️ {block}")
            self._on_state(self.symbol, block_reason=block)
            self._last_block_reason = block
            return

        # 5. LIVE balance gate FIRST — sizing depends on free USDT
        try:
            bal = self.exchange.get_balance("USDT")
            free_usdt = float(bal.get("free", 0))
        except Exception as e:
            msg = f"{tag} balance fetch failed: {e}"
            self._log("WARNING", msg)
            self._on_state(self.symbol, block_reason=msg)
            return

        # 6. Sizing — AGGRESSIVE EARLY REVERSAL score-tiered sizing.
        # Spec: smaller per-trade size because we allow MANY concurrent
        # positions (max 20 total, 5 per symbol). 25% reserve preserved.
        #       60–69 → 10% (standard — meets min score)
        #       70–79 → 20% (strong)
        #       ≥ 80  → 30% (excellent)
        # VOLATILE regime → downsize by 25% (reduce size if risk is high).
        # Ceiling at free_usdt * 0.75 keeps 25% reserve. $10 floor = Binance
        # Spot min notional.
        _score  = int(ev.get("score") or 0)
        _conf   = int(ev.get("confidence") or 0)
        _regime = (ev.get("regime") or "").upper()
        # FINAL STABLE MODE — sizing must honor the SIMPLE ENTRY rule: a trade
        # qualifies on EITHER a strong score OR sufficient AI confidence. The
        # score tiers below set conviction-based size; when the score is sub-60
        # but the AI-confidence path qualified the entry (conf >= 30), we still
        # trade at the conservative base size instead of hard-blocking.
        # Path-B floor = the effective threshold the orchestrator used to
        # qualify this winner (anti-idle may have lowered it below 50). Falls
        # back to 50 (MIN_SCORE) for any direct/legacy caller.
        _score_floor = int(ev.get("score_threshold") or 50)
        if   _score >= 80:           _dyn_pct, _tier_lbl = 30.0, "excellent"
        elif _score >= 70:           _dyn_pct, _tier_lbl = 20.0, "strong"
        elif _score >= 60:           _dyn_pct, _tier_lbl = 10.0, "standard"
        elif _score >= _score_floor: _dyn_pct, _tier_lbl = 10.0, "score-min"      # path B
        elif _conf  >= 30:           _dyn_pct, _tier_lbl = 10.0, "ai-confidence"  # path A
        else:                        _dyn_pct, _tier_lbl =  0.0, "below-min"
        if _regime == "VOLATILE" and _dyn_pct > 0:
            _dyn_pct *= 0.75
            _tier_lbl += "-vol"
        print(f"[BOT] {self.symbol} size tier={_tier_lbl} score={_score} "
              f"conf={_conf} regime={_regime} → {_dyn_pct:.1f}% of free USDT",
              flush=True)
        # Block ONLY if neither entry path qualifies (score<50 AND conf<30) —
        # exactly matching the orchestrator's SIMPLE ENTRY rule, so a winner
        # can never be qualified upstream then blocked here.
        if _dyn_pct <= 0:
            msg = (f"score={_score} < {_score_floor} AND conf={_conf} < 30 — "
                   f"neither entry path qualifies")
            print(f"[BOT] {self.symbol} blocked reason={msg} (sizing gate)",
                  flush=True)
            self._on_state(self.symbol, block_reason=msg)
            return
        invested = free_usdt * _dyn_pct / 100.0
        invested = min(invested, free_usdt * 0.75)
        # Hard floor at Binance minimum
        if invested < 10.0:
            msg = (f"sizing ${invested:.2f} < $10 Binance min (free USDT "
                   f"${free_usdt:.2f} too low for {_dyn_pct:.0f}% sizing)")
            print(f"[BOT] {self.symbol} blocked reason={msg} (balance gate)", flush=True)
            self._on_state(self.symbol, block_reason=msg)
            return
        invested = round(invested, 2)

        # 7. Global gate
        ok_g, block_g = global_gate_fn(invested, self.symbol)
        if not ok_g:
            print(f"[BOT] {self.symbol} blocked reason={block_g} (global gate)", flush=True)
            self._log("INFO", f"{tag} ⏸️ {block_g}")
            self._on_state(self.symbol, block_reason=block_g)
            return
        if invested > free_usdt:
            msg = f"invest ${invested:.2f} > free USDT ${free_usdt:.2f} in Binance Spot wallet"
            print(f"[BOT] {self.symbol} blocked reason={msg} (balance gate)", flush=True)
            self._log("WARNING", f"{tag} ⚠️ {msg}")
            self._on_state(self.symbol, block_reason=msg)
            return

        # All gates clear
        self._on_state(self.symbol, block_reason="")

        sl_p = self.risk.stop_loss_price(price, signal)
        tp_p = self.risk.take_profit_price(price, signal)

        # 8. Execute REAL order via Exchange interface
        qty = self.exchange.round_quantity(self.symbol, invested / price)
        print(f"[WORKER {self.symbol}] ORDER REQUEST → {self.exchange.name} LIVE "
              f"{signal} qty={qty} (~${invested:.2f})", flush=True)
        if signal == "BUY":
            resp = self.exchange.place_buy_order(self.symbol, invested)
        else:
            resp = self.exchange.place_sell_order(self.symbol, qty)
        print(f"[WORKER {self.symbol}] ORDER RESPONSE ← {resp}", flush=True)
        if not resp.get("ok"):
            err = resp.get("error", "unknown")
            self._log("ERROR", f"{tag} order failed: {err}")
            self._on_state(self.symbol, block_reason=f"Order failed: {err}",
                           last_order=resp)
            self._on_tg("error_alert", f"Order FAILED — {signal} {self.symbol}\n{err}")
            return
        fill_price = float(resp.get("price") or price)
        qty        = float(resp.get("qty") or qty)
        self._log("ORDER", f"{tag} ✅ LIVE {signal} | {qty} @ ${fill_price:.4f}")
        # Headline log line in the format the operator asked for.
        print(f"[BOT] TRADE EXECUTED {self.symbol} {signal} qty={qty} "
              f"price=${fill_price:.4f} invested=${invested:.2f}", flush=True)
        self._on_state(self.symbol, last_order=resp)

        self._on_tg("trade_open", self.symbol, signal, fill_price, invested, reason, "LIVE")
        self._last_trade_at  = datetime.now()
        self._last_trade_dir = signal
        self._recent_trade_times.append(self._last_trade_at)

        # 9. Record trade
        trade = {
            "coin":            self.symbol,
            "exchange":        self.exchange.name,
            "type":            "bot",
            "strategy":        self.strategy,
            "side":            signal,
            "entry_price":     fill_price,
            "exit_price":      None,
            "quantity":        qty,
            "invested":        invested,
            "profit_loss":     None,
            "profit_loss_pct": None,
            "open_time":       datetime.now().isoformat(),
            "close_time":      None,
            "reason":          reason,
            "close_reason":    None,
            "stop_loss":       sl_p,
            "take_profit":     tp_p,
            "status":          "open",
        }
        added = self._on_open(trade)
        self._session_trades += 1
        self._log("INFO",
            f"{tag} 📝 Trade recorded | ID:{added.get('id') if added else '?'} | "
            f"{signal} | ${invested:.2f} USDT | SL ${sl_p:.4f} | TP ${tp_p:.4f} | "
            f"Session: {self._session_trades}")

    def _close_position(self, trade: Dict, price: float, reason: str) -> None:
        """Close an open position with a REAL counter-order targeting the
        EXACT base qty of the original open.

        Recorded close price comes from the Binance execution (resp['price']).

        Robust close (PRO MODE bugfix for Binance error -2010 / insufficient
        balance): if the first attempt fails with an insufficient-balance
        error, we REFETCH the real asset balance from Binance, round it down
        to the symbol's step size, and retry the sell with the actual
        available quantity. This handles cases where the local record drifts
        from Binance's true free balance (partial fills, manual moves, etc).

        If the exchange-filled qty deviates from the intended qty by more than
        5%, we now SELL the available balance and mark the local trade
        CORRECTED (close_reason annotated) so the dashboard isn't stuck on a
        phantom open.
        """
        intended_qty = float(trade.get("quantity") or 0)
        if intended_qty <= 0:
            self._log("ERROR",
                f"[{self.symbol}] Cannot close trade {trade.get('id')} — zero qty on record.")
            return
        if trade["side"] == "BUY":
            resp = self.exchange.place_sell_order(self.symbol, intended_qty)
        else:
            # SHORT close — BUY exact base qty (NOT quote estimate)
            resp = self.exchange.place_buy_order_qty(self.symbol, intended_qty)

        # ── Close-error retry: refetch real Binance balance & retry ──────
        if (not resp.get("ok")) and trade["side"] == "BUY":
            _err = str(resp.get("error", "")).lower()
            if "insufficient" in _err or "-2010" in _err or "balance" in _err:
                base = self._base_asset(self.symbol)
                try:
                    real = self.exchange.get_balance(base)
                    real_free = float(real.get("free", 0) or 0)
                    real_qty  = self.exchange.round_quantity(self.symbol, real_free)
                    self._log("WARNING",
                        f"[{self.symbol}] Close retry — local qty {intended_qty} "
                        f"rejected (insufficient). Real {base} free={real_free}, "
                        f"rounded={real_qty}. Retrying with available balance.")
                    if real_qty > 0:
                        resp = self.exchange.place_sell_order(self.symbol, real_qty)
                        if resp.get("ok"):
                            trade["close_reason"] = (trade.get("close_reason") or "") + \
                                f" | CORRECTED qty {intended_qty}→{real_qty} (real bal)"
                            intended_qty = real_qty
                    else:
                        # No balance left — this position doesn't actually
                        # exist on Binance. Mark closed at last price so the
                        # local book stops showing a phantom open.
                        self._log("WARNING",
                            f"[{self.symbol}] No {base} balance on Binance — "
                            f"marking phantom trade {trade.get('id')} closed at "
                            f"${price:.4f}.")
                        self._on_close(trade["id"], price,
                            f"{reason} | RECONCILED (no {base} balance on Binance)")
                        return
                except Exception as _re:
                    self._log("ERROR",
                        f"[{self.symbol}] Close retry refetch failed: {_re}")

        if not resp.get("ok"):
            self._log("ERROR", f"[{self.symbol}] Close order failed: {resp.get('error')}")
            return
        exec_price = float(resp.get("price") or 0)
        exec_qty   = float(resp.get("qty") or 0)
        if exec_price <= 0 or exec_qty <= 0:
            self._log("ERROR",
                f"[{self.symbol}] Close response missing execution data — NOT marking trade closed. resp={resp}")
            return
        # Fill-size guard. With the new refetch-and-retry above, residual
        # mismatch usually means partial fill — accept it and annotate.
        deviation = abs(exec_qty - intended_qty) / intended_qty
        if deviation > 0.05:
            self._log("WARNING",
                f"[{self.symbol}] Close fill mismatch accepted — intended "
                f"{intended_qty}, filled {exec_qty} (Δ {deviation*100:.2f}%). "
                f"Marking trade {trade.get('id')} closed (CORRECTED).")
            reason = (reason or "") + f" | CORRECTED qty Δ{deviation*100:.1f}%"
        self._on_close(trade["id"], exec_price, reason)
        # Forget post-entry tracking for this trade.
        self._post_entry.pop(str(trade.get("id")), None)

    @staticmethod
    def _base_asset(symbol: str) -> str:
        """BTCUSDT → BTC. Strips USDT / BUSD / USDC suffix."""
        for q in ("USDT", "BUSD", "USDC"):
            if symbol.upper().endswith(q):
                return symbol[: -len(q)]
        return symbol

    def _log(self, level: str, msg: str) -> None:
        self._on_log(level, msg)

    # ── Diagnostics for dashboard ────────────────────────────────────────────
    def info(self) -> Dict:
        return {
            "symbol":           self.symbol,
            "exchange":         self.exchange.name,
            "strategy":         self.strategy,
            "interval":         self.interval,
            "session_trades":   self._session_trades,
            "last_trade_at":    self._last_trade_at,
            "last_trade_dir":   self._last_trade_dir,
            "invest_per_trade": self.risk.get_invest_amount(),
            "stop_loss_pct":    self.risk.settings.stop_loss_pct,
            "take_profit_pct":  self.risk.settings.take_profit_pct,
        }
