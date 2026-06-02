"""Aggressive Mode — operator-selectable trading intensity profiles.

Four modes (least → most intense): Conservative, Balanced, Aggressive,
Very Aggressive. The selected mode tunes ONLY "knobs" that change how *often*
and how *large* the bot trades:

  • confidence_floor          — AI-confidence acceptance threshold
  • score_threshold_base/floor — signal acceptance thresholds
  • gpt_prob_floor            — GPT advisory acceptance threshold
  • global_throttle_sec       — min seconds between any two trades (frequency)
  • cooldown_seconds          — per-symbol re-entry timing
  • dynamic_size_pct          — requested position size (% of free USDT)
  • check_every               — watchlist / scan tick interval (seconds)

It must NEVER touch the safety layer. Applying a profile does not modify:
strategy validation / allowlist gate (`TradingBot.require_validation`,
`research.is_strategy_validated`), GlobalRiskSettings (spending limits, max open
trades, daily loss, emergency stop), per-symbol emergency_stop / max_open_trades,
safe mode, or any exchange safety check. Aggressive sizing is only a *request*:
`execute_entry()` still floors at the $10 min-notional and caps at free×0.75, and
`GlobalRiskManager.check_global()` still enforces every spending/exposure cap.

Persistence is in PostgreSQL (survives restart / redeploy / refresh) via
`DATABASE_URL`. Every change is appended to an audit table. If the database is
unavailable, reads fall back to the safe default (Balanced) and never raise.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

try:
    import psycopg2
    import psycopg2.extras
    _PG_OK = True
except Exception:                                    # pragma: no cover
    psycopg2 = None                                  # type: ignore
    _PG_OK = False


# ── Modes & profiles ─────────────────────────────────────────────────────────
CONSERVATIVE    = "Conservative"
BALANCED        = "Balanced"
AGGRESSIVE      = "Aggressive"
VERY_AGGRESSIVE = "Very Aggressive"

# Ordered least → most aggressive. Used for the UI select and for monotonicity.
MODES: List[str] = [CONSERVATIVE, BALANCED, AGGRESSIVE, VERY_AGGRESSIVE]

DEFAULT_MODE = BALANCED

# Each profile is a flat dict of the tunable knobs only. Confidence floors match
# the operator spec exactly: 85 / 75 / 65 / 55. More aggressive ⇒ lower
# acceptance thresholds, faster cadence, larger requested size.
PROFILES: Dict[str, Dict[str, float]] = {
    CONSERVATIVE: {
        "confidence_floor":     85,
        "score_threshold_base": 75,
        "score_threshold_floor": 65,
        "gpt_prob_floor":       70,
        "global_throttle_sec":  30,
        "cooldown_seconds":     30,
        "dynamic_size_pct":     15.0,
        "check_every":          15,
    },
    BALANCED: {
        "confidence_floor":     75,
        "score_threshold_base": 65,
        "score_threshold_floor": 55,
        "gpt_prob_floor":       60,
        "global_throttle_sec":  15,
        "cooldown_seconds":     15,
        "dynamic_size_pct":     25.0,
        "check_every":          8,
    },
    AGGRESSIVE: {
        "confidence_floor":     65,
        "score_threshold_base": 55,
        "score_threshold_floor": 45,
        "gpt_prob_floor":       55,
        "global_throttle_sec":  8,
        "cooldown_seconds":     8,
        "dynamic_size_pct":     40.0,
        "check_every":          4,
    },
    VERY_AGGRESSIVE: {
        "confidence_floor":     55,
        "score_threshold_base": 50,
        "score_threshold_floor": 40,
        "gpt_prob_floor":       50,
        "global_throttle_sec":  5,
        "cooldown_seconds":     5,
        "dynamic_size_pct":     60.0,
        "check_every":          2,
    },
}

MODE_DESCRIPTIONS: Dict[str, str] = {
    CONSERVATIVE: (
        "Fewest, highest-conviction trades. Requires AI confidence ≥ 85 and the "
        "strongest signals. Smallest size (15% of free USDT), slowest cadence "
        "(15s scan, 30s between trades)."
    ),
    BALANCED: (
        "Balanced frequency and size. AI confidence ≥ 75. 25% sizing, 8s scan, "
        "15s between trades. Sensible default."
    ),
    AGGRESSIVE: (
        "More opportunities. AI confidence ≥ 65, lower signal bar. 40% sizing, "
        "4s scan, 8s between trades."
    ),
    VERY_AGGRESSIVE: (
        "Maximum opportunities and largest allowed size. AI confidence ≥ 55, "
        "lowest signal bar. 60% requested sizing (still capped by your risk "
        "limits), 2s scan, 5s between trades."
    ),
}


def normalize_mode(mode: Optional[str]) -> str:
    """Return a valid mode name, falling back to the safe default."""
    if isinstance(mode, str):
        for m in MODES:
            if m.lower() == mode.strip().lower():
                return m
    return DEFAULT_MODE


def get_profile(mode: Optional[str]) -> Dict[str, float]:
    """Return a copy of the knob profile for `mode` (safe default if unknown)."""
    return dict(PROFILES[normalize_mode(mode)])


# ── PostgreSQL persistence ───────────────────────────────────────────────────
# Table names are module-level so tests can point them at scratch tables.
TABLE_MODE  = "trading_aggressive_mode"
TABLE_AUDIT = "trading_aggressive_mode_audit"


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
        print(f"[AGGRO] DB connect failed: {e}", flush=True)
        return None


def ensure_tables() -> bool:
    """Create the mode + audit tables if missing. Returns True on success."""
    conn = _conn()
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_MODE} (
                    id         INTEGER PRIMARY KEY DEFAULT 1,
                    mode       TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT {TABLE_MODE}_singleton CHECK (id = 1)
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_AUDIT} (
                    id         SERIAL PRIMARY KEY,
                    old_mode   TEXT,
                    new_mode   TEXT NOT NULL,
                    actor      TEXT,
                    note       TEXT,
                    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        return True
    except Exception as e:                            # pragma: no cover
        print(f"[AGGRO] ensure_tables failed: {e}", flush=True)
        return False
    finally:
        conn.close()


def get_mode() -> str:
    """Read the persisted mode. Safe default (Balanced) if DB/row missing."""
    conn = _conn()
    if conn is None:
        return DEFAULT_MODE
    try:
        ensure_tables()
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT mode FROM {TABLE_MODE} WHERE id = 1")
            row = cur.fetchone()
        if row and row[0]:
            return normalize_mode(row[0])
        return DEFAULT_MODE
    except Exception as e:
        print(f"[AGGRO] get_mode failed: {e}", flush=True)
        return DEFAULT_MODE
    finally:
        conn.close()


def set_mode(mode: str, actor: str = "dashboard", note: str = "") -> bool:
    """Persist `mode` (upsert single row) and append an audit entry.

    Returns True on success. Invalid mode names are rejected (returns False).
    """
    norm = normalize_mode(mode)
    if isinstance(mode, str) and mode.strip().lower() not in (m.lower() for m in MODES):
        print(f"[AGGRO] set_mode rejected unknown mode={mode!r}", flush=True)
        return False
    conn = _conn()
    if conn is None:
        return False
    try:
        ensure_tables()
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT mode FROM {TABLE_MODE} WHERE id = 1")
            row = cur.fetchone()
            old = normalize_mode(row[0]) if row and row[0] else None
            cur.execute(
                f"""
                INSERT INTO {TABLE_MODE} (id, mode, updated_at)
                VALUES (1, %s, now())
                ON CONFLICT (id) DO UPDATE
                    SET mode = EXCLUDED.mode, updated_at = now()
                """,
                (norm,),
            )
            cur.execute(
                f"""
                INSERT INTO {TABLE_AUDIT} (old_mode, new_mode, actor, note)
                VALUES (%s, %s, %s, %s)
                """,
                (old, norm, actor, note or None),
            )
        print(f"[AGGRO] mode set {old} → {norm} by {actor}", flush=True)
        return True
    except Exception as e:
        print(f"[AGGRO] set_mode failed: {e}", flush=True)
        return False
    finally:
        conn.close()


def get_audit_log(limit: int = 50) -> List[Dict]:
    """Return recent mode-change audit rows, newest first."""
    conn = _conn()
    if conn is None:
        return []
    try:
        ensure_tables()
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                f"""
                SELECT old_mode, new_mode, actor, note, changed_at
                FROM {TABLE_AUDIT}
                ORDER BY id DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[AGGRO] get_audit_log failed: {e}", flush=True)
        return []
    finally:
        conn.close()


# ── Apply a profile to a running bot ─────────────────────────────────────────
# SAFETY: this function sets ONLY frequency/threshold/size knobs. It must never
# read or write any validation/allowlist/risk-cap/safe-mode attribute. The tests
# in test_aggressive_mode.py assert these invariants.
_BOT_KNOBS = (
    "score_threshold_base",
    "score_threshold",
    "score_threshold_floor",
    "confidence_floor",
    "gpt_prob_floor",
    "global_throttle_sec",
)


def apply_profile_to_bot(bot, mode: str) -> Dict[str, float]:
    """Apply `mode`'s knobs to a live TradingBot and its workers.

    Returns the applied profile. Does NOT touch validation, allowlist, global
    risk caps, emergency stops, or any safety gate.
    """
    p = get_profile(mode)
    if bot is None:
        return p

    bot.score_threshold_base  = int(p["score_threshold_base"])
    bot.score_threshold       = int(p["score_threshold_base"])
    bot.score_threshold_floor = int(p["score_threshold_floor"])
    bot.confidence_floor      = int(p["confidence_floor"])
    bot.gpt_prob_floor        = int(p["gpt_prob_floor"])
    bot.global_throttle_sec   = int(p["global_throttle_sec"])
    if hasattr(bot, "check_every"):
        bot.check_every = int(p["check_every"])

    # Per-symbol knobs: requested size + re-entry cooldown only.
    for w in getattr(bot, "workers", {}).values():
        rm = getattr(w, "risk", None)
        s  = getattr(rm, "settings", None)
        if s is not None:
            s.dynamic_size_pct = float(p["dynamic_size_pct"])
            s.cooldown_seconds = int(p["cooldown_seconds"])
    return p


def apply_profile_to_risk(settings, mode: str) -> None:
    """Apply size/cooldown knobs to a SymbolRiskSettings before bot creation."""
    p = get_profile(mode)
    if settings is None:
        return
    settings.dynamic_size_pct = float(p["dynamic_size_pct"])
    settings.cooldown_seconds = int(p["cooldown_seconds"])
