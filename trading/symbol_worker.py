"""One SymbolWorker per (exchange, symbol). LIVE Binance Mainnet only.

Every BUY/SELL goes through Exchange.place_*_order, which talks directly to
api.binance.com. No paper. No testnet. No simulated fills.
"""
from datetime import datetime
from typing import Optional, Dict, List, Callable

from exchanges.base import Exchange
from strategy import get_signal, get_indicators
from risk import RiskManager


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
    ):
        self.exchange   = exchange
        self.symbol     = symbol
        self.strategy   = strategy
        self.risk       = risk_manager
        self.interval   = interval
        self.threshold  = price_threshold

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

    # ── Public API used by orchestrator ──────────────────────────────────────
    def tick(self, all_open_trades: List[Dict],
             global_gate_fn: Callable[[float, str], tuple]) -> None:
        """One iteration of the strategy loop for this symbol."""
        tag = f"[{self.symbol}]"

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
        for trade in my_open:
            entry = trade["entry_price"]
            side  = trade["side"]
            sl_hit, sl_msg = self.risk.check_stop_loss(entry, price, side)
            tp_hit, tp_msg = self.risk.check_take_profit(entry, price, side)
            pnl_now = ((price - entry) / entry * 100) if side == "BUY" \
                      else ((entry - price) / entry * 100)
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

        signal, reason, confidence = get_signal(df, self.strategy, threshold=self.threshold)
        self._on_state(self.symbol, signal=signal, reason=reason, confidence=confidence)
        # Concise per-symbol decision line (matches user-requested format)
        print(f"[BOT] {self.symbol} signal={signal} reason={reason}", flush=True)
        self._log("SIGNAL", f"{tag} 📊 [{self.strategy}] → {signal} | conf={confidence} | {reason}")

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
        self._on_state(self.symbol, last_order=resp)

        self._on_tg("trade_open", self.symbol, signal, fill_price, invested, reason, "LIVE")
        self._last_trade_at  = datetime.now()
        self._last_trade_dir = signal

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
        If the exchange-filled qty deviates from the intended qty by more than
        5%, we LOG ERROR and refuse to mark the trade closed — the trade
        stays open so the operator can reconcile residual position manually.
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
        if not resp.get("ok"):
            self._log("ERROR", f"[{self.symbol}] Close order failed: {resp.get('error')}")
            return
        exec_price = float(resp.get("price") or 0)
        exec_qty   = float(resp.get("qty") or 0)
        if exec_price <= 0 or exec_qty <= 0:
            self._log("ERROR",
                f"[{self.symbol}] Close response missing execution data — NOT marking trade closed. resp={resp}")
            return
        # Fill-size guard: refuse to close if Binance filled materially less/more
        deviation = abs(exec_qty - intended_qty) / intended_qty
        if deviation > 0.05:
            self._log("ERROR",
                f"[{self.symbol}] Close fill size mismatch — intended {intended_qty}, "
                f"filled {exec_qty} (Δ {deviation*100:.2f}%). Trade {trade.get('id')} "
                f"left OPEN for manual reconciliation.")
            return
        self._on_close(trade["id"], exec_price, reason)

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
