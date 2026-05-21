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

# ── Singleton ─────────────────────────────────────────────────────────────────
_bot: Optional["TradingBot"] = None
_bot_lock = threading.Lock()

# ── Shared live state (bot writes → dashboard reads every rerun) ───────────────
_state_lock = threading.Lock()
_shared: Dict = {
    "df":         None,   # pd.DataFrame with indicators
    "price":      None,   # float
    "updated_at": None,   # datetime
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

def _set_shared(df=None, price=None, tick=False):
    with _state_lock:
        if df    is not None: _shared["df"]    = df
        if price is not None: _shared["price"] = price
        _shared["updated_at"] = datetime.now()
        if tick: _shared["last_tick_at"] = datetime.now()

def get_shared_last_tick():
    with _state_lock:
        return _shared.get("last_tick_at")

# ── Data paths ────────────────────────────────────────────────────────────────
_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

TRADES_FILE   = os.path.join(DATA_DIR, "trades.json")
ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
_file_lock    = threading.Lock()
MAX_ACTIVITY  = 500


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
        return True

    def stop(self):
        self._running = False
        log_activity("INFO", f"⛔ Bot stopping for {self.symbol}")

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

        # ── 3. Can we open a new trade? ───────────────────────────────────────
        can_trade, block_reason = self.risk.can_open_trade(len(open_trades), self._session_trades)
        if not can_trade:
            log_activity("INFO", f"⏸️ {block_reason}")
            return

        # ── 4. Compute signal from klines ─────────────────────────────────────
        df = self._get_klines()
        if df is None:
            return

        # Push live klines (with indicators) to shared state for dashboard
        try:
            from strategy import get_indicators as _gi
            _set_shared(df=_gi(df))
        except Exception:
            _set_shared(df=df)

        signal, reason = get_signal(df, self.strategy, threshold=self.threshold)
        log_activity("SIGNAL", f"📊 [{self.strategy}] → {signal} | {reason}")

        if signal == "HOLD":
            log_activity("INFO", "⏭️ HOLD — no trade opened")
            return

        # ── 5. Size the position (FIXED USDT — never uses full balance) ─────────
        invested = self.risk.get_invest_amount()
        if invested < 10:
            log_activity(
                "WARNING",
                f"⚠️ Skipping — invest_per_trade ${invested:.2f} < $10 minimum. "
                f"Raise it in Risk → Invest per trade."
            )
            return

        qty = self._round_qty(invested / price)
        sl_p    = self.risk.stop_loss_price(price, signal)
        tp_p    = self.risk.take_profit_price(price, signal)

        # ── 6. Execute ────────────────────────────────────────────────────────
        if not self.paper and self.client:
            try:
                order      = self.client.place_market_order(self.symbol, signal, qty)
                fill_price = float(order.get("fills", [{}])[0].get("price", price))
                price      = fill_price
                log_activity("ORDER",
                    f"✅ LIVE {signal} | {qty} {self.symbol} @ ${price:.4f}")
            except Exception as e:
                log_activity("ERROR", f"Order failed: {e}")
                return
        else:
            log_activity(
                "ORDER",
                f"📋 PAPER {signal} | {qty:.6f} {self.symbol} @ ${price:.4f} | "
                f"${invested:.2f} invested | SL ${sl_p:.4f} | TP ${tp_p:.4f}",
            )

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
                return self.client.get_account_balance("USDT")
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
