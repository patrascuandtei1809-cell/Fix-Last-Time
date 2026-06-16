"""Read-only wallet helpers for the FastAPI dashboard API (Phase D1).

Extracted from dashboard wallet logic — no Streamlit imports.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from dashboard_support import amount_left_to_trade, group_trades_by_exchange

_BINANCE_MAJORS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
_STABLES = frozenset({
    "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI",
})
_FEE_STABLES = frozenset({
    "USDT", "BUSD", "USDC", "FDUSD", "TUSD", "DAI",
})
_MAJORS = _BINANCE_MAJORS
_DUST_USD = 10.0


def _is_legacy_asset(asset: str) -> bool:
    a = (asset or "").upper()
    if not a or a in _FEE_STABLES:
        return False
    return f"{a}USDT" not in _MAJORS


def _price_usdt(asset: str, venue: str = "binance") -> Optional[float]:
    a = (asset or "").upper()
    if a in _STABLES:
        return 1.0
    sym = f"{a}USDT"
    try:
        if venue == "mexc":
            from exchanges.mexc import public_price as _mx_price
            return float(_mx_price(sym))
        from binance_client import public_price
        return float(public_price(sym))
    except Exception:
        return None


def load_binance_client() -> Optional[Any]:
    try:
        from secrets_store import load_credentials
        creds = load_credentials()
    except Exception:
        creds = None
    if not creds:
        return None
    key, secret = creds
    try:
        from binance_client import BinanceClient
    except ImportError:
        return None
    try:
        client = BinanceClient(key, secret)
        ok, _ = client.test_connection()
        if not ok:
            return None
        return client
    except Exception:
        return None


def compute_account_value(client: Any) -> Dict[str, Any]:
    """USDT + live market value of every coin held."""
    bals = client.get_all_balances()
    total = 0.0
    holdings: List[Dict[str, Any]] = []
    unpriced: List[Dict[str, Any]] = []
    for asset, b in bals.items():
        amt = float(b.get("total") or 0)
        if amt <= 0:
            continue
        free = float(b.get("free") or 0)
        locked = float(b.get("locked") or 0)
        if asset in _STABLES:
            val = amt
        else:
            px = _price_usdt(asset, "binance")
            val = (amt * px) if px else 0.0
        if val > 0:
            total += val
            legacy = _is_legacy_asset(asset)
            dust = legacy and val < _DUST_USD
            holdings.append({
                "asset": asset,
                "amount": amt,
                "free": free,
                "locked": locked,
                "value": val,
                "legacy": legacy,
                "dust": dust,
            })
        elif asset not in _STABLES:
            unpriced.append({
                "asset": asset,
                "amount": amt,
                "free": free,
                "locked": locked,
                "legacy": _is_legacy_asset(asset),
            })
    holdings.sort(key=lambda h: h["value"], reverse=True)
    return {"total": total, "holdings": holdings, "unpriced": unpriced}


def fetch_binance_wallet() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    warnings: List[Dict[str, Any]] = []
    client = load_binance_client()
    if client is None:
        warnings.append({
            "code": "binance_not_connected",
            "message": "No Binance credentials or connection failed.",
        })
        return {
            "connected": False,
            "account_value": None,
            "usdt": {"free": 0.0, "locked": 0.0, "total": 0.0},
            "holdings": [],
            "unpriced": [],
            "legacy_count": 0,
            "dust_count": 0,
        }, warnings
    try:
        usdt_bal = client.get_account_balance("USDT")
        av = compute_account_value(client)
        legacy = sum(1 for h in av["holdings"] if h.get("legacy"))
        dust = sum(1 for h in av["holdings"] if h.get("dust"))
        return {
            "connected": True,
            "account_value": av["total"],
            "usdt": {
                "free": float(usdt_bal.get("free") or 0),
                "locked": float(usdt_bal.get("locked") or 0),
                "total": float(usdt_bal.get("total") or 0),
            },
            "holdings": av["holdings"],
            "unpriced": av["unpriced"],
            "legacy_count": legacy,
            "dust_count": dust,
        }, warnings
    except Exception as exc:
        warnings.append({
            "code": "binance_wallet_error",
            "message": str(exc),
        })
        return {
            "connected": False,
            "account_value": None,
            "usdt": {"free": 0.0, "locked": 0.0, "total": 0.0},
            "holdings": [],
            "unpriced": [],
            "legacy_count": 0,
            "dust_count": 0,
        }, warnings


def fetch_mexc_wallet(open_trades: Optional[List[Dict]] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    warnings: List[Dict[str, Any]] = []
    try:
        from exchanges.mexc import (
            MexcClient,
            MexcExchange,
            load_mexc_credentials,
        )
    except Exception as exc:
        warnings.append({
            "code": "mexc_import_error",
            "message": str(exc),
        })
        return _empty_mexc_wallet(), warnings

    creds = load_mexc_credentials()
    if not creds:
        warnings.append({
            "code": "mexc_not_connected",
            "message": "No MEXC credentials (data/.mexc_creds.json).",
        })
        return _empty_mexc_wallet(), warnings

    try:
        mx = MexcExchange(client=MexcClient(*creds), live_orders=False)
        all_bal = mx.client.get_all_balances()
        usdt = all_bal.get("USDT", {"free": 0, "locked": 0, "total": 0})
        assets: List[Dict[str, Any]] = []
        for asset, b in all_bal.items():
            amt = float(b.get("total") or 0)
            if amt <= 0:
                continue
            free = float(b.get("free") or 0)
            locked = float(b.get("locked") or 0)
            px = _price_usdt(asset, "mexc")
            usd_val = (amt * px) if px else 0.0
            dust = usd_val > 0 and usd_val < 1.0
            assets.append({
                "asset": asset,
                "free": free,
                "locked": locked,
                "total": amt,
                "usd_value": round(usd_val, 4),
                "dust": dust,
            })
        assets.sort(key=lambda a: a["usd_value"], reverse=True)

        trades = open_trades or []
        exposure = sum(
            (t.get("invested") or 0) for t in trades
            if (t.get("exchange") or "") == "mexc"
        )
        open_count = sum(
            1 for t in trades if (t.get("exchange") or "") == "mexc"
        )
        free_usdt = float(usdt.get("free") or 0)
        return {
            "connected": True,
            "usdt": {
                "free": float(usdt.get("free") or 0),
                "locked": float(usdt.get("locked") or 0),
                "total": float(usdt.get("total") or 0),
            },
            "assets": assets,
            "open_exposure": round(exposure, 4),
            "open_positions": open_count,
            "amount_left": round(amount_left_to_trade(free_usdt, 0, exposure), 4),
        }, warnings
    except Exception as exc:
        warnings.append({
            "code": "mexc_wallet_error",
            "message": str(exc),
        })
        return _empty_mexc_wallet(), warnings


def _empty_mexc_wallet() -> Dict[str, Any]:
    return {
        "connected": False,
        "usdt": {"free": 0.0, "locked": 0.0, "total": 0.0},
        "assets": [],
        "open_exposure": 0.0,
        "open_positions": 0,
        "amount_left": 0.0,
    }


def wallet_summary() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    warnings: List[Dict[str, Any]] = []
    trades, trade_warnings = _load_open_trades()
    warnings.extend(trade_warnings)

    bin_data, bin_warn = fetch_binance_wallet()
    warnings.extend(bin_warn)
    mexc_data, mexc_warn = fetch_mexc_wallet(trades)
    warnings.extend(mexc_warn)

    by_ex = group_trades_by_exchange(trades)
    bin_exp = sum((t.get("invested") or 0) for t in by_ex.get("binance", []))
    mexc_exp = sum((t.get("invested") or 0) for t in by_ex.get("mexc", []))

    return {
        "binance": bin_data,
        "mexc": mexc_data,
        "deployed": {
            "binance_usdt": round(bin_exp, 4),
            "mexc_usdt": round(mexc_exp, 4),
            "total_usdt": round(bin_exp + mexc_exp, 4),
        },
        "open_positions": {
            "binance": len(by_ex.get("binance", [])),
            "mexc": len(by_ex.get("mexc", [])),
            "total": len(trades),
        },
    }, warnings


def _load_open_trades() -> Tuple[List[Dict], List[Dict[str, Any]]]:
    """Load open trades from disk without importing bot orchestrator."""
    warnings: List[Dict[str, Any]] = []
    try:
        from pathlib import Path
        import json
        trades_dir = Path(__file__).resolve().parent / "data" / "trades"
        if not trades_dir.is_dir():
            warnings.append({
                "code": "trades_dir_missing",
                "message": f"Trade directory not found: {trades_dir}",
            })
            return [], warnings
        trades: List[Dict] = []
        for fp in sorted(trades_dir.glob("*.json")):
            try:
                with fp.open(encoding="utf-8") as f:
                    chunk = json.load(f)
                if isinstance(chunk, list):
                    trades.extend(chunk)
            except Exception as exc:
                warnings.append({
                    "code": "trade_file_read_error",
                    "message": str(exc),
                    "path": str(fp),
                })
        return [t for t in trades if t.get("status") == "open"], warnings
    except Exception as exc:
        warnings.append({
            "code": "trades_load_error",
            "message": str(exc),
        })
        return [], warnings
