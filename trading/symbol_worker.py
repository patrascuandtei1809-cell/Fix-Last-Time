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


class SymbolWorker:
    def __init__(
        self,
        exchange:        Exchange,
        symbol:          str,
        strategy:        str,
        risk_manager:    RiskManager,
        interval:        str   = "5m",
        price_threshold: float = 0.0003,
        on_log:          Optional[Callable] = None,
        on_state_update: Optional[Callable] = None,
        on_open_trade:   Optional[Callable] = None,
        on_close_trade:  Optional[Callable] = None,
        on_telegram:     Optional[Callable] = None,
        ai_assist:       bool = False,
        ai_aggressiveness: str = "Balanced",
    ):
        self.exchange   = exchange
        self.symbol     = symbol
        self.strategy   = strategy
        self.risk       = risk_manager
        self.interval   = interval
        self.threshold  = price_threshold
        self.ai_assist  = bool(ai_assist)
        self.ai_aggressiveness = ai_aggressiveness or "Balanced"

        self._on_log    = on_log    or (lambda *_a, **_kw: None)
        self._on_state  = on_state_update or (lambda *_a, **_kw: None)
        self._on_open   = on_open_trade  or (lambda t: t)
        self._on_close  = on_close_trade or (lambda *_a, **_kw: None)
        self._on_tg     = on_telegram   or (lambda *_a, **_kw: None)

        # Per-symbol state
        self._last_trade_at:  Optional[datetime] = None
        self._last_trade_dir: Optional[str] = None
        self._session_trades: int = 0
        self._last_block_reason: str = ""
        # Per-trade post-entry exit state — keyed by trade id.
        # Tracks bars seen since entry, consecutive red count, breakeven-armed.
        self._post_entry: Dict[str, Dict] = {}
        # Per-hour trade cap (used by Pro Fast Scalper). Timestamps culled in tick.
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

        # 2. Manage open positions (SL/TP)
        my_open = [t for t in all_open_trades
                   if t.get("type") == "bot" and t.get("coin") == self.symbol]
        # PRO post-entry exit hints (only active when this worker runs a
        # profile that defines them — Pro Fast Scalper).
        try:
            from ai_engine import AGGRESSIVENESS_PROFILES
            _pro_prof = AGGRESSIVENESS_PROFILES.get(self.ai_aggressiveness, {})
        except Exception:
            _pro_prof = {}
        _be_arm_pct   = float(_pro_prof.get("be_arm_pct", 0) or 0)
        _max_red      = int(_pro_prof.get("max_red_after_entry", 0) or 0)
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
                    # N-red exit
                    if _max_red > 0 and _pe["red_count"] >= _max_red:
                        _msg = (f"PRO exit — {_pe['red_count']} consecutive red "
                                f"candles after entry (limit {_max_red})")
                        self._log("ORDER", f"{tag} 🔻 {_msg} | {_tid}")
                        self._close_position(_t, _close_n, _msg)
                        return
                # EMA9-break exit (BUY only) — only after breakeven arm
                # so we don't whipsaw out on first dip. Acts as trailing stop.
                if _pe["armed_be"] and _ema9_now > 0 and _close_n < _ema9_now:
                    _msg = (f"PRO exit — price ${_close_n:.4f} < EMA9 "
                            f"${_ema9_now:.4f} after breakeven arm")
                    self._log("ORDER", f"{tag} 🔻 {_msg} | {_tid}")
                    self._close_position(_t, _close_n, _msg)
                    return

        signal, reason, confidence = get_signal(df, self.strategy, threshold=self.threshold)
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

        if signal == "HOLD":
            # Clear block_reason — HOLD is a strategy decision, not a risk gate.
            # The per-symbol overview card surfaces the HOLD reason via `last_reason`.
            self._on_state(self.symbol, block_reason="")
            return

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

        # 4b. PRO per-symbol per-hour cap (Pro Fast Scalper).
        _max_per_hr = int(_pro_prof.get("max_trades_per_hour", 0) or 0)
        if _max_per_hr > 0:
            _cutoff = datetime.now()
            self._recent_trade_times = [t for t in self._recent_trade_times
                                        if (_cutoff - t).total_seconds() < 3600]
            if len(self._recent_trade_times) >= _max_per_hr:
                _msg = (f"PRO hourly cap — {len(self._recent_trade_times)}/"
                        f"{_max_per_hr} trades on {self.symbol} in last 60 min")
                print(f"[BOT] {self.symbol} blocked reason={_msg} (per-hour cap)", flush=True)
                self._log("INFO", f"{tag} ⏸️ {_msg}")
                self._on_state(self.symbol, block_reason=_msg)
                return

        # 5. Sizing
        invested = self.risk.get_invest_amount()
        if invested < 10:
            msg = f"{tag} ⚠️ invest_per_trade ${invested:.2f} < $10 minimum"
            print(f"[WORKER {self.symbol}] BLOCKED (sizing) {msg}", flush=True)
            self._log("WARNING", msg)
            self._on_state(self.symbol, block_reason=msg)
            return

        # 6. Global gate
        ok_g, block_g = global_gate_fn(invested, self.symbol)
        if not ok_g:
            print(f"[BOT] {self.symbol} blocked reason={block_g} (global gate)", flush=True)
            self._log("INFO", f"{tag} ⏸️ {block_g}")
            self._on_state(self.symbol, block_reason=block_g)
            return

        # 7. LIVE balance gate — always
        try:
            bal = self.exchange.get_balance("USDT")
            free_usdt = float(bal.get("free", 0))
        except Exception as e:
            msg = f"{tag} balance fetch failed: {e}"
            self._log("WARNING", msg)
            self._on_state(self.symbol, block_reason=msg)
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
