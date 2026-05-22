"""One SymbolWorker per (exchange, symbol).

Encapsulates everything that used to live inside the single TradingBot._tick:
  • pulls price + klines from the assigned Exchange
  • computes signal via strategy module
  • manages open positions (SL / TP)
  • opens new trades through the Exchange interface
  • per-symbol risk gating (RiskManager)

The bot orchestrator calls `worker.tick()` for each enabled worker on every
loop iteration. Workers share NO state with each other — they each own their
own risk counters, last-trade timestamp, session counter, etc.
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
        paper_mode:      bool  = True,
        price_threshold: float = 0.0003,
        # Callbacks injected by the orchestrator — keeps this module
        # independent of bot.py's persistence/logging globals.
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
        self.paper      = paper_mode
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
        """One iteration of the strategy loop for this symbol.

        global_gate_fn(invest, symbol) → (ok: bool, reason: str)
        Called BEFORE order submission to enforce cross-symbol caps.
        """
        tag = f"[{self.symbol}]"

        # 1. Price ─────────────────────────────────────────────────────────────
        try:
            price = self.exchange.get_price(self.symbol)
        except Exception as e:
            self._log("ERROR", f"{tag} price fetch failed: {e}")
            return
        self._on_state(self.symbol, price=price, tick=True)

        # 2. Manage open positions (SL/TP) ─────────────────────────────────────
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

        # 3. Klines + signal ───────────────────────────────────────────────────
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
        self._log("SIGNAL", f"{tag} 📊 [{self.strategy}] → {signal} | conf={confidence} | {reason}")

        if signal == "HOLD":
            self._on_state(self.symbol, block_reason="")
            return

        # 4. Per-symbol gate ──────────────────────────────────────────────────
        ok, block = self.risk.can_open_trade(
            open_trades_for_symbol = my_open,
            symbol                 = self.symbol,
            new_signal             = signal,
            last_trade_at          = self._last_trade_at,
            last_trade_dir         = self._last_trade_dir,
            session_count          = self._session_trades,
        )
        if not ok:
            print(f"[WORKER {self.symbol}] BLOCKED (per-symbol) {block}", flush=True)
            self._log("INFO", f"{tag} ⏸️ {block}")
            self._on_state(self.symbol, block_reason=block)
            self._last_block_reason = block
            return

        # 5. Sizing ───────────────────────────────────────────────────────────
        invested = self.risk.get_invest_amount()
        if invested < 10:
            msg = f"{tag} ⚠️ invest_per_trade ${invested:.2f} < $10 minimum"
            print(f"[WORKER {self.symbol}] BLOCKED (sizing) {msg}", flush=True)
            self._log("WARNING", msg)
            self._on_state(self.symbol, block_reason=msg)
            return

        # 6. Global gate ──────────────────────────────────────────────────────
        ok_g, block_g = global_gate_fn(invested, self.symbol)
        if not ok_g:
            print(f"[WORKER {self.symbol}] BLOCKED (global) {block_g}", flush=True)
            self._log("INFO", f"{tag} ⏸️ {block_g}")
            self._on_state(self.symbol, block_reason=block_g)
            return

        # 7. Live balance gate ────────────────────────────────────────────────
        if not self.paper:
            try:
                bal = self.exchange.get_balance("USDT")
                free_usdt = float(bal.get("free", 0))
            except Exception as e:
                msg = f"{tag} balance fetch failed: {e}"
                self._log("WARNING", msg)
                self._on_state(self.symbol, block_reason=msg)
                return
            if invested > free_usdt:
                msg = (f"{tag} ⚠️ invest ${invested:.2f} > free USDT ${free_usdt:.2f}")
                print(f"[WORKER {self.symbol}] BLOCKED (balance) {msg}", flush=True)
                self._log("WARNING", msg)
                self._on_state(self.symbol, block_reason=msg)
                return

        # All gates clear
        self._on_state(self.symbol, block_reason="")

        sl_p = self.risk.stop_loss_price(price, signal)
        tp_p = self.risk.take_profit_price(price, signal)
        mode_tag = "PAPER" if self.paper else ("TESTNET" if self.exchange.testnet else "LIVE")

        # 8. Execute via Exchange interface ───────────────────────────────────
        fill_price = price
        qty        = self.exchange.round_quantity(self.symbol, invested / price)

        if not self.paper:
            print(f"[WORKER {self.symbol}] ORDER REQUEST → {self.exchange.name} {mode_tag} "
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
            self._log("ORDER", f"{tag} ✅ {mode_tag} {signal} | {qty} @ ${fill_price:.4f}")
            self._on_state(self.symbol, last_order=resp)
        else:
            self._log("ORDER",
                f"{tag} 📋 PAPER {signal} | {qty:.6f} @ ${price:.4f} | "
                f"${invested:.2f} invested | SL ${sl_p:.4f} | TP ${tp_p:.4f}")
            self._on_state(self.symbol, last_order={
                "ok": True, "exchange": self.exchange.name, "side": signal,
                "qty": qty, "symbol": self.symbol, "price": price, "mode": mode_tag,
            })

        self._on_tg("trade_open", self.symbol, signal, fill_price, invested, reason, mode_tag)
        self._last_trade_at  = datetime.now()
        self._last_trade_dir = signal

        # 9. Record trade ─────────────────────────────────────────────────────
        trade = {
            "coin":            self.symbol,
            "exchange":        f"{self.exchange.name}{' (testnet)' if self.exchange.testnet else ''}"
                               if not self.paper else "Paper (public data)",
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
            "paper":           self.paper,
        }
        added = self._on_open(trade)
        self._session_trades += 1
        self._log("INFO",
            f"{tag} 📝 Trade recorded | ID:{added.get('id') if added else '?'} | "
            f"{signal} | ${invested:.2f} USDT | SL ${sl_p:.4f} | TP ${tp_p:.4f} | "
            f"Session: {self._session_trades}")

    def _close_position(self, trade: Dict, price: float, reason: str) -> None:
        """Close an open position. Mirrors old TradingBot._close."""
        if not self.paper:
            qty = trade.get("quantity", 0)
            if trade["side"] == "BUY":
                resp = self.exchange.place_sell_order(self.symbol, qty)
            else:
                resp = self.exchange.place_buy_order(self.symbol, qty * price)
            if not resp.get("ok"):
                self._log("ERROR", f"[{self.symbol}] Close order failed: {resp.get('error')}")
                return
        self._on_close(trade["id"], price, reason)

    def _log(self, level: str, msg: str) -> None:
        self._on_log(level, msg)

    # ── Diagnostics for dashboard ────────────────────────────────────────────
    def info(self) -> Dict:
        return {
            "symbol":           self.symbol,
            "exchange":         self.exchange.name,
            "strategy":         self.strategy,
            "interval":         self.interval,
            "paper":            self.paper,
            "session_trades":   self._session_trades,
            "last_trade_at":    self._last_trade_at,
            "last_trade_dir":   self._last_trade_dir,
            "invest_per_trade": self.risk.get_invest_amount(),
            "stop_loss_pct":    self.risk.settings.stop_loss_pct,
            "take_profit_pct":  self.risk.settings.take_profit_pct,
        }
