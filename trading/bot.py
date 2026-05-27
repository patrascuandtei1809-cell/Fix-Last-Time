"""
TradingBot — multi-symbol, multi-exchange orchestrator.

Design:
  • Module-level `_bot` singleton survives Streamlit reruns.
  • Holds a dict of SymbolWorker instances keyed by f"{exchange}:{symbol}".
  • Single daemon thread iterates enabled workers each tick.
  • Global risk gate (GlobalRiskManager) runs BEFORE each worker tick.
  • Per-symbol trade persistence under data/trades/<exchange>_<symbol>.json.
  • All shared dashboard state is per-symbol; getters take an optional
    `symbol` arg and fall back to the first registered symbol for legacy
    no-arg calls from the existing dashboard code.
"""

import threading
import time
import json
import os
import uuid
import glob
from datetime import datetime
from typing import Optional, List, Dict

from risk import RiskManager, RiskSettings, GlobalRiskManager, GlobalRiskSettings
from exchanges.base import Exchange
from exchanges.binance import BinanceExchange
from exchanges import registry as ex_registry
from symbol_worker import SymbolWorker
import telegram_notifier as tg


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + locks
# ─────────────────────────────────────────────────────────────────────────────
_bot: Optional["TradingBot"] = None
_bot_lock = threading.Lock()

# ── Shared live state: dict keyed by symbol ─────────────────────────────────
_state_lock = threading.Lock()
_shared_per_symbol: Dict[str, Dict] = {}   # symbol → {df, price, signal, ...}
_primary_symbol: Optional[str] = None      # legacy no-arg getters fall back here


def _ensure_sym(sym: str) -> Dict:
    if sym not in _shared_per_symbol:
        _shared_per_symbol[sym] = {}
    return _shared_per_symbol[sym]


def _resolve_sym(sym: Optional[str]) -> Optional[str]:
    """If sym is None return primary symbol (or first available)."""
    if sym:
        return sym
    if _primary_symbol and _primary_symbol in _shared_per_symbol:
        return _primary_symbol
    if _shared_per_symbol:
        return next(iter(_shared_per_symbol))
    return None


def _set_shared_for(symbol: str, *, df=None, price=None, tick=False,
                    ai_trend=None, ai_signal_strength=None,
                    ai_why_bullets=None, ai_blocker=None,
                    signal=None, reason=None, confidence=None,
                    block_reason=None, last_order=None,
                    ai_decision=None, ai_confidence=None, ai_reason=None,
                    gpt_decision=None, gpt_confidence=None, gpt_reason=None,
                    gpt_age_sec=None, gpt_enabled=None, gpt_active=None):
    """Worker callback target. Writes to _shared_per_symbol[symbol]."""
    with _state_lock:
        s = _ensure_sym(symbol)
        if df         is not None: s["df"]    = df
        if price      is not None: s["price"] = price
        if signal     is not None: s["last_signal"]     = signal
        if reason     is not None: s["last_reason"]     = reason
        if confidence is not None: s["last_confidence"] = int(confidence)
        # AI fields — shown by the dashboard's AI status pill so the user can
        # see the live AI verdict for the currently-viewed symbol at a glance.
        if ai_decision        is not None: s["ai_decision"]        = ai_decision
        if ai_confidence      is not None: s["ai_confidence"]      = int(ai_confidence)
        if ai_reason          is not None: s["ai_reason"]          = ai_reason
        if ai_trend           is not None: s["ai_trend"]           = ai_trend
        if ai_signal_strength is not None: s["ai_signal_strength"] = int(ai_signal_strength)
        if ai_why_bullets     is not None: s["ai_why_bullets"]     = list(ai_why_bullets)
        if ai_blocker         is not None: s["ai_blocker"]         = ai_blocker
        # HYBRID MODE — GPT advisor fields (non-blocking secondary AI).
        if gpt_decision   is not None: s["gpt_decision"]   = gpt_decision
        if gpt_confidence is not None: s["gpt_confidence"] = int(gpt_confidence)
        if gpt_reason     is not None: s["gpt_reason"]     = gpt_reason
        if gpt_age_sec    is not None: s["gpt_age_sec"]    = gpt_age_sec
        if gpt_enabled    is not None: s["gpt_enabled"]    = bool(gpt_enabled)
        if gpt_active     is not None: s["gpt_active"]     = bool(gpt_active)
        if block_reason is not None:
            s["block_reason"]    = block_reason
            s["block_reason_at"] = datetime.now()
        if last_order is not None:
            s["last_order"]    = last_order
            s["last_order_at"] = datetime.now()
        s["updated_at"] = datetime.now()
        if tick:
            s["last_tick_at"] = datetime.now()


# ── Back-compat getters (all accept optional `symbol` arg) ──────────────────
def get_shared_df(symbol: Optional[str] = None):
    with _state_lock:
        sym = _resolve_sym(symbol)
        return _shared_per_symbol.get(sym, {}).get("df") if sym else None


def get_shared_price(symbol: Optional[str] = None):
    with _state_lock:
        sym = _resolve_sym(symbol)
        return _shared_per_symbol.get(sym, {}).get("price") if sym else None


def get_shared_updated_at(symbol: Optional[str] = None):
    with _state_lock:
        sym = _resolve_sym(symbol)
        return _shared_per_symbol.get(sym, {}).get("updated_at") if sym else None


def get_shared_last_tick(symbol: Optional[str] = None):
    with _state_lock:
        sym = _resolve_sym(symbol)
        return _shared_per_symbol.get(sym, {}).get("last_tick_at") if sym else None


def get_bot_signal_meta(symbol: Optional[str] = None) -> dict:
    with _state_lock:
        sym = _resolve_sym(symbol)
        s   = _shared_per_symbol.get(sym, {}) if sym else {}
        return {
            "signal":     s.get("last_signal"),
            "reason":     s.get("last_reason"),
            "confidence": int(s.get("last_confidence") or 0),
            # AI fields — surfaced by the dashboard's AI pill.
            "ai_decision":        s.get("ai_decision"),
            "ai_confidence":      int(s.get("ai_confidence") or 0),
            "ai_reason":          s.get("ai_reason"),
            "ai_trend":           s.get("ai_trend", "SIDEWAYS"),
            "ai_signal_strength": int(s.get("ai_signal_strength") or 0),
            "ai_why_bullets":     s.get("ai_why_bullets") or [],
            "ai_blocker":         s.get("ai_blocker", ""),
            # HYBRID MODE — GPT advisor surfaced by dashboard badges.
            "gpt_decision":       s.get("gpt_decision", ""),
            "gpt_confidence":     int(s.get("gpt_confidence") or 0),
            "gpt_reason":         s.get("gpt_reason", ""),
            "gpt_age_sec":        s.get("gpt_age_sec"),
            "gpt_enabled":        bool(s.get("gpt_enabled", False)),
            "gpt_active":         bool(s.get("gpt_active",  False)),
        }


def get_bot_diagnostics(symbol: Optional[str] = None) -> dict:
    with _state_lock:
        sym = _resolve_sym(symbol)
        s   = _shared_per_symbol.get(sym, {}) if sym else {}
        return {
            "block_reason":    s.get("block_reason"),
            "block_reason_at": s.get("block_reason_at"),
            "last_order":      s.get("last_order"),
            "last_order_at":   s.get("last_order_at"),
        }


def get_all_symbol_state() -> Dict[str, Dict]:
    """Snapshot of every tracked symbol — for the dashboard overview strip."""
    with _state_lock:
        return {
            sym: {
                "price":      s.get("price"),
                "signal":     s.get("last_signal"),
                "reason":     s.get("last_reason"),
                "confidence": s.get("last_confidence", 0),
                "block":      s.get("block_reason") or "",
                "updated_at": s.get("updated_at"),
            }
            for sym, s in _shared_per_symbol.items()
        }


# ─────────────────────────────────────────────────────────────────────────────
# Data paths + per-symbol trade persistence
# ─────────────────────────────────────────────────────────────────────────────
_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(_DIR, "data")
TRADES_DIR = os.path.join(DATA_DIR, "trades")
os.makedirs(TRADES_DIR, exist_ok=True)

ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LEGACY_TRADES = os.path.join(DATA_DIR, "trades.json")
_file_lock    = threading.Lock()
_settings_lock = threading.Lock()
MAX_ACTIVITY  = 500


# ── One-time migration: archive old single-file trades.json ─────────────────
def _archive_legacy_trades_file():
    """User chose archive-not-migrate. Rename old trades.json → trades.json.bak."""
    if os.path.exists(LEGACY_TRADES):
        bak = LEGACY_TRADES + ".bak"
        try:
            os.replace(LEGACY_TRADES, bak)
            print(f"[BOT] archived legacy {LEGACY_TRADES} → {bak}", flush=True)
        except Exception as e:
            print(f"[BOT] could not archive legacy trades: {e}", flush=True)


_archive_legacy_trades_file()


# ── Settings persistence ────────────────────────────────────────────────────
def load_settings() -> dict:
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
    with _settings_lock:
        try:
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, SETTINGS_FILE)
            print(f"[SETTINGS] saved {len(data)} keys", flush=True)
            return True
        except Exception as e:
            print(f"[SETTINGS] save failed: {e}", flush=True)
            return False


# ── Per-symbol trade files ──────────────────────────────────────────────────
def _trade_file_for(exchange: str, symbol: str) -> str:
    safe_ex = (exchange or "unknown").replace(" ", "_").replace("/", "_")
    return os.path.join(TRADES_DIR, f"{safe_ex}_{symbol}.json")


def _load_trade_file(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f) or []
    except Exception:
        return []


def _save_trade_file(path: str, trades: List[Dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(trades, f, indent=2, default=str)
    os.replace(tmp, path)


def _exchange_key(trade: Dict) -> str:
    """Map a trade dict to its on-disk exchange bucket. LIVE only."""
    ex = (trade.get("exchange") or "").lower()
    if "binance" in ex:
        return "binance"
    return ex or "unknown"


def load_trades(symbol: Optional[str] = None,
                exchange: Optional[str] = None) -> List[Dict]:
    """Return all trades, optionally filtered by symbol and/or exchange."""
    with _file_lock:
        files = sorted(glob.glob(os.path.join(TRADES_DIR, "*.json")))
        all_trades: List[Dict] = []
        for fp in files:
            all_trades.extend(_load_trade_file(fp))
    if symbol:
        all_trades = [t for t in all_trades if t.get("coin") == symbol]
    if exchange:
        all_trades = [t for t in all_trades if _exchange_key(t) == exchange]
    return all_trades


def save_trades(trades: List[Dict]):
    """Rewrite all per-symbol files from the given full list.

    Kept for back-compat with callers that load → mutate → save. Groups trades
    by (exchange_bucket, coin) and writes one file per group.
    """
    grouped: Dict[str, List[Dict]] = {}
    for t in trades:
        key = _trade_file_for(_exchange_key(t), t.get("coin", "UNKNOWN"))
        grouped.setdefault(key, []).append(t)
    with _file_lock:
        # Truncate any existing files that are no longer represented? Safer to
        # leave them alone — close_trade always writes the file that owns the id.
        for fp, items in grouped.items():
            _save_trade_file(fp, items)


def get_open_trades(symbol: Optional[str] = None) -> List[Dict]:
    return [t for t in load_trades(symbol=symbol) if t.get("status") == "open"]


def add_trade(trade: Dict) -> Dict:
    """Append a trade to its symbol/exchange file."""
    trade["id"] = str(uuid.uuid4())[:8]
    path = _trade_file_for(_exchange_key(trade), trade.get("coin", "UNKNOWN"))
    with _file_lock:
        existing = _load_trade_file(path)
        existing.append(trade)
        _save_trade_file(path, existing)
    return trade


def close_trade(trade_id: str, exit_price: float, reason: str) -> Optional[Dict]:
    """Find the trade across all per-symbol files and close it."""
    with _file_lock:
        for fp in sorted(glob.glob(os.path.join(TRADES_DIR, "*.json"))):
            trades = _load_trade_file(fp)
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
                    t["exit_price"]   = exit_price
                    t["close_time"]   = datetime.now().isoformat()
                    t["close_reason"] = reason
                    t["status"]       = "closed"
                    _save_trade_file(fp, trades)
                    return t
    return None


# ── Activity log (global) ───────────────────────────────────────────────────
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


def reset_all_data():
    """Wipe activity + all per-symbol trade files."""
    with _file_lock:
        if os.path.exists(ACTIVITY_FILE):
            os.remove(ACTIVITY_FILE)
        for fp in glob.glob(os.path.join(TRADES_DIR, "*.json")):
            os.remove(fp)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram dispatch helper (used by SymbolWorker callback)
# ─────────────────────────────────────────────────────────────────────────────
def _tg_dispatch(kind: str, *args, **kwargs):
    try:
        if kind == "trade_open":
            tg.trade_open(*args, **kwargs)
        elif kind == "trade_close":
            tg.trade_close(*args, **kwargs)
        elif kind == "error_alert":
            tg.error_alert(*args, **kwargs)
        elif kind == "bot_event":
            tg.bot_event(*args, **kwargs)
    except Exception as e:
        print(f"[TG] dispatch {kind} failed: {e}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# TradingBot — multi-symbol orchestrator
# ─────────────────────────────────────────────────────────────────────────────
class TradingBot:
    def __init__(
        self,
        workers:        Dict[str, SymbolWorker],
        global_risk:    GlobalRiskManager,
        check_every:    int = 30,
        initial_balance: float = 1000.0,
    ):
        self.workers          = workers           # key = f"{exchange}:{symbol}"
        self.global_risk      = global_risk
        self.check_every      = check_every
        self._initial_balance = initial_balance

        self._thread:   Optional[threading.Thread] = None
        self._running:  bool = False

    # ── Control ──────────────────────────────────────────────────────────────
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            log_activity("WARNING", "⚠️ Bot already running — ignoring duplicate start")
            return False
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"alphatrade-orchestrator",
        )
        self._thread.start()
        syms = ", ".join(sorted({w.symbol for w in self.workers.values()}))
        exs  = ", ".join(sorted({w.exchange.name for w in self.workers.values()}))
        log_activity("INFO",
            f"🚀 Multi-symbol bot started | symbols={syms} | exchanges={exs} | "
            f"check={self.check_every}s | "
            f"global exposure cap=${self.global_risk.settings.max_total_exposure_usdt:.0f}")
        _tg_dispatch("bot_event", "started",
                     f"Symbols: {syms}\nExchanges: {exs}")
        return True

    def stop(self):
        self._running = False
        log_activity("INFO", "⛔ Orchestrator stopping")
        _tg_dispatch("bot_event", "stopped", "all symbols")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._running)

    # ── Worker management ────────────────────────────────────────────────────
    def add_worker(self, worker: SymbolWorker):
        key = f"{worker.exchange.name}:{worker.symbol}"
        self.workers[key] = worker
        log_activity("INFO", f"➕ Worker added: {key}")

    def remove_worker(self, exchange_name: str, symbol: str):
        key = f"{exchange_name}:{symbol}"
        if key in self.workers:
            del self.workers[key]
            log_activity("INFO", f"➖ Worker removed: {key}")

    # ── Main loop ────────────────────────────────────────────────────────────
    def _loop(self):
        log_activity("INFO",
            f"📡 Orchestrator alive — {len(self.workers)} workers, "
            f"tick every {self.check_every}s")
        # Track last successful order across all symbols for the
        # "ACTIVE BUT NO SIGNALS" forced log.
        self._last_trade_executed_at: Optional[datetime] = None
        self._loop_started_at = datetime.now()
        self._last_idle_warn_at: Optional[datetime] = None
        # Threshold for idle warning: 5 min OR 10× check_every, whichever is larger.
        idle_warn_secs = max(300, self.check_every * 10)
        while self._running:
            # Helper: re-read current open trades + today's PnL fresh.
            # Called between EACH worker so global caps cannot be overshot by
            # multiple workers opening within the same orchestrator cycle.
            def _refresh_global_state():
                try:
                    open_now = get_open_trades()
                except Exception as e:
                    log_activity("ERROR", f"Failed to load open trades: {e}")
                    open_now = []
                today = datetime.now().strftime("%Y-%m-%d")
                pnl_today = sum(
                    (t.get("profit_loss") or 0)
                    for t in load_trades()
                    if t.get("type") == "bot" and t.get("status") == "closed"
                    and (t.get("close_time") or "").startswith(today)
                )
                pct = (pnl_today / self._initial_balance * 100) if self._initial_balance else 0.0
                return open_now, pct

            all_open, daily_pct = _refresh_global_state()

            # Headline "scan starting" log line per cycle.
            _syms = "/".join(sorted({w.symbol.replace("USDT", "")
                                     for w in self.workers.values()}))
            print(f"[BOT] ACTIVE SCAN {_syms} | open={len(all_open)} "
                  f"| daily_pnl={daily_pct:+.2f}%", flush=True)

            # Snapshot count of bot trades opened this session before workers run.
            _before_count = sum(w._session_trades for w in self.workers.values())

            # Iterate workers — refresh state between each so the gate sees
            # any trades just opened in this same cycle.
            for key, worker in list(self.workers.items()):
                if not self._running:
                    break

                # Re-snapshot before this worker runs (cheap: file read + lock).
                all_open, daily_pct = _refresh_global_state()

                def global_gate(invest: float, sym: str,
                                _all=all_open, _dp=daily_pct):
                    return self.global_risk.check_global(
                        all_open_trades = _all,
                        new_invest_usdt = invest,
                        new_symbol      = sym,
                        daily_loss_pct  = _dp,
                    )

                try:
                    worker.tick(all_open_trades=all_open, global_gate_fn=global_gate)
                except Exception as exc:
                    log_activity("ERROR", f"Worker {key} crashed: {exc}")
                    _tg_dispatch("error_alert", f"Worker {key} crashed: {exc}")

            # Did any worker execute a trade this cycle?
            _after_count = sum(w._session_trades for w in self.workers.values())
            if _after_count > _before_count:
                self._last_trade_executed_at = datetime.now()

            # Idle warning: bot ON, has been running long enough, no trades for
            # idle_warn_secs → force a single log line per warning interval.
            _now = datetime.now()
            _ref = self._last_trade_executed_at or self._loop_started_at
            _idle = (_now - _ref).total_seconds()
            _last_warn = self._last_idle_warn_at
            _warned_recently = (_last_warn is not None
                                and (_now - _last_warn).total_seconds() < idle_warn_secs)
            if _idle >= idle_warn_secs and not _warned_recently:
                _mins = int(_idle // 60)
                print(f"[BOT] BOT ACTIVE BUT NO SIGNALS — threshold too strict "
                      f"(no trades for {_mins} min across {_syms})", flush=True)
                log_activity("WARNING",
                    f"⏳ Bot active but no signals for {_mins} min — "
                    f"consider lowering threshold or switching to Price Movement.")
                self._last_idle_warn_at = _now

            # Auto-stop on daily-loss breaker (re-read once more after all workers)
            _, daily_pct = _refresh_global_state()
            if daily_pct <= -abs(self.global_risk.settings.max_daily_loss_pct):
                log_activity("WARNING",
                    f"🛑 Auto-stopping — daily loss {daily_pct:+.2f}% ≤ "
                    f"−{self.global_risk.settings.max_daily_loss_pct}%")
                _tg_dispatch("bot_event", "auto-stopped",
                             f"Daily loss limit hit ({daily_pct:+.2f}%)")
                self._running = False
                break

            # Interruptible sleep
            for _ in range(self.check_every):
                if not self._running:
                    break
                time.sleep(1)

        log_activity("INFO", "🛑 Orchestrator thread exited")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton API
# ─────────────────────────────────────────────────────────────────────────────
def get_bot() -> Optional[TradingBot]:
    return _bot


def get_bot_session_trades() -> int:
    """Total bot-opened trades across all symbols this session."""
    if not _bot:
        return 0
    return sum(w._session_trades for w in _bot.workers.values())


def get_bot_last_signal() -> dict:
    """Most recent SIGNAL entry from the activity log (any symbol)."""
    for entry in reversed(load_activity()):
        if entry.get("level") == "SIGNAL":
            return entry
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Builder — accepts BOTH new multi-symbol signature AND legacy single-symbol.
# ─────────────────────────────────────────────────────────────────────────────
def create_bot(
    client=None,                                # BinanceClient (LIVE) — REQUIRED for orders
    symbol:       Optional[str] = None,         # legacy: single-symbol mode
    strategy:     str = "Active Scalper",       # ACTIVE SCALPER MODE — only mode
    risk_manager: Optional[RiskManager] = None,
    interval:     str = "1m",                   # 1m candles for scalping
    check_every:  int = 2,                      # 2s tick — ACTIVE SCALPER spec
    threshold:    float = 0.0001,               # 0.01% (passed as fraction)
    # Multi-symbol args
    symbols:               Optional[List[str]] = None,
    per_symbol_risk:       Optional[Dict[str, RiskManager]] = None,
    global_risk:           Optional[GlobalRiskManager] = None,
    exchange:              Optional[Exchange] = None,
    initial_balance:       float = 1000.0,
    # AI assist (extra decision layer)
    ai_assist:             bool = True,         # ACTIVE SCALPER — AI always on
    ai_aggressiveness:     str  = "Active Scalper",  # ignored — single hardcoded mode
) -> TradingBot:
    """Build (or rebuild) the singleton bot. LIVE Binance Mainnet only."""
    global _bot, _primary_symbol

    # Build / register the exchange
    if exchange is None:
        exchange = BinanceExchange(client=client)
        ex_registry.clear()
        ex_registry.register(exchange)
    else:
        ex_registry.register(exchange)

    # ACTIVE SCALPER MODE: hardcoded to BTC + ETH + SOL unless caller overrides.
    sym_list = symbols if symbols else ([symbol] if symbol else
                                        ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    if len(sym_list) > 3:
        print(f"[BOT] capping symbols list to 3 (got {len(sym_list)})", flush=True)
        sym_list = sym_list[:3]

    # Resolve per-symbol risk managers (fallback to shared one)
    per_sym = dict(per_symbol_risk or {})
    for s in sym_list:
        if s not in per_sym:
            per_sym[s] = risk_manager or RiskManager(RiskSettings())

    # Global risk fallback (derive from legacy single-risk daily-loss if needed)
    if global_risk is None:
        gs = GlobalRiskSettings()
        if risk_manager:
            gs.max_daily_loss_pct = risk_manager.settings.max_daily_loss_pct
            gs.emergency_stop     = risk_manager.settings.emergency_stop
        global_risk = GlobalRiskManager(gs)

    # Build workers
    workers: Dict[str, SymbolWorker] = {}
    with _bot_lock:
        # Stop previous bot (if any) before swapping singleton
        if _bot and _bot.is_running():
            _bot.stop()
            time.sleep(0.5)

        # Reset shared state for symbols no longer active (keep history for new ones)
        with _state_lock:
            for stale in list(_shared_per_symbol.keys()):
                if stale not in sym_list:
                    _shared_per_symbol.pop(stale, None)

        for sym in sym_list:
            rm = per_sym[sym]
            w = SymbolWorker(
                exchange          = exchange,
                symbol            = sym,
                strategy          = strategy,
                risk_manager      = rm,
                interval          = interval,
                price_threshold   = threshold,
                on_log            = log_activity,
                on_state_update   = _set_shared_for,
                on_open_trade     = add_trade,
                on_close_trade    = close_trade,
                on_telegram       = _tg_dispatch,
                ai_assist         = ai_assist,
                ai_aggressiveness = ai_aggressiveness,
            )
            workers[f"{exchange.name}:{sym}"] = w

        _primary_symbol = sym_list[0]
        _bot = TradingBot(
            workers         = workers,
            global_risk     = global_risk,
            check_every     = check_every,
            initial_balance = initial_balance,
        )
    return _bot


def stop_bot():
    global _bot
    with _bot_lock:
        if _bot:
            _bot.stop()


# force_paper_trade was removed — LIVE-only build has no force/paper test trades.
