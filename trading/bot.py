"""
TradingBot — background daemon thread, survives Streamlit reruns.

Key design:
  - Module-level global `_bot` is the singleton. Streamlit imports this module
    once per process; the global persists across script reruns.
  - Supports authenticated BinanceClient OR public-API-only (paper mode, no key).
  - One thread, no duplicates. start() is a no-op if the thread is alive.
  - All state (trades, activity) written to JSON on disk so it survives restarts.
"""

import threading
import time
import json
import os
import uuid
from datetime import datetime
from typing import Optional, List, Dict

from strategy import get_signal
from risk import RiskManager, RiskSettings
from binance_client import public_klines, public_price
import telegram_notifier as tg

# ── Singleton ─────────────────────────────────────────────────────────────────
_bot: Optional["TradingBot"] = None
_bot_lock = threading.Lock()

# ── Shared live state (bot writes → dashboard reads every rerun) ───────────────
_state_lock = threading.Lock()
_shared: Dict = {
    "df":           None,   # pd.DataFrame with indicators
    "price":        None,   # float
    "updated_at":   None,   # datetime
    "last_signal":  None,   # str  "BUY"/"SELL"/"HOLD"
    "last_reason":  None,   # str
    "last_confidence": 0,   # int  0..100
}

def get_shared_df():
    with _state_lock:
        return _shared.get("df")

def get_shared_price():
    with _state_lock:
        return _shared.get("price")

def get_shared_updated_at():
    with _state_lock:
        return _shared.get("updated_at")

def _set_shared(df=None, price=None, tick=False, signal=None, reason=None,
                confidence=None, block_reason=None, last_order=None):
    with _state_lock:
        if df         is not None: _shared["df"]    = df
        if price      is not None: _shared["price"] = price
        if signal     is not None: _shared["last_signal"]     = signal
        if reason     is not None: _shared["last_reason"]     = reason
        if confidence is not None: _shared["last_confidence"] = int(confidence)
        if block_reason is not None:
            _shared["block_reason"]    = block_reason
            _shared["block_reason_at"] = datetime.now()
        if last_order is not None:
            _shared["last_order"]    = last_order
            _shared["last_order_at"] = datetime.now()
        _shared["updated_at"] = datetime.now()
        if tick: _shared["last_tick_at"] = datetime.now()


def get_bot_diagnostics() -> dict:
    """All bot decision/exec state for the dashboard to display."""
    with _state_lock:
        return {
            "block_reason":    _shared.get("block_reason"),
            "block_reason_at": _shared.get("block_reason_at"),
            "last_order":      _shared.get("last_order"),
            "last_order_at":   _shared.get("last_order_at"),
        }


def get_bot_signal_meta() -> dict:
    """Latest structured signal info from the bot (signal/reason/confidence)."""
    with _state_lock:
        return {
            "signal":     _shared.get("last_signal"),
            "reason":     _shared.get("last_reason"),
            "confidence": int(_shared.get("last_confidence") or 0),
        }

def get_shared_last_tick():
    with _state_lock:
        return _shared.get("last_tick_at")

# ── Data paths ────────────────────────────────────────────────────────────────
_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

TRADES_FILE   = os.path.join(DATA_DIR, "trades.json")
ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
_file_lock    = threading.Lock()
_settings_lock = threading.Lock()
MAX_ACTIVITY  = 500


# ── Settings persistence (survives restarts) ──────────────────────────────────
def load_settings() -> dict:
    """Load persisted user settings from disk. Returns {} if no file."""
    with _settings_lock:
        if not os.path.exists(SETTINGS_FILE):
            return {}
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f) or {}
        except Exception as e:
            print(f"[SETTINGS] load failed: {e}", flush=True)
            return {}


def save_settings(data: dict) -> bool:
    """Persist a dict of user settings to disk. Returns True on success."""
    with _settings_lock:
        try:
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, SETTINGS_FILE)
            print(f"[SETTINGS] saved {len(data)} keys to {SETTINGS_FILE}", flush=True)
            return True
        except Exception as e:
            print(f"[SETTINGS] save failed: {e}", flush=True)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_trades() -> List[Dict]:
    with _file_lock:
        if not os.path.exists(TRADES_FILE):
            return []
        try:
            with open(TRADES_FILE) as f:
                return json.load(f)
        except Exception:
            return []


def save_trades(trades: List[Dict]):
    with _file_lock:
        with open(TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2, default=str)


def load_activity() -> List[Dict]:
    with _file_lock:
        if not os.path.exists(ACTIVITY_FILE):
            return []
        try:
            with open(ACTIVITY_FILE) as f:
                return json.load(f)[-MAX_ACTIVITY:]
        except Exception:
            return []


def _append_activity(entry: Dict):
    with _file_lock:
        data: List[Dict] = []
        if os.path.exists(ACTIVITY_FILE):
            try:
                with open(ACTIVITY_FILE) as f:
                    data = json.load(f)
            except Exception:
                pass
        data.append(entry)
        data = data[-MAX_ACTIVITY:]
        with open(ACTIVITY_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)


def log_activity(level: str, message: str):
    _append_activity({
        "time":    datetime.now().isoformat(),
        "level":   level,
        "message": message,
    })


def clear_activity():
    with _file_lock:
        with open(ACTIVITY_FILE, "w") as f:
            json.dump([], f)


def get_open_trades() -> List[Dict]:
    return [t for t in load_trades() if t.get("status") == "open"]


def add_trade(trade: Dict) -> Dict:
    trades = load_trades()
    trade["id"] = str(uuid.uuid4())[:8]
    trades.append(trade)
    save_trades(trades)
    return trade


def close_trade(trade_id: str, exit_price: float, reason: str) -> Optional[Dict]:
    trades = load_trades()
    for t in trades:
        if t.get("id") == trade_id and t.get("status") == "open":
            invested = t.get("invested") or 0
            entry    = t["entry_price"]
            side     = t["side"]
            if side == "BUY":
                t["profit_loss"]     = (exit_price - entry) / entry * invested
                t["profit_loss_pct"] = (exit_price - entry) / entry * 100
            else:
                t["profit_loss"]     = (entry - exit_price) / entry * invested
                t["profit_loss_pct"] = (entry - exit_price) / entry * 100
            t["exit_price"]  = exit_price
            t["close_time"]  = datetime.now().isoformat()
            t["close_reason"] = reason
            t["status"]      = "closed"
            save_trades(trades)
            return t
    return None


def reset_all_data():
    with _file_lock:
        for fpath in [TRADES_FILE, ACTIVITY_FILE]:
            if os.path.exists(fpath):
                os.remove(fpath)


# ─────────────────────────────────────────────────────────────────────────────
# TradingBot
# ─────────────────────────────────────────────────────────────────────────────

class TradingBot:
    """
    Parameters
    ----------
    client : BinanceClient | None
        Authenticated client. If None the bot runs in paper-only mode using
        the public Binance REST API for market data. No API key required.
    symbol, strategy, risk_manager, interval, check_every, paper_mode, threshold
        Self-explanatory trading parameters.
    """

    def __init__(
        self,
        client,                          # BinanceClient or None
        symbol:          str,
        strategy:        str,
        risk_manager:    RiskManager,
        interval:        str   = "5m",
        check_every:     int   = 30,
        paper_mode:      bool  = True,
        price_threshold: float = 0.0003,
    ):
        self.client      = client
        self.symbol      = symbol
        self.strategy    = strategy
        self.risk        = risk_manager
        self.interval    = interval
        self.check_every = check_every
        self.paper       = paper_mode
        self.threshold   = price_threshold

        # If no authenticated client, force paper mode
        if self.client is None:
            self.paper = True

        self._thread:         Optional[threading.Thread] = None
        self._running:        bool = False
        self._session_trades: int  = 0   # trades opened this session
        self._last_trade_at:  Optional[datetime] = None
        self._last_trade_dir: Optional[str] = None
        self._initial_balance: float = 1000.0   # used for daily-loss % calc; updated externally

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            log_activity("WARNING", "⚠️ Bot already running — ignoring duplicate start")
            return False
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"alphatrade-{self.symbol}",
        )
        self._thread.start()
        auth_label = "auth client" if self.client else "public API (no key)"
        mode_label = "PAPER" if self.paper else "LIVE"
        _invest = self.risk.get_invest_amount()
        _cap    = self.risk.settings.max_trade_usdt
        _msess  = self.risk.settings.max_trades_per_session
        _sess_label = f"max {_msess}/session" if _msess > 0 else "unlimited/session"
        log_activity(
            "INFO",
            f"🚀 Bot started | {self.symbol} | {self.strategy} | {mode_label} | "
            f"Data: {auth_label} | interval={self.interval} | check={self.check_every}s | "
            f"Invest ${_invest:.2f} USDT/trade (cap ${_cap:.2f}) | {_sess_label} | "
            f"SL={self.risk.settings.stop_loss_pct}% | TP={self.risk.settings.take_profit_pct}%",
        )
        tg.bot_event(
            "started",
            f"{self.symbol} | {self.strategy} | {mode_label}\n"
            f"Invest ${_invest:.2f} USDT/trade | {_sess_label}",
        )
        return True

    def stop(self):
        self._running = False
        log_activity("INFO", f"⛔ Bot stopping for {self.symbol}")
        tg.bot_event("stopped", self.symbol)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._running)

    def update_settings(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        log_activity("INFO", f"📡 Bot thread alive — ticking every {self.check_every}s")
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                log_activity("ERROR", f"Unhandled bot error: {exc}")
                tg.error_alert(f"Unhandled bot error on {self.symbol}: {exc}")
            # Interruptible sleep — checks _running every second
            for _ in range(self.check_every):
                if not self._running:
                    break
                time.sleep(1)
        log_activity("INFO", f"🛑 Bot thread exited for {self.symbol}")

    # ── Single tick ───────────────────────────────────────────────────────────

    def _tick(self):
        # Emergency stop guard
        if self.risk.settings.emergency_stop:
            log_activity("WARNING", "🚨 Emergency stop active — tick skipped")
            return

        # ── 1. Get current price ──────────────────────────────────────────────
        price = self._get_price()
        if price is None:
            return
        _set_shared(price=price, tick=True)
        log_activity("INFO", f"💰 {self.symbol} = ${price:,.4f}")

        # ── 2. Manage open positions (SL / TP) ───────────────────────────────
        open_trades  = get_open_trades()
        my_open      = [t for t in open_trades
                        if t.get("type") == "bot" and t.get("coin") == self.symbol]

        for trade in my_open:
            entry = trade["entry_price"]
            side  = trade["side"]

            sl_hit, sl_msg = self.risk.check_stop_loss(entry, price, side)
            tp_hit, tp_msg = self.risk.check_take_profit(entry, price, side)

            pnl_now = (price - entry) / entry * 100 if side == "BUY" else (entry - price) / entry * 100

            log_activity(
                "INFO",
                f"📌 Open {trade['id']} | {side} @ ${entry:.4f} | "
                f"Now ${price:.4f} | Δ {pnl_now:+.2f}% | "
                f"SL ${self.risk.stop_loss_price(entry, side):.4f} | "
                f"TP ${self.risk.take_profit_price(entry, side):.4f}",
            )

            if sl_hit:
                log_activity("ORDER", f"🔴 STOP LOSS | {trade['id']} | {sl_msg}")
                self._close(trade, price, sl_msg)
                return
            if tp_hit:
                log_activity("ORDER", f"🟢 TAKE PROFIT | {trade['id']} | {tp_msg}")
                self._close(trade, price, tp_msg)
                return

        # ── 3. Compute signal from klines (BEFORE gating, so we can show it) ──
        df = self._get_klines()
        if df is None:
            return

        # Push live klines (with indicators) to shared state for dashboard
        try:
            from strategy import get_indicators as _gi
            _set_shared(df=_gi(df))
        except Exception:
            _set_shared(df=df)

        signal, reason, confidence = get_signal(df, self.strategy, threshold=self.threshold)
        _set_shared(signal=signal, reason=reason, confidence=confidence)
        log_activity("SIGNAL", f"📊 [{self.strategy}] → {signal} | conf={confidence} | {reason}")

        if signal == "HOLD":
            log_activity("INFO", "⏭️ HOLD — no trade opened")
            return

        # ── 4. Daily loss circuit breaker + gate ──────────────────────────────
        # Compute today's realized PnL % from closed bot trades
        _today = datetime.now().strftime("%Y-%m-%d")
        _today_pnl = sum(
            (t.get("profit_loss") or 0)
            for t in load_trades()
            if t.get("type") == "bot" and t.get("status") == "closed"
            and (t.get("close_time") or "").startswith(_today)
        )
        _daily_pct = (_today_pnl / self._initial_balance * 100) if self._initial_balance else 0.0

        can_trade, block_reason = self.risk.can_open_trade(
            open_trades    = open_trades,
            symbol         = self.symbol,
            new_signal     = signal,
            last_trade_at  = self._last_trade_at,
            last_trade_dir = self._last_trade_dir,
            daily_loss_pct = _daily_pct,
            session_count  = self._session_trades,
        )
        if not can_trade:
            print(f"[BOT] BLOCKED signal={signal} reason={block_reason}", flush=True)
            log_activity("INFO", f"⏸️ {block_reason}")
            _set_shared(block_reason=block_reason)
            # Auto-stop on daily-loss breaker
            if "Daily loss limit" in block_reason:
                log_activity("WARNING", "🛑 Auto-stopping bot — daily loss limit hit")
                tg.bot_event("auto-stopped", f"Daily loss {_daily_pct:+.2f}% ≥ "
                                             f"{self.risk.settings.max_daily_loss_pct}%")
                self._running = False
            return
        # Clear stale block reason when trade is allowed
        _set_shared(block_reason="")

        # ── 5. Size the position (FIXED USDT — never uses full balance) ─────────
        invested = self.risk.get_invest_amount()
        # For LIVE balance gate we need FREE USDT (locked funds cannot be spent).
        free_usdt = None
        if not self.paper and self.client:
            try:
                _bd = self.client.get_account_balance("USDT")
                free_usdt = float(_bd["free"]) if isinstance(_bd, dict) else float(_bd)
            except Exception as e:
                print(f"[BOT][ERROR] free balance fetch failed: {e}", flush=True)
                log_activity("WARNING", f"Balance fetch failed: {e}")
        bal_display = free_usdt if free_usdt is not None else self._get_balance()
        print(f"[BOT] SIZING signal={signal} price={price:.6f} invest_per_trade=${invested:.2f} "
              f"free_usdt={free_usdt} balance=${bal_display:.2f} paper={self.paper}", flush=True)

        if invested < 10:
            msg = (f"⚠️ Skipping — invest_per_trade ${invested:.2f} < $10 min. "
                   f"Raise it in Risk → Invest per trade.")
            print(f"[BOT] BLOCKED reason={msg}", flush=True)
            log_activity("WARNING", msg)
            _set_shared(block_reason=msg)
            return
        if not self.paper and free_usdt is not None and invested > free_usdt:
            msg = (f"⚠️ Skipping — invest_per_trade ${invested:.2f} > free USDT "
                   f"${free_usdt:.2f} (locked funds excluded). Top up or lower invest size.")
            print(f"[BOT] BLOCKED reason={msg}", flush=True)
            log_activity("WARNING", msg)
            _set_shared(block_reason=msg)
            return

        qty = self._round_qty(invested / price)
        sl_p    = self.risk.stop_loss_price(price, signal)
        tp_p    = self.risk.take_profit_price(price, signal)

        # ── 6. Execute ────────────────────────────────────────────────────────
        _mode_tag = "PAPER" if self.paper else ("TESTNET" if (self.client and self.client.testnet) else "LIVE")
        if not self.paper and self.client:
            print(f"[BOT] ORDER REQUEST → Binance {_mode_tag} | "
                  f"{signal} {qty} {self.symbol} (market)", flush=True)
            try:
                order      = self.client.place_market_order(self.symbol, signal, qty)
                print(f"[BOT] ORDER RESPONSE ← {order}", flush=True)
                fill_price = float(order.get("fills", [{}])[0].get("price", price))
                price      = fill_price
                log_activity("ORDER",
                    f"✅ LIVE {signal} | {qty} {self.symbol} @ ${price:.4f}")
                _set_shared(last_order={
                    "ok": True, "side": signal, "qty": qty,
                    "symbol": self.symbol, "price": price, "mode": _mode_tag,
                })
            except Exception as e:
                err = f"Order failed: {e}"
                print(f"[BOT][ERROR] {err}", flush=True)
                log_activity("ERROR", err)
                _set_shared(block_reason=err, last_order={
                    "ok": False, "side": signal, "qty": qty,
                    "symbol": self.symbol, "error": str(e), "mode": _mode_tag,
                })
                tg.error_alert(f"Order FAILED — {signal} {self.symbol}\n{e}")
                return
        else:
            log_activity(
                "ORDER",
                f"📋 PAPER {signal} | {qty:.6f} {self.symbol} @ ${price:.4f} | "
                f"${invested:.2f} invested | SL ${sl_p:.4f} | TP ${tp_p:.4f}",
            )
        tg.trade_open(self.symbol, signal, price, invested, reason, mode=_mode_tag)
        # Update trade-control state (session_trades is incremented after add_trade below)
        self._last_trade_at  = datetime.now()
        self._last_trade_dir = signal

        # ── 7. Record trade ───────────────────────────────────────────────────
        trade = {
            "coin":            self.symbol,
            "exchange":        "Binance Testnet" if (self.client and self.client.testnet) else
                               ("Binance Live"   if self.client else "Paper (public data)"),
            "type":            "bot",
            "strategy":        self.strategy,
            "side":            signal,
            "entry_price":     price,
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
        added = add_trade(trade)
        self._session_trades += 1
        _max_sess = self.risk.settings.max_trades_per_session
        _sess_str = f"{self._session_trades}/{_max_sess}" if _max_sess > 0 else f"{self._session_trades}/∞"
        log_activity("INFO",
            f"📝 Trade recorded | ID:{added['id']} | {signal} {self.symbol} | "
            f"${invested:.2f} USDT | SL ${sl_p:.4f} | TP ${tp_p:.4f} | "
            f"Session trades: {_sess_str}")

    # ── Close helper ──────────────────────────────────────────────────────────

    def _close(self, trade: Dict, price: float, reason: str):
        if not self.paper and self.client:
            opposite = "SELL" if trade["side"] == "BUY" else "BUY"
            try:
                self.client.place_market_order(self.symbol, opposite, trade["quantity"])
            except Exception as e:
                log_activity("ERROR", f"Close order failed: {e}")
                return

        closed = close_trade(trade["id"], price, reason)
        if closed:
            pnl  = closed.get("profit_loss") or 0
            pct  = closed.get("profit_loss_pct") or 0
            icon = "🟢" if pnl >= 0 else "🔴"
            log_activity(
                "ORDER",
                f"{icon} Closed {closed['id']} | P&L ${pnl:+.4f} ({pct:+.2f}%) | {reason}",
            )
            _cmode = "PAPER" if trade.get("paper") else ("TESTNET" if (self.client and self.client.testnet) else "LIVE")
            tg.trade_close(
                symbol     = trade["coin"],
                side       = trade["side"],
                entry      = trade["entry_price"],
                exit_price = price,
                pnl        = pnl,
                pct        = pct,
                reason     = reason,
                mode       = _cmode,
            )

    # ── Data helpers ──────────────────────────────────────────────────────────

    def _get_price(self) -> Optional[float]:
        # Prefer authenticated client (testnet might differ from mainnet)
        if self.client:
            try:
                return self.client.get_symbol_price(self.symbol)
            except Exception as e:
                log_activity("WARNING", f"Auth price failed, falling back to public: {e}")

        # Public API — no key needed
        try:
            return public_price(self.symbol, testnet=False)
        except Exception as e:
            log_activity("ERROR", f"Public price fetch failed: {e}")
            return None

    def _get_klines(self):
        if self.client:
            try:
                return self.client.get_klines(self.symbol, self.interval, limit=150)
            except Exception as e:
                log_activity("WARNING", f"Auth klines failed, falling back to public: {e}")

        try:
            return public_klines(self.symbol, self.interval, limit=150, testnet=False)
        except Exception as e:
            log_activity("ERROR", f"Public klines fetch failed: {e}")
            return None

    def _get_balance(self) -> float:
        """Return USDT balance. Uses simulated balance of $1000 when no auth client."""
        if self.client and not self.paper:
            try:
                _b = self.client.get_account_balance("USDT")
                return float(_b["total"]) if isinstance(_b, dict) else float(_b)
            except Exception as e:
                log_activity("WARNING", f"Balance fetch failed: {e}")
        # Paper or no auth — derive from initial assumption
        # The actual invested balance is tracked by the risk manager settings
        # We return a nominal $1000 when no real balance is available
        all_closed = [t for t in load_trades() if t.get("status") == "closed"]
        total_pnl  = sum((t.get("profit_loss") or 0) for t in all_closed)
        return max(100.0, 1000.0 + total_pnl)

    def _round_qty(self, qty: float) -> float:
        if self.client:
            try:
                return self.client.round_quantity(self.symbol, qty)
            except Exception:
                pass
        return round(qty, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton API
# ─────────────────────────────────────────────────────────────────────────────

def get_bot() -> Optional[TradingBot]:
    return _bot

def get_bot_session_trades() -> int:
    """Trades opened in the current bot session."""
    return _bot._session_trades if _bot else 0


def get_bot_last_signal() -> dict:
    """Most recent SIGNAL entry from the activity log."""
    for entry in reversed(load_activity()):
        if entry.get("level") == "SIGNAL":
            return entry
    return {}


def force_paper_trade(symbol: str, side: str, price: float, invested: float) -> dict:
    """Execute a paper test trade immediately — bypasses signal check. For testing."""
    from risk import RiskManager as _RM
    _rm = _bot.risk if _bot else _RM()
    qty  = round(invested / price, 6)
    sl_p = _rm.stop_loss_price(price, side)
    tp_p = _rm.take_profit_price(price, side)
    trade = {
        "coin":            symbol,
        "exchange":        "Paper (force test)",
        "type":            "bot",
        "strategy":        "Force Test",
        "side":            side,
        "entry_price":     price,
        "exit_price":      None,
        "quantity":        qty,
        "invested":        invested,
        "profit_loss":     None,
        "profit_loss_pct": None,
        "open_time":       datetime.now().isoformat(),
        "close_time":      None,
        "reason":          f"🧪 Force test {side} @ ${price:.4f}",
        "close_reason":    None,
        "stop_loss":       sl_p,
        "take_profit":     tp_p,
        "status":          "open",
        "paper":           True,
    }
    added = add_trade(trade)
    log_activity(
        "ORDER",
        f"🧪 FORCE TEST {side} | {qty:.6f} {symbol} @ ${price:.4f} | "
        f"${invested:.2f} USDT | SL ${sl_p:.4f} | TP ${tp_p:.4f}",
    )
    tg.trade_open(symbol, side, price, invested, "Force test trade", mode="PAPER-FORCE")
    return added


def create_bot(
    client,
    symbol:       str,
    strategy:     str,
    risk_manager: RiskManager,
    interval:     str   = "5m",
    check_every:  int   = 30,
    paper_mode:   bool  = True,
    threshold:    float = 0.0003,
) -> TradingBot:
    global _bot
    with _bot_lock:
        if _bot and _bot.is_running():
            _bot.stop()
            time.sleep(0.5)
        _bot = TradingBot(
            client          = client,
            symbol          = symbol,
            strategy        = strategy,
            risk_manager    = risk_manager,
            interval        = interval,
            check_every     = check_every,
            paper_mode      = paper_mode,
            price_threshold = threshold,
        )
    return _bot


def stop_bot():
    global _bot
    with _bot_lock:
        if _bot:
            _bot.stop()
