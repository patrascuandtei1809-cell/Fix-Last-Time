"""Multi-exchange opportunity scanner (READ-ONLY public market data).

Scans the full USDT-spot universe on Binance + MEXC, filters out junk
(illiquid / dead / dangerously volatile / extreme pump-dump / stablecoins /
leveraged tokens), scores what's left, and writes the top N to
``data/multi_exchange_opportunities.json`` for the bot to consume.

It NEVER places orders and needs NO API keys — only public 24h tickers.

Binance public reads go through ``data-api.binance.vision`` (works on Replit,
where ``api.binance.com`` is geo-blocked 451, AND on the production droplet).
Each exchange fetch degrades gracefully: if one venue is unreachable the scan
still returns results from the other.

The filter + scoring functions are PURE (no network) so they can be unit
tested with synthetic tickers.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

_BINANCE_PUBLIC = "https://data-api.binance.vision/api/v3"
_MEXC_PUBLIC = "https://api.mexc.com/api/v3"

_OUT_PATH = Path(__file__).resolve().parent / "data" / "multi_exchange_opportunities.json"

# Assets that are stablecoins / fiat — exclude as a BASE asset (no edge, no move).
_STABLE_FIAT = {
    "USDT", "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "USDD", "PYUSD",
    "GUSD", "USTC", "EUR", "GBP", "AUD", "TRY", "BRL", "RUB", "JPY", "EURI",
    "XUSD", "USD1", "USDE", "USDS",
}
# Leveraged / structured tokens — exclude (decay, not spot exposure).
_LEVERAGED_RE = re.compile(r"(\d+[LS]|UP|DOWN|BULL|BEAR)$")
# A tradeable base must be plain uppercase alphanumerics (drops oddities like
# non-ASCII vanity listings that leak through the public ticker feed).
_VALID_BASE_RE = re.compile(r"^[A-Z0-9]+$")


@dataclass
class ScanConfig:
    min_quote_volume: float = 2_000_000.0   # 24h liquidity floor (USDT)
    target_quote_volume: float = 150_000_000.0  # full-marks liquidity
    min_volatility_pct: float = 3.0         # below = too dead to scalp
    max_volatility_pct: float = 120.0       # above = dangerous
    min_24h_change_pct: float = -35.0       # below = falling knife
    max_24h_change_pct: float = 60.0        # above = blow-off pump
    max_spread_pct: float = 0.8             # wide spread = bad fills
    ideal_volatility_pct: float = 15.0      # volatility sweet-spot peak
    top_n: int = 15
    quotes: Tuple[str, ...] = ("USDT",)


# ── pure: normalisation ──────────────────────────────────────────────────────
def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_ticker(raw: dict, exchange: str) -> Optional[dict]:
    """Map a raw 24h ticker dict into the scanner's common shape.

    ``priceChangePercent`` is already a PERCENT on Binance but a FRACTION on
    MEXC, so MEXC is ×100. Returns None if the row is unusable."""
    symbol = raw.get("symbol")
    if not symbol:
        return None
    price = _f(raw.get("lastPrice"))
    high = _f(raw.get("highPrice"))
    low = _f(raw.get("lowPrice"))
    qv = _f(raw.get("quoteVolume"))
    chg = _f(raw.get("priceChangePercent"))
    if exchange == "mexc":
        chg *= 100.0
    bid = _f(raw.get("bidPrice"))
    ask = _f(raw.get("askPrice"))
    volatility = ((high - low) / low * 100.0) if low > 0 else 0.0
    spread = ((ask - bid) / bid * 100.0) if (bid > 0 and ask > 0) else None
    return {
        "exchange": exchange,
        "symbol": symbol,
        "price": price,
        "volume": qv,           # quote (USDT) 24h volume = liquidity
        "volatility": volatility,
        "change": chg,
        "spread": spread,
    }


def _base_asset(symbol: str, quotes: Tuple[str, ...]) -> Optional[str]:
    for q in quotes:
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return None


# ── pure: filtering ──────────────────────────────────────────────────────────
def filter_reason(m: dict, cfg: ScanConfig) -> Optional[str]:
    """Return a rejection reason, or None if the candidate passes all filters."""
    base = _base_asset(m["symbol"], cfg.quotes)
    if base is None:
        return "non-USDT quote"
    if not _VALID_BASE_RE.match(base):
        return "non-standard symbol"
    if base in _STABLE_FIAT:
        return "stablecoin/fiat base"
    if _LEVERAGED_RE.search(base):
        return "leveraged token"
    if m["price"] <= 0:
        return "no price"
    if m["volume"] < cfg.min_quote_volume:
        return f"illiquid (${m['volume']:,.0f} < ${cfg.min_quote_volume:,.0f})"
    if m["volatility"] < cfg.min_volatility_pct:
        return f"too flat ({m['volatility']:.1f}%)"
    if m["volatility"] > cfg.max_volatility_pct:
        return f"too volatile ({m['volatility']:.0f}%)"
    if m["change"] < cfg.min_24h_change_pct:
        return f"dumping ({m['change']:.0f}%)"
    if m["change"] > cfg.max_24h_change_pct:
        return f"pumping ({m['change']:.0f}%)"
    if m["spread"] is not None and m["spread"] > cfg.max_spread_pct:
        return f"wide spread ({m['spread']:.2f}%)"
    return None


# ── pure: scoring ────────────────────────────────────────────────────────────
def score_opportunity(m: dict, cfg: ScanConfig) -> Tuple[float, str]:
    """Score 0-100 from liquidity (40) + volatility sweet-spot (30) +
    momentum (20) + spread (10). Returns (score, human reason)."""
    # Liquidity — log-scaled between floor and target.
    lo, hi = math.log10(cfg.min_quote_volume), math.log10(cfg.target_quote_volume)
    qv = max(m["volume"], 1.0)
    liq = (math.log10(qv) - lo) / (hi - lo) if hi > lo else 0.0
    liq_score = max(0.0, min(1.0, liq)) * 40.0

    # Volatility — triangular peak at ideal, 0 at the configured bounds.
    v = m["volatility"]
    ideal = cfg.ideal_volatility_pct
    if v <= ideal:
        span = max(ideal - cfg.min_volatility_pct, 1e-9)
        vol = (v - cfg.min_volatility_pct) / span
    else:
        span = max(cfg.max_volatility_pct - ideal, 1e-9)
        vol = (cfg.max_volatility_pct - v) / span
    vol_score = max(0.0, min(1.0, vol)) * 30.0

    # Momentum — positive 24h change rewarded up to +20%, negatives muted.
    chg = m["change"]
    mom = max(0.0, min(1.0, chg / 20.0)) if chg > 0 else 0.0
    mom_score = mom * 20.0

    # Spread — tighter is better; unknown spread → neutral.
    sp = m["spread"]
    if sp is None:
        sp_score = 5.0
    else:
        sp_score = max(0.0, min(1.0, 1.0 - sp / cfg.max_spread_pct)) * 10.0

    total = round(liq_score + vol_score + mom_score + sp_score, 1)
    reason = (f"liq {liq_score:.0f}/40 · vol {vol_score:.0f}/30 "
              f"({v:.1f}%) · mom {mom_score:.0f}/20 ({chg:+.1f}%) · "
              f"spr {sp_score:.0f}/10")
    return total, reason


# ── network (graceful) ───────────────────────────────────────────────────────
def _fetch_24h(url: str, label: str) -> List[dict]:
    try:
        r = requests.get(f"{url}/ticker/24hr", timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[SCANNER][WARN] {label} 24h fetch failed: {e}", flush=True)
        return []


def fetch_all_tickers() -> Dict[str, List[dict]]:
    return {
        "binance": _fetch_24h(_BINANCE_PUBLIC, "binance"),
        "mexc": _fetch_24h(_MEXC_PUBLIC, "mexc"),
    }


# ── pure: ranking over already-fetched tickers ───────────────────────────────
def rank(raw_by_exchange: Dict[str, List[dict]],
         cfg: Optional[ScanConfig] = None) -> List[dict]:
    """Normalise → filter → score → sort. Pure (no network)."""
    cfg = cfg or ScanConfig()
    scored: List[dict] = []
    for exch, rows in raw_by_exchange.items():
        for raw in rows:
            m = normalize_ticker(raw, exch)
            if m is None:
                continue
            if filter_reason(m, cfg) is not None:
                continue
            sc, reason = score_opportunity(m, cfg)
            m["score"] = sc
            m["reason"] = reason
            scored.append(m)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ── orchestration ────────────────────────────────────────────────────────────
def scan(cfg: Optional[ScanConfig] = None, write: bool = True) -> dict:
    """Run a full live scan and (optionally) persist the top N.

    Returns the payload dict: {updated_at, config, count_universe, opportunities}.
    """
    cfg = cfg or ScanConfig()
    raw = fetch_all_tickers()
    ranked = rank(raw, cfg)
    top = ranked[: cfg.top_n]
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "min_quote_volume": cfg.min_quote_volume,
            "min_volatility_pct": cfg.min_volatility_pct,
            "max_volatility_pct": cfg.max_volatility_pct,
            "min_24h_change_pct": cfg.min_24h_change_pct,
            "max_24h_change_pct": cfg.max_24h_change_pct,
            "top_n": cfg.top_n,
        },
        "count_scored": len(ranked),
        "opportunities": top,
    }
    if write:
        _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _OUT_PATH.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(_OUT_PATH)
        print(f"[SCANNER] wrote {len(top)} opportunities → {_OUT_PATH.name} "
              f"(scored {len(ranked)})", flush=True)
    return payload


def load_opportunities(exchange: Optional[str] = None) -> List[dict]:
    """Read the persisted scan output. Optionally filter to one exchange
    (used by the bot to scope active_symbols by exchange_mode)."""
    if not _OUT_PATH.exists():
        return []
    try:
        with _OUT_PATH.open() as f:
            data = json.load(f)
        opps = data.get("opportunities", [])
        if exchange:
            opps = [o for o in opps if o.get("exchange") == exchange]
        return opps
    except Exception as e:  # noqa: BLE001
        print(f"[SCANNER][ERROR] failed to read opportunities: {e}", flush=True)
        return []


# ── background cadence daemon ────────────────────────────────────────────────
_daemon_started = False
_daemon_lock = threading.Lock()


def start_scanner_daemon(interval_sec: int = 120,
                         cfg: Optional[ScanConfig] = None,
                         on_log: Optional[Callable[[str, str], None]] = None
                         ) -> bool:
    """Start (once) a daemon thread that re-runs ``scan(write=True)`` on a
    cadence so the bot always trades fresh scanner picks. Idempotent — repeated
    calls are no-ops. Returns True if it started the thread, False if one was
    already running. Each refresh is logged via ``on_log`` (e.g. activity.json).
    """
    global _daemon_started
    with _daemon_lock:
        if _daemon_started:
            return False
        _daemon_started = True

    def _loop():
        while True:
            try:
                payload = scan(cfg=cfg, write=True)
                n = len(payload.get("opportunities", []))
                scored = payload.get("count_scored", 0)
                if on_log:
                    try:
                        on_log("SCAN",
                               f"[SCANNER] refreshed {n} opportunities "
                               f"(scored {scored} across Binance+MEXC)")
                    except Exception:
                        pass
            except Exception as e:  # noqa: BLE001
                print(f"[SCANNER][daemon] scan failed: {e}", flush=True)
            time.sleep(max(15, int(interval_sec)))

    t = threading.Thread(target=_loop, daemon=True, name="alphatrade-scanner")
    t.start()
    print(f"[SCANNER] daemon started (every {interval_sec}s)", flush=True)
    return True


if __name__ == "__main__":
    import sys
    out = scan(write=True)
    print(json.dumps(out["opportunities"][:10], indent=2))
    sys.exit(0)
