"""Live trading settings for the 20-Minute Dip strategy — PostgreSQL persisted.

Mirrors the persistence pattern of `aggressive_mode.py`:

  • A single-row settings table (id = 1) holding a JSONB blob of every
    operator-tunable trading setting.
  • An append-only audit table recording each change (old/new blob + actor).
  • A per-symbol cooldown table holding UTC-aware timestamps for the
    30-minute stop-loss cooldown and the faster post-profit re-entry timing.

Rules:
  • Settings are LOADED on startup. Defaults are returned ONLY when the table
    is empty / unavailable — saved values are NEVER overwritten with defaults.
  • Every read falls back to a safe in-memory default and never raises, so a
    DB outage can never crash the trading loop.

All timestamps stored as TIMESTAMPTZ ⇒ psycopg2 returns tz-aware datetimes,
which keeps every cooldown comparison offset-aware (fixes the
"can't compare offset-naive and offset-aware datetimes" crash).
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    import psycopg2
    import psycopg2.extras
    _PG_OK = True
except Exception:                                    # pragma: no cover
    psycopg2 = None                                  # type: ignore
    _PG_OK = False


# ── Size modes ───────────────────────────────────────────────────────────────
SIZE_AUTO = "AUTO"                       # bot picks a % of free USDT
SIZE_FIXED = "FIXED_USDT"                # fixed dollar amount per trade
SIZE_PERCENT = "PORTFOLIO_PERCENT"       # % of free USDT per trade
SIZE_ALL = "ALL_AVAILABLE"               # deploy all free USDT (safety caps still apply)
SIZE_MODES: List[str] = [SIZE_AUTO, SIZE_FIXED, SIZE_PERCENT, SIZE_ALL]


def normalize_size_mode(mode: Optional[str]) -> str:
    if isinstance(mode, str):
        m = mode.strip().upper()
        for valid in SIZE_MODES:
            if valid == m:
                return valid
    return SIZE_AUTO


@dataclass
class LiveSettings:
    """Every operator-tunable LIVE trading setting. Defaults match the spec."""
    # Strategy thresholds (defaults = the FINAL TRADING RULE)
    buy_threshold_pct: float = -0.20     # BUY when 20m change ≤ this
    take_profit_pct: float = 1.00        # SELL when profit ≥ this
    stop_loss_pct: float = -0.30         # STOP-LOSS when loss ≤ this
    lookback_minutes: int = 20

    # Entry-quality filters (part of the BUY criteria)
    volume_filter_on: bool = True          # require a volume spike to BUY
    min_volume_multiple: float = 0.2       # last-candle vol ≥ this × avg
    trend_filter_on: bool = False          # require a short-term upturn to BUY

    # Position sizing
    size_mode: str = SIZE_AUTO
    fixed_usdt_amount: float = 25.0      # used when size_mode = FIXED_USDT
    portfolio_percent: float = 25.0      # used when size_mode = PORTFOLIO_PERCENT
    auto_percent: float = 20.0           # base % used when size_mode = AUTO

    # Spending / size limits (0 = disabled / unlimited)
    bot_spending_limit_usdt: float = 0.0   # max total USDT the bot may deploy
    max_position_size_usdt: float = 0.0    # hard $ cap on a single trade
    max_position_pct: float = 50.0         # cap a single trade at this % of free USDT
    min_trade_size_usdt: float = 10.0      # floor (Binance min-notional ~ $10)

    # Behavior toggles
    aggressive_on: bool = True             # aggressive default ON (spec)
    safe_mode: bool = False                # operator freeze (no new entries)

    # Cooldowns (seconds) — FINAL RULE: 1 minute after a stop-loss AND 1 minute
    # after a (profitable) sell before re-entering the same symbol.
    stop_loss_cooldown_sec: int = 60       # 1 minute after a stop-loss
    reentry_cooldown_sec: int = 60         # 1 minute after any sell

    def normalized(self) -> "LiveSettings":
        self.size_mode = normalize_size_mode(self.size_mode)
        return self

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "LiveSettings":
        """Build from a (partial) dict — unknown keys ignored, missing keys use
        the dataclass default. Never raises on a malformed blob."""
        if not isinstance(data, dict):
            return cls()
        valid = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in valid}
        try:
            return cls(**kwargs).normalized()
        except Exception:
            return cls()

    def to_dict(self) -> dict:
        return asdict(self)


def default_settings() -> LiveSettings:
    return LiveSettings()


# ── PostgreSQL persistence ───────────────────────────────────────────────────
# Table names are module-level so tests can point them at scratch tables.
TABLE_SETTINGS = "trading_live_settings"
TABLE_AUDIT = "trading_live_settings_audit"
TABLE_COOLDOWN = "trading_dip_cooldown"


def _dsn() -> Optional[str]:
    return os.environ.get("DATABASE_URL") or None


def db_available() -> bool:
    return bool(_PG_OK and _dsn())


def _conn():
    if not db_available():
        return None
    try:
        return psycopg2.connect(_dsn())              # type: ignore[union-attr]
    except Exception as e:                            # pragma: no cover
        print(f"[LIVE-SETTINGS] DB connect failed: {e}", flush=True)
        return None


def ensure_tables() -> bool:
    """Create settings + audit + cooldown tables if missing."""
    conn = _conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_SETTINGS} (
                    id         INTEGER PRIMARY KEY DEFAULT 1,
                    data       JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT {TABLE_SETTINGS}_singleton CHECK (id = 1)
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_AUDIT} (
                    id         SERIAL PRIMARY KEY,
                    old_data   JSONB,
                    new_data   JSONB NOT NULL,
                    actor      TEXT,
                    note       TEXT,
                    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_COOLDOWN} (
                    symbol              TEXT PRIMARY KEY,
                    last_stop_loss_at   TIMESTAMPTZ,
                    last_sell_at        TIMESTAMPTZ,
                    last_sell_profit    BOOLEAN,
                    last_buy_at         TIMESTAMPTZ,
                    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        return True
    except Exception as e:                            # pragma: no cover
        print(f"[LIVE-SETTINGS] ensure_tables failed: {e}", flush=True)
        return False
    finally:
        conn.close()


def get_settings() -> LiveSettings:
    """Load persisted settings. Returns safe defaults when the DB / row is
    missing — and NEVER overwrites the stored row with defaults."""
    conn = _conn()
    if conn is None:
        return default_settings()
    try:
        ensure_tables()
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT data FROM {TABLE_SETTINGS} WHERE id = 1")
            row = cur.fetchone()
        if row and row[0]:
            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return LiveSettings.from_dict(data)
        return default_settings()
    except Exception as e:
        print(f"[LIVE-SETTINGS] get_settings failed: {e}", flush=True)
        return default_settings()
    finally:
        conn.close()


def save_settings(settings: LiveSettings, actor: str = "dashboard",
                  note: str = "") -> bool:
    """Upsert the single settings row and append an audit entry."""
    if not isinstance(settings, LiveSettings):
        return False
    settings = settings.normalized()
    conn = _conn()
    if conn is None:
        return False
    try:
        ensure_tables()
        new_json = json.dumps(settings.to_dict())
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT data FROM {TABLE_SETTINGS} WHERE id = 1")
            row = cur.fetchone()
            old_json = None
            if row and row[0]:
                old_json = json.dumps(row[0]) if isinstance(row[0], dict) else row[0]
            cur.execute(
                f"""
                INSERT INTO {TABLE_SETTINGS} (id, data, updated_at)
                VALUES (1, %s::jsonb, now())
                ON CONFLICT (id) DO UPDATE
                    SET data = EXCLUDED.data, updated_at = now()
                """,
                (new_json,),
            )
            cur.execute(
                f"""
                INSERT INTO {TABLE_AUDIT} (old_data, new_data, actor, note)
                VALUES (%s::jsonb, %s::jsonb, %s, %s)
                """,
                (old_json, new_json, actor, note or None),
            )
        return True
    except Exception as e:
        print(f"[LIVE-SETTINGS] save_settings failed: {e}", flush=True)
        return False
    finally:
        conn.close()


def get_audit_log(limit: int = 50) -> List[Dict]:
    conn = _conn()
    if conn is None:
        return []
    try:
        ensure_tables()
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                f"""
                SELECT old_data, new_data, actor, note, changed_at
                FROM {TABLE_AUDIT}
                ORDER BY id DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[LIVE-SETTINGS] get_audit_log failed: {e}", flush=True)
        return []
    finally:
        conn.close()


# ── Per-symbol cooldown state ────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    """Force a datetime to be UTC-aware (treat naive as UTC)."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


class CooldownStore:
    """PostgreSQL-backed per-symbol cooldown timestamps, with a safe in-memory
    fallback when the database is unavailable. All datetimes are UTC-aware."""

    def __init__(self):
        # In-memory fallback: {symbol: {last_stop_loss_at, last_sell_at,
        #                               last_sell_profit, last_buy_at}}
        self._mem: Dict[str, Dict] = {}

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, symbol: str) -> Dict:
        conn = _conn()
        if conn is None:
            return dict(self._mem.get(symbol, {}))
        try:
            ensure_tables()
            with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
                cur.execute(
                    f"""SELECT last_stop_loss_at, last_sell_at, last_sell_profit,
                               last_buy_at
                        FROM {TABLE_COOLDOWN} WHERE symbol = %s""",
                    (symbol,),
                )
                row = cur.fetchone()
            if not row:
                return {}
            return {
                "last_stop_loss_at": _aware(row["last_stop_loss_at"]),
                "last_sell_at": _aware(row["last_sell_at"]),
                "last_sell_profit": row["last_sell_profit"],
                "last_buy_at": _aware(row["last_buy_at"]),
            }
        except Exception as e:
            print(f"[LIVE-SETTINGS] cooldown get failed: {e}", flush=True)
            return dict(self._mem.get(symbol, {}))
        finally:
            conn.close()

    # ── writes ───────────────────────────────────────────────────────────────
    def _set(self, symbol: str, column: str, value, extra: Optional[dict] = None):
        now = _utcnow()
        # Update in-memory mirror first (always succeeds).
        m = self._mem.setdefault(symbol, {})
        m[column] = value
        if extra:
            m.update(extra)
        conn = _conn()
        if conn is None:
            return
        try:
            ensure_tables()
            cols = {column: value}
            if extra:
                cols.update(extra)
            set_cols = ", ".join(f"{c} = %s" for c in cols)
            ins_cols = ", ".join(cols.keys())
            ins_vals = ", ".join(["%s"] * len(cols))
            params = list(cols.values())
            with conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TABLE_COOLDOWN} (symbol, {ins_cols}, updated_at)
                    VALUES (%s, {ins_vals}, now())
                    ON CONFLICT (symbol) DO UPDATE
                        SET {set_cols}, updated_at = now()
                    """,
                    [symbol] + params + params,
                )
        except Exception as e:
            print(f"[LIVE-SETTINGS] cooldown set failed: {e}", flush=True)
        finally:
            conn.close()

    def record_stop_loss(self, symbol: str):
        self._set(symbol, "last_stop_loss_at", _utcnow())

    def record_sell(self, symbol: str, profitable: bool):
        self._set(symbol, "last_sell_at", _utcnow(),
                  extra={"last_sell_profit": bool(profitable)})

    def record_buy(self, symbol: str):
        self._set(symbol, "last_buy_at", _utcnow())

    def clear(self, symbol: str):
        self._mem.pop(symbol, None)
        conn = _conn()
        if conn is None:
            return
        try:
            ensure_tables()
            with conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {TABLE_COOLDOWN} WHERE symbol = %s",
                            (symbol,))
        except Exception as e:
            print(f"[LIVE-SETTINGS] cooldown clear failed: {e}", flush=True)
        finally:
            conn.close()


# Module-level singleton cooldown store (used by the live engine).
_cooldown_store: Optional[CooldownStore] = None


def cooldown_store() -> CooldownStore:
    global _cooldown_store
    if _cooldown_store is None:
        _cooldown_store = CooldownStore()
    return _cooldown_store
