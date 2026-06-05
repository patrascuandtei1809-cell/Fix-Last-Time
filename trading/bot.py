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
import diagnostics
# ── 20-Minute Dip strategy (Task #11) — the ONLY live order path ─────────────
# When TradingBot.dip_mode is True (default) the orchestrator runs this engine
# per active symbol and NEVER touches the legacy worker.tick/execute_entry path.
import live_settings
from live_engine import DipLiveEngine
try:
    # AUTO-DISABLE gate — the live bot refuses to auto-trade any
    # (strategy, timeframe) that a research run has not ACCEPTED as having a
    # positive after-fee edge. Default-safe: no validated allowlist → no auto
    # entry. Manual trades are never affected by this gate.
    from research import is_strategy_validated as _is_strategy_validated
except Exception:                      # pragma: no cover - research optional
    _is_strategy_validated = None


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
                    gpt_age_sec=None, gpt_enabled=None, gpt_active=None,
                    score=None, score_breakdown=None, regime=None):
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
        # SMART PRIORITY SCALPER — per-symbol score + breakdown for dashboard.
        if score           is not None: s["score"]           = int(score)
        if score_breakdown is not None: s["score_breakdown"] = dict(score_breakdown)
        if regime          is not None: s["regime"]          = str(regime)
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
                # SMART PRIORITY SCALPER — surfaced on the per-symbol cards.
                "score":           int(s.get("score") or 0),
                "score_breakdown": s.get("score_breakdown") or {},
                "regime":          s.get("regime") or "",
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
    # P1 SYNC — explicit OPEN state-transition log so every position lifecycle
    # is auditable in the activity feed.
    try:
        log_activity("INFO",
            f"🔄 STATE {trade.get('coin','?')} {trade.get('side','?')} "
            f"id={trade['id']} →OPEN @ ${float(trade.get('entry_price') or 0):.4f} "
            f"(${float(trade.get('invested') or 0):.2f}, "
            f"{'🤖 bot' if trade.get('type')=='bot' else '👤 manual'})")
    except Exception:
        pass
    return trade


def close_trade(trade_id: str, exit_price: float, reason: str,
                exit_fee: float = 0.0) -> Optional[Dict]:
    """Find the trade across all per-symbol files and close it.

    P2 REAL PnL: records GROSS pnl (price move only — kept on `profit_loss` for
    backward compatibility with old records/UI) AND the fee-aware NET pnl. The
    entry commission was stamped on the trade at open time (`entry_fee`); the
    exit commission is passed in here from the live close order.
    """
    with _file_lock:
        for fp in sorted(glob.glob(os.path.join(TRADES_DIR, "*.json"))):
            trades = _load_trade_file(fp)
            for t in trades:
                if t.get("id") == trade_id and t.get("status") == "open":
                    invested = t.get("invested") or 0
                    entry    = t["entry_price"]
                    side     = t["side"]
                    if side == "BUY":
                        gross     = (exit_price - entry) / entry * invested
                        gross_pct = (exit_price - entry) / entry * 100
                    else:
                        gross     = (entry - exit_price) / entry * invested
                        gross_pct = (entry - exit_price) / entry * 100
                    entry_fee  = float(t.get("entry_fee") or 0.0)
                    exit_fee_v = float(exit_fee or 0.0)
                    total_fees = entry_fee + exit_fee_v
                    net        = gross - total_fees
                    net_pct    = (net / invested * 100) if invested else 0.0
                    # P2 honesty: total_fees is only COMPLETE when BOTH legs were
                    # really captured. Entry fee is stamped at open (bot + manual);
                    # legacy trades / failed fee conversions leave a leg at 0. When
                    # incomplete, the dashboard estimates rather than understating.
                    had_entry  = ("entry_fee" in t) and (entry_fee > 0)
                    fees_complete = had_entry and (exit_fee_v > 0)
                    # Back-compat: profit_loss stays GROSS (existing dashboards/
                    # reports read it). Real fee-aware figures are additive.
                    t["profit_loss"]     = gross
                    t["profit_loss_pct"] = gross_pct
                    t["gross_pnl"]       = gross
                    t["gross_pnl_pct"]   = gross_pct
                    t["entry_fee"]       = entry_fee
                    t["exit_fee"]        = exit_fee_v
                    t["total_fees"]      = total_fees
                    t["fees_complete"]   = fees_complete
                    t["net_pnl"]         = net
                    t["net_pnl_pct"]     = net_pct
                    t["exit_price"]   = exit_price
                    t["close_time"]   = datetime.now().isoformat()
                    t["close_reason"] = reason
                    t["status"]       = "closed"
                    _save_trade_file(fp, trades)
                    # P1 SYNC — explicit OPEN→CLOSED state-transition log.
                    try:
                        log_activity("INFO",
                            f"🔄 STATE {t.get('coin','?')} {side} id={trade_id} "
                            f"OPEN→CLOSED @ ${exit_price:.4f} | net ${net:+.2f} "
                            f"(gross ${gross:+.2f} − fees ${total_fees:.4f}) | "
                            f"{(reason or '')[:60]}")
                    except Exception:
                        pass
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

        # ── SMART PRIORITY SCALPER state ─────────────────────────────────
        # SMART AI SCALPING BOT (May 28, 2026): quality-first thresholds.
        # Score 65, confidence 65, GPT probability 65, 10s global throttle.
        # Anti-idle still lowers the threshold but floor is 60 (NOT 30): a
        # truly motionless market HOLDs rather than forcing a low-quality
        # entry. GPT runs as a GLOBAL ANALYST every cycle (≥1 candidate) and
        # can veto with NO_TRADE.
        # FINAL STABLE MODE (May 29 2026): make the bot ACT. A candidate now
        # qualifies on EITHER score>=threshold OR AI confidence>=floor (see the
        # qualified filter below) — not both. GPT is advisory only.
        self.score_threshold_base: int = 50   # path B: score-based entry
        self.score_threshold:      int = 50   # current threshold (anti-idle lowers it)
        self.score_threshold_floor: int = 40  # anti-idle never drops below this
        self.confidence_floor:     int = 30   # path A: AI-confidence entry
        self.gpt_prob_floor:       int = 50   # GPT advisory only (no hard veto)
        self.global_throttle_sec:   int = 5   # min seconds between any 2 new trades
        self._last_global_trade_at: float = 0.0   # unix seconds
        # AUTO-DISABLE gate: only auto-trade strategy/timeframes a research run
        # has ACCEPTED (positive after-fee edge). Default-safe ON. Operator can
        # override with ALPHATRADE_ALLOW_UNVALIDATED=1 (trades at their own risk).
        self.require_validation: bool = (
            os.environ.get("ALPHATRADE_ALLOW_UNVALIDATED", "0") != "1")
        self._validation_block_logged: Optional[str] = None
        # Candidate queue — workers append their evaluation here via on_candidate;
        # orchestrator picks winner each cycle then clears the queue.
        self._candidates: List[Dict] = []
        self._cand_lock = threading.Lock()
        # Wire each worker's candidate hook to ours (workers built by create_bot
        # already pass on_candidate; this re-binds in case workers were added
        # via add_worker without it).
        for _w in self.workers.values():
            _w._on_candidate = self._collect_candidate

        # ── 20-Minute Dip strategy (Task #11) ────────────────────────────────
        # When dip_mode is True the orchestrator runs ONLY the DipLiveEngine
        # per symbol. The entire legacy decision stack above (score thresholds,
        # confidence floors, GPT/AI veto, weighted scoring, ranking, anti-idle,
        # validation/allowlist) is kept importable but never reached.
        self.dip_mode: bool = True
        self._dip_engines: Dict[str, DipLiveEngine] = {}
        self._cooldown = live_settings.cooldown_store()

    def _collect_candidate(self, ev: Dict) -> None:
        with self._cand_lock:
            self._candidates.append(ev)

    # ── 20-Minute Dip strategy (Task #11) ────────────────────────────────────
    def _dip_engine_for(self, worker: SymbolWorker) -> DipLiveEngine:
        """Build (once) and reuse a DipLiveEngine for a worker's exchange."""
        key = f"{worker.exchange.name}:{worker.symbol}"
        eng = self._dip_engines.get(key)
        if eng is None:
            eng = DipLiveEngine(
                exchange           = worker.exchange,
                on_log             = log_activity,
                on_state           = _set_shared_for,
                on_open_trade      = add_trade,
                close_fn           = worker._close_position,
                cooldown           = self._cooldown,
                manage_manual      = worker.manage_manual_trades,
            )
            self._dip_engines[key] = eng
        return eng

    def _run_dip_cycle(self, refresh_fn) -> bool:
        """Run the DipLiveEngine across all active symbols. Returns True if any
        symbol placed a LIVE order this cycle."""
        settings = live_settings.get_settings()
        # Aggressive default ON ⇒ scan faster; calmer cadence when OFF.
        self.check_every = 2 if settings.aggressive_on else 6

        traded = False
        for key, worker in list(self.workers.items()):
            if not self._running:
                break
            all_open, daily_pct = refresh_fn()
            exposure = sum((t.get("invested") or 0) for t in all_open)
            emergency = bool(self.global_risk.settings.emergency_stop
                             or getattr(worker.risk.settings, "emergency_stop", False))

            def global_gate(invest: float, sym: str,
                            _all=all_open, _dp=daily_pct):
                return self.global_risk.check_global(
                    all_open_trades = _all,
                    new_invest_usdt = invest,
                    new_symbol      = sym,
                    daily_loss_pct  = _dp,
                )
            try:
                rec = self._dip_engine_for(worker).evaluate(
                    symbol           = worker.symbol,
                    settings         = settings,
                    open_trades      = all_open,
                    current_exposure = exposure,
                    global_gate_fn   = global_gate,
                    daily_loss_pct   = daily_pct,
                    emergency_stop   = emergency,
                )
                if rec.traded:
                    traded = True
                    worker._session_trades += 1
            except Exception as exc:
                log_activity("ERROR", f"Dip engine {key} crashed: {exc}")
        return traded

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
        """Crash-PROOF thread target. If a single orchestrator cycle ever raises
        an unhandled exception (e.g. a transient data/IO error in the
        global-state refresh, the winner path, or a GPT/network hiccup), the
        thread MUST NOT die — otherwise trading silently stops and the operator
        is forced to restart the whole process (`restart-bot.sh`). We catch
        everything, log it, pause briefly, and re-enter the cycle. The ONLY ways
        the thread exits are an operator Stop or the daily-loss breaker (both set
        self._running=False)."""
        _last_alert_sig = None
        _last_alert_at  = 0.0
        while self._running:
            try:
                self._inner_loop()
            except Exception as _exc:
                # The recovery path itself MUST NOT raise — if it did, the
                # supervisor thread would die and defeat the whole point. Wrap
                # every side effect defensively.
                try:
                    print(f"[BOT] orchestrator crashed — auto-recovering in 3s: "
                          f"{_exc}", flush=True)
                    log_activity("ERROR",
                        f"Orchestrator crashed — auto-recovering in 3s: {_exc}")
                    # Throttle alerts: one per exception signature per 5 min so a
                    # persistent fault doesn't spam Telegram/activity every 3s.
                    _sig = f"{type(_exc).__name__}:{str(_exc)[:80]}"
                    _now = time.time()
                    if _sig != _last_alert_sig or (_now - _last_alert_at) >= 300:
                        _tg_dispatch("error_alert",
                            f"Orchestrator crashed, auto-recovering: {_exc}")
                        _last_alert_sig = _sig
                        _last_alert_at  = _now
                except Exception:
                    pass
                for _ in range(3):
                    if not self._running:
                        break
                    time.sleep(1)
        log_activity("INFO", "🛑 Orchestrator thread fully exited")

    def _inner_loop(self):
        log_activity("INFO",
            f"📡 Orchestrator alive — {len(self.workers)} workers, "
            f"tick every {self.check_every}s")
        # Track last successful order across all symbols for the
        # "ACTIVE BUT NO SIGNALS" forced log.
        self._last_trade_executed_at: Optional[datetime] = None
        self._loop_started_at = datetime.now()
        self._last_idle_warn_at: Optional[datetime] = None
        # Auto ghost-trade reconciliation timer (0 = run on first cycle).
        self._last_reconcile_at: float = 0.0
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

            # ── 20-Minute Dip strategy (Task #11) — ONLY live path ───────────
            # Run the DipLiveEngine per symbol and skip the entire legacy
            # Phase A/B/C decision stack below (kept importable but unreached).
            if getattr(self, "dip_mode", True):
                try:
                    if self._run_dip_cycle(_refresh_global_state):
                        self._last_trade_executed_at = datetime.now()
                        self._last_global_trade_at   = time.time()
                except Exception as exc:
                    log_activity("ERROR", f"Dip cycle crashed: {exc}")
                # Daily-loss breaker still applies (safety).
                _, _dp = _refresh_global_state()
                if _dp <= -abs(self.global_risk.settings.max_daily_loss_pct):
                    log_activity("WARNING",
                        f"🛑 Auto-stopping — daily loss {_dp:+.2f}% ≤ "
                        f"−{self.global_risk.settings.max_daily_loss_pct}%")
                    self._running = False
                    break
                for _ in range(self.check_every):
                    if not self._running:
                        break
                    time.sleep(1)
                continue

            # ── Auto ghost-trade reconciliation (every 5 min) ────────────
            # Close LOCAL open trades that no longer exist on Binance. Safe +
            # conservative: only the clear "no balance" case is auto-closed.
            _now_ts = time.time()
            if _now_ts - self._last_reconcile_at >= 300:
                self._last_reconcile_at = _now_ts
                try:
                    _ex = next(iter(self.workers.values())).exchange
                    _rc = diagnostics.reconcile_ghost_trades(_ex)
                    if _rc.get("closed"):
                        log_activity("WARNING",
                            f"🧹 Reconciled {len(_rc['closed'])} ghost trade(s) "
                            f"not present on Binance — marked closed.")
                except Exception as _re:
                    print(f"[DIAG] auto-reconcile failed: {_re}", flush=True)

            # Snapshot count of bot trades opened this session before workers run.
            _before_count = sum(w._session_trades for w in self.workers.values())

            # ── SMART PRIORITY SCALPER — Phase A: tick all workers ────────
            # Each worker manages its own open positions (SL/TP/BE/red-exit)
            # and computes signal + score, then publishes via on_candidate
            # callback INSTEAD of placing an order. Orders happen below in
            # Phase B (winner only).
            with self._cand_lock:
                self._candidates.clear()

            for key, worker in list(self.workers.items()):
                if not self._running:
                    break
                # Re-snapshot before this worker runs (positions may have
                # closed in a previous worker's manage-positions phase).
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
                    worker.tick(all_open_trades=all_open,
                                global_gate_fn=global_gate)
                except Exception as exc:
                    log_activity("ERROR", f"Worker {key} crashed: {exc}")
                    _tg_dispatch("error_alert", f"Worker {key} crashed: {exc}")

            # ── SMART PRIORITY SCALPER — Phase B: pick winner ─────────────
            with self._cand_lock:
                cands = list(self._candidates)

            # Anti-idle: lower score_threshold by 10 every 5 min idle (floor).
            _idle_sec = time.time() - (self._last_global_trade_at or 0)
            if self._last_global_trade_at == 0:
                # Cold start — give the bot the base threshold.
                self.score_threshold = self.score_threshold_base
            elif _idle_sec >= 600:    # 10 min
                self.score_threshold = max(self.score_threshold_floor,
                                           self.score_threshold_base - 20)
            elif _idle_sec >= 300:    # 5 min
                self.score_threshold = max(self.score_threshold_floor,
                                           self.score_threshold_base - 10)
            else:
                self.score_threshold = self.score_threshold_base

            # Rank summary log — one line per cycle covering all symbols.
            _rank_bits = " ".join(
                f"{c['symbol'].replace('USDT','')}={c.get('score',0)}"
                f"({c.get('signal','?')[0]})"
                for c in cands)

            # FINAL STABLE MODE — SIMPLE ENTRY RULE: a candidate qualifies on a
            # directional signal AND (score>=threshold OR AI confidence>=floor).
            # Either path is enough — we do NOT require both. Strategy HOLD is
            # the only hard block (no direction to trade).
            # NOTE: score>0 is REQUIRED on both paths. The weighted engine is the
            # canonical edge gate; score==0 means either no weighted edge or a
            # hard veto fired (worker fails closed → HOLD/score=0). Without this,
            # a candidate could qualify on AI confidence alone with zero weighted
            # conviction and place a LIVE order unprotected.
            qualified = [c for c in cands
                         if c.get("signal") in ("BUY", "SELL")
                         and int(c.get("score", 0)) > 0
                         and (int(c.get("score", 0))      >= self.score_threshold
                              or int(c.get("confidence", 0)) >= self.confidence_floor)]
            qualified.sort(key=lambda c: (int(c.get("score", 0)),
                                          int(c.get("confidence", 0))),
                           reverse=True)

            # GPT = GLOBAL ANALYST (hard edge filter, not just tiebreak).
            # SMART AI SCALPING BOT: every cycle with ≥1 qualified candidate,
            # build a full per-symbol payload (BTC + ETH + SOL — ALL three,
            # not just qualified ones, so GPT can compare) and ask the
            # analyst for a single decision. Throttled+cached inside
            # analyze_global() at 10s so the 2s loop isn't blocked.
            #
            # Trade ONLY if:
            #   - GPT returns action=TRADE
            #   - GPT's symbol == our score winner
            #   - GPT's direction == our winner's signal
            #   - GPT's probability >= gpt_prob_floor (55)
            #
            # On transient GPT outage (None) → fall back to pure-score winner
            # (don't kill trading on an API hiccup).
            winner    = None
            gpt_block = ""
            gpt_pick  = None       # for state/dashboard
            if qualified:
                winner = qualified[0]
                try:
                    from gpt_advisor import get_advisor as _ga
                    _adv = _ga()
                    if _adv and getattr(_adv, "_enabled", False):
                        # Build payload for ALL three symbols (richer context).
                        _by_sym = {c.get("symbol"): c for c in cands}
                        _global_payload = []
                        for _s, _c in _by_sym.items():
                            _global_payload.append({
                                "symbol":     _s,
                                "signal":     _c.get("signal"),
                                "regime":     _c.get("regime"),
                                "score":      int(_c.get("score") or 0),
                                "confidence": int(_c.get("confidence") or 0),
                                "price":      _c.get("price"),
                                "breakdown":  _c.get("breakdown") or {},
                            })
                        _gd = _adv.analyze_global(_global_payload)
                        if _gd is None:
                            pass  # throttled / outage → fall back to score winner
                        else:
                            gpt_pick = _gd
                            _act  = _gd.get("action", "NO_TRADE")
                            _gsym = _gd.get("symbol", "NONE")
                            _gdir = _gd.get("direction", "NONE")
                            _gprob = int(_gd.get("probability", 0))
                            # FINAL STABLE MODE: GPT is ADVISORY. It may reject
                            # ONLY an obviously-bad setup — i.e. it explicitly
                            # flags OUR winner symbol as NO_TRADE. Symbol /
                            # direction disagreement or a low probability NO
                            # LONGER vetoes the trade (GPT must not block all
                            # trades). Local AI confidence + score already
                            # qualified the winner.
                            if _act == "NO_TRADE" and _gsym == winner["symbol"]:
                                gpt_block = (f"GPT NO_TRADE on winner "
                                             f"{_gsym} "
                                             f"({_gd.get('reason','')[:80]})")
                                winner = None
                            else:
                                print(f"[GPT] advisory {_act} pick={_gsym} "
                                      f"{_gdir} prob={_gprob} risk="
                                      f"{_gd.get('risk_level','?')} → "
                                      f"proceeding with {winner['symbol']} "
                                      f"{winner.get('signal')} "
                                      f"({_gd.get('reason','')[:60]})",
                                      flush=True)
                except Exception as _re:
                    print(f"[GPT] analyst skipped: {_re}", flush=True)

            # Phase C: execute winner if throttle clear + cap not hit.
            now_ts        = time.time()
            secs_since    = now_ts - (self._last_global_trade_at or 0)
            throttle_left = max(0, self.global_throttle_sec - int(secs_since))
            cap           = self.global_risk.settings.max_open_trades_total

            if not cands:
                print(f"[RANK] (no candidates yet) threshold={self.score_threshold} "
                      f"throttle={throttle_left}s open={len(all_open)}/{cap}",
                      flush=True)
            elif winner is None:
                _why = (f"GPT veto: {gpt_block}" if gpt_block
                        else f"no candidate met score≥{self.score_threshold} "
                             f"OR conf≥{self.confidence_floor}")
                print(f"[RANK] {_rank_bits} → HOLD ({_why}) "
                      f"throttle={throttle_left}s open={len(all_open)}/{cap}",
                      flush=True)
            elif len(all_open) >= cap:
                print(f"[RANK] {_rank_bits} → SKIP "
                      f"(max_open {len(all_open)}/{cap}) winner={winner['symbol']}",
                      flush=True)
            elif throttle_left > 0:
                print(f"[RANK] {_rank_bits} → THROTTLED "
                      f"({throttle_left}s left) winner={winner['symbol']} "
                      f"score={winner['score']}",
                      flush=True)
            else:
                print(f"[RANK] {_rank_bits} → WINNER={winner['symbol']} "
                      f"score={winner['score']} threshold={self.score_threshold}",
                      flush=True)
                # Re-snapshot once more before the actual order.
                all_open, daily_pct = _refresh_global_state()
                def global_gate(invest: float, sym: str,
                                _all=all_open, _dp=daily_pct):
                    return self.global_risk.check_global(
                        all_open_trades = _all,
                        new_invest_usdt = invest,
                        new_symbol      = sym,
                        daily_loss_pct  = _dp,
                    )
                key = f"{winner.get('exchange','')}:{winner['symbol']}"
                w   = self.workers.get(key)
                if w is None:
                    # Fallback: lookup by symbol only.
                    for _k, _w in self.workers.items():
                        if _w.symbol == winner["symbol"]:
                            w = _w; break
                # ── AUTO-DISABLE GATE ────────────────────────────────────────
                # Refuse to auto-trade a (strategy, timeframe) that no research
                # run has ACCEPTED. Default-safe: unknown/unvalidated → block.
                # This is the honest enforcement of "no proven after-fee edge →
                # no auto-trading". Manual trades bypass this entirely.
                if w is not None and self.require_validation:
                    _allowed, _entry = (False, None)
                    if _is_strategy_validated is not None:
                        try:
                            _allowed, _entry = _is_strategy_validated(
                                w.strategy, w.interval, w.symbol)
                        except Exception:
                            _allowed = False
                    if not _allowed:
                        _key = f"{w.strategy}@{w.interval}"
                        if self._validation_block_logged != _key:
                            self._validation_block_logged = _key
                            log_activity("WARNING",
                                f"🔒 AUTO-DISABLED: '{w.strategy}' @ {w.interval} "
                                f"has no validated after-fee edge — bot will NOT "
                                f"auto-trade it. Run research.py to validate, or "
                                f"set ALPHATRADE_ALLOW_UNVALIDATED=1 to override. "
                                f"Manual trades still allowed.")
                        print(f"[GATE] {_key} not validated → SKIP auto-entry "
                              f"({winner['symbol']})", flush=True)
                        w = None
                if w is not None:
                    try:
                        # Stamp the effective score threshold used to qualify
                        # this winner (anti-idle may have lowered it) so the
                        # sizing gate uses the SAME floor — never qualify here
                        # then block at sizing.
                        winner["score_threshold"] = int(self.score_threshold)
                        # Stamp the bot spending limit + current open exposure so
                        # execute_entry() can size the trade DOWN to fit the
                        # operator's fixed-$ budget (0 = unlimited). Uses the same
                        # all_open snapshot as the global gate for consistency.
                        winner["bot_budget_usdt"]   = float(
                            self.global_risk.settings.max_total_exposure_usdt)
                        winner["bot_exposure_usdt"] = sum(
                            (t.get("invested") or 0) for t in all_open)
                        w.execute_entry(winner, all_open, global_gate)
                    except Exception as exc:
                        log_activity("ERROR",
                            f"execute_entry({winner['symbol']}) crashed: {exc}")

            # Did any worker execute a trade this cycle?
            _after_count = sum(w._session_trades for w in self.workers.values())
            _traded_this_cycle = _after_count > _before_count
            if _traded_this_cycle:
                self._last_trade_executed_at = datetime.now()
                self._last_global_trade_at   = time.time()

            # ── DECISION JOURNAL (observability only) ────────────────────
            # Record WHY each symbol did/didn't trade this cycle so the
            # dashboard "WHY NO TRADE?" panel and the Top-10 report are exact.
            try:
                _cand_by_sym = {c.get("symbol"): c for c in cands}
                _snaps = []
                for _w in self.workers.values():
                    _c = _cand_by_sym.get(_w.symbol) or (_w._last_eval or {})
                    _snaps.append({
                        "symbol":       _w.symbol,
                        "signal":       _c.get("signal"),
                        "score":        int(_c.get("score") or 0),
                        "confidence":   int(_c.get("confidence") or 0),
                        "regime":       _c.get("regime") or "",
                        "worker_block": _w._last_block_reason or "",
                    })
                # The symbol that reached the FINAL gate stage this cycle. When
                # GPT vetoes, `winner` is nulled but the top qualified candidate
                # is still the one that was selected — attribute the GPT/cap/
                # throttle reason to it (not "awaiting selection").
                _selected_sym = (winner or {}).get("symbol")
                if _selected_sym is None and gpt_block and qualified:
                    _selected_sym = qualified[0].get("symbol")
                diagnostics.record_cycle(
                    snaps=_snaps,
                    score_threshold=int(self.score_threshold),
                    confidence_floor=int(self.confidence_floor),
                    winner_symbol=_selected_sym,
                    throttle_sec=int(self.global_throttle_sec),
                    last_global_trade_at=float(self._last_global_trade_at or 0),
                    n_open=len(all_open),
                    cap=int(self.global_risk.settings.max_open_trades_total),
                    gpt_block=gpt_block or "",
                    traded=_traded_this_cycle,
                )
            except Exception as _de:
                print(f"[DIAG] journal record failed: {_de}", flush=True)

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
    manage_manual_trades:  bool = True,         # ON = bot manages (TP/SL/exit) manual trades
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
                # on_candidate is bound in TradingBot.__init__ after workers
                # dict is built — leave None here.
                on_candidate      = None,
                ai_assist         = ai_assist,
                ai_aggressiveness = ai_aggressiveness,
                manage_manual_trades = manage_manual_trades,
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
