import threading
import time
import json
import os
import uuid
from datetime import datetime
from typing import Optional, List, Dict

from strategy import get_signal
from risk import RiskManager, RiskSettings

# ── Global singleton ─────────────────────────────────────────────────────────
_bot: Optional["TradingBot"] = None
_bot_lock = threading.Lock()

# ── Data paths ────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")
ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
_file_lock = threading.Lock()

MAX_ACTIVITY = 500


# ── Persistence helpers ───────────────────────────────────────────────────────

def load_trades() -> List[Dict]:
    with _file_lock:
        if not os.path.exists(TRADES_FILE):
            return []
        try:
            with open(TRADES_FILE, "r") as f:
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
            with open(ACTIVITY_FILE, "r") as f:
                data = json.load(f)
                return data[-MAX_ACTIVITY:]
        except Exception:
            return []


def _write_activity(entry: Dict):
    with _file_lock:
        data = []
        if os.path.exists(ACTIVITY_FILE):
            try:
                with open(ACTIVITY_FILE, "r") as f:
                    data = json.load(f)
            except Exception:
                data = []
        data.append(entry)
        data = data[-MAX_ACTIVITY:]
        with open(ACTIVITY_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)


def log_activity(level: str, message: str):
    _write_activity({
        "time": datetime.now().isoformat(),
        "level": level,
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
            t["exit_price"] = exit_price
            t["close_time"] = datetime.now().isoformat()
            t["status"] = "closed"
            t["close_reason"] = reason
            invested = t.get("invested", 0) or 0
            entry = t["entry_price"]
            side = t["side"]
            if side == "BUY":
                t["profit_loss"] = (exit_price - entry) / entry * invested
                t["profit_loss_pct"] = (exit_price - entry) / entry * 100
            else:
                t["profit_loss"] = (entry - exit_price) / entry * invested
                t["profit_loss_pct"] = (entry - exit_price) / entry * 100
            save_trades(trades)
            return t
    return None


def reset_all_data():
    with _file_lock:
        for f in [TRADES_FILE, ACTIVITY_FILE]:
            if os.path.exists(f):
                os.remove(f)


# ── Trading Bot ───────────────────────────────────────────────────────────────

class TradingBot:
    def __init__(
        self,
        client,
        symbol: str,
        strategy: str,
        risk_manager: RiskManager,
        interval: str = "5m",
        check_every: int = 30,
        paper_mode: bool = True,
        price_threshold: float = 0.0003,
    ):
        self.client = client
        self.symbol = symbol
        self.strategy = strategy
        self.risk = risk_manager
        self.interval = interval
        self.check_every = check_every
        self.paper = paper_mode
        self.threshold = price_threshold

        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ── Public control ────────────────────────────────────────────────────────

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            log_activity("WARNING", "⚠️ Bot already running — ignoring duplicate start request")
            return False
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"bot-{self.symbol}",
        )
        self._thread.start()
        mode = "PAPER" if self.paper else "LIVE"
        log_activity(
            "INFO",
            f"🚀 Bot started | {self.symbol} | Strategy: {self.strategy} | "
            f"Mode: {mode} | Interval: {self.interval} | Check every {self.check_every}s",
        )
        return True

    def stop(self):
        self._running = False
        log_activity("INFO", f"⛔ Bot stop requested for {self.symbol}")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._running)

    def update_settings(
        self,
        strategy: str = None,
        interval: str = None,
        check_every: int = None,
        paper_mode: bool = None,
        threshold: float = None,
    ):
        if strategy:
            self.strategy = strategy
        if interval:
            self.interval = interval
        if check_every is not None:
            self.check_every = check_every
        if paper_mode is not None:
            self.paper = paper_mode
        if threshold is not None:
            self.threshold = threshold

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self):
        log_activity("INFO", f"📡 Bot thread running | checks every {self.check_every}s")
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                log_activity("ERROR", f"Bot error: {exc}")
            # Interruptible sleep
            for _ in range(self.check_every):
                if not self._running:
                    break
                time.sleep(1)
        log_activity("INFO", f"🛑 Bot thread exited for {self.symbol}")

    def _tick(self):
        # Emergency stop guard
        if self.risk.settings.emergency_stop:
            log_activity("WARNING", "🚨 Emergency stop is ON — skipping tick")
            return

        # Get current price
        try:
            price = self.client.get_symbol_price(self.symbol)
        except Exception as e:
            log_activity("ERROR", f"Failed to fetch price: {e}")
            return
        log_activity("INFO", f"💰 Price check | {self.symbol} = ${price:,.4f}")

        # Manage open positions (SL / TP)
        open_trades = get_open_trades()
        bot_open = [t for t in open_trades if t.get("type") == "bot" and t.get("coin") == self.symbol]

        for trade in bot_open:
            entry = trade["entry_price"]
            side = trade["side"]
            sl_hit, sl_msg = self.risk.check_stop_loss(entry, price, side)
            tp_hit, tp_msg = self.risk.check_take_profit(entry, price, side)

            if sl_hit:
                log_activity("ORDER", f"🔴 STOP LOSS | Trade {trade['id']} | {sl_msg}")
                self._close(trade, price, sl_msg)
                return
            if tp_hit:
                log_activity("ORDER", f"🟢 TAKE PROFIT | Trade {trade['id']} | {tp_msg}")
                self._close(trade, price, tp_msg)
                return

            log_activity(
                "INFO",
                f"📌 Position open | {trade['id']} | Entry: ${entry:.4f} | "
                f"SL: ${self.risk.stop_loss_price(entry, side):.4f} | "
                f"TP: ${self.risk.take_profit_price(entry, side):.4f} | "
                f"Current: ${price:.4f}",
            )

        # Check if a new trade can be opened
        can, block = self.risk.can_open_trade(len(open_trades))
        if not can:
            log_activity("INFO", f"⏸️ Cannot open trade: {block}")
            return

        # Compute signal
        try:
            df = self.client.get_klines(self.symbol, self.interval, limit=150)
        except Exception as e:
            log_activity("ERROR", f"Failed to fetch klines: {e}")
            return

        signal, reason = get_signal(df, self.strategy, threshold=self.threshold)
        log_activity("SIGNAL", f"📊 [{self.strategy}] → {signal} | {reason}")

        if signal == "HOLD":
            log_activity("INFO", "⏭️ No action — signal is HOLD")
            return

        # Compute size
        try:
            balance = self.client.get_account_balance("USDT")
        except Exception as e:
            log_activity("ERROR", f"Failed to fetch balance: {e}")
            return

        invested = self.risk.calculate_invested(balance)
        if invested < 10:
            log_activity("WARNING", f"⚠️ Investment too small (${invested:.2f}) — need ≥$10")
            return

        qty = self.risk.calculate_quantity(balance, price)
        try:
            qty = self.client.round_quantity(self.symbol, qty)
        except Exception:
            pass

        sl_price = self.risk.stop_loss_price(price, signal)
        tp_price = self.risk.take_profit_price(price, signal)

        # Place order or paper record
        if not self.paper:
            try:
                order = self.client.place_market_order(self.symbol, signal, qty)
                fill_price = float(order.get("fills", [{}])[0].get("price", price))
                log_activity("ORDER", f"✅ LIVE {signal} | {qty} {self.symbol} @ ${fill_price:.4f}")
                price = fill_price
            except Exception as e:
                log_activity("ERROR", f"Order placement failed: {e}")
                return
        else:
            log_activity(
                "ORDER",
                f"📋 PAPER {signal} | {qty:.6f} {self.symbol} @ ${price:.4f} | "
                f"Invested: ${invested:.2f} | SL: ${sl_price:.4f} | TP: ${tp_price:.4f}",
            )

        trade = {
            "coin": self.symbol,
            "exchange": "Binance Testnet" if self.client.testnet else "Binance Live",
            "type": "bot",
            "strategy": self.strategy,
            "side": signal,
            "entry_price": price,
            "exit_price": None,
            "quantity": qty,
            "invested": invested,
            "profit_loss": None,
            "profit_loss_pct": None,
            "open_time": datetime.now().isoformat(),
            "close_time": None,
            "reason": reason,
            "close_reason": None,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "status": "open",
            "paper": self.paper,
        }
        added = add_trade(trade)
        log_activity("INFO", f"📝 Trade recorded | ID: {added['id']} | {signal} {self.symbol}")

    def _close(self, trade: Dict, price: float, reason: str):
        if not self.paper:
            opposite = "SELL" if trade["side"] == "BUY" else "BUY"
            try:
                self.client.place_market_order(self.symbol, opposite, trade["quantity"])
            except Exception as e:
                log_activity("ERROR", f"Close order failed: {e}")
                return
        closed = close_trade(trade["id"], price, reason)
        if closed:
            pnl = closed.get("profit_loss") or 0
            pct = closed.get("profit_loss_pct") or 0
            icon = "🟢" if pnl >= 0 else "🔴"
            log_activity(
                "ORDER",
                f"{icon} Trade closed | ID: {closed['id']} | "
                f"P&L: ${pnl:+.4f} ({pct:+.2f}%) | {reason}",
            )


# ── Singleton helpers ─────────────────────────────────────────────────────────

def get_bot() -> Optional[TradingBot]:
    return _bot


def create_bot(client, symbol, strategy, risk_manager, interval, check_every, paper_mode, threshold) -> TradingBot:
    global _bot
    with _bot_lock:
        if _bot and _bot.is_running():
            _bot.stop()
            time.sleep(1)
        _bot = TradingBot(
            client=client,
            symbol=symbol,
            strategy=strategy,
            risk_manager=risk_manager,
            interval=interval,
            check_every=check_every,
            paper_mode=paper_mode,
            price_threshold=threshold,
        )
        return _bot


def stop_bot():
    global _bot
    with _bot_lock:
        if _bot:
            _bot.stop()
