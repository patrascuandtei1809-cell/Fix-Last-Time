"""AlphaTrade read-only FastAPI dashboard API (Phase A + D1).

Runs side-by-side with Streamlit on port 8000. No trading, no writes.
"""
from __future__ import annotations

import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

import api_support as api

app = FastAPI(
    title="AlphaTrade API",
    description="Read-only dashboard data API. No trading actions.",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow future React dev server; read-only API only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_D1_ENDPOINTS = [
    "/api/wallet/binance",
    "/api/wallet/mexc",
    "/api/wallet/summary",
    "/api/trades/history/rows",
    "/api/scanner/status",
    "/api/scanner/config",
    "/api/rules/global",
    "/api/status/system",
    "/api/status/bot",
    "/api/markets/core",
    "/api/decisions",
    "/api/diagnostics/trades",
    "/api/diagnostics/report",
    "/api/settings/snapshot",
    "/api/performance/equity",
    "/api/performance/details",
    "/api/chart/candles",
    "/api/chart/markers",
]


@app.get("/")
def root():
    return {
        "service": "alphatrade-api",
        "mode": "read-only",
        "docs": "/docs",
        "endpoints": [
            "/api/health",
            "/api/settings",
            "/api/trades/open",
            "/api/trades/closed",
            "/api/trades/history",
            "/api/scanner",
            "/api/activity",
            "/api/performance",
            *_D1_ENDPOINTS,
        ],
    }


@app.get("/api/health")
def get_health():
    return api.build_health()


@app.get("/api/settings")
def get_settings():
    return api.load_settings()


@app.get("/api/settings/snapshot")
def get_settings_snapshot():
    return api.load_settings_snapshot()


@app.get("/api/trades/open")
def get_trades_open():
    return api.load_trades_open()


@app.get("/api/trades/closed")
def get_trades_closed():
    return api.load_trades_closed()


@app.get("/api/trades/history")
def get_trades_history(
    exchange: str | None = Query(None, description="binance or mexc"),
    status: str | None = Query(None, description="open or closed"),
):
    return api.load_trades_history(exchange=exchange, status=status)


@app.get("/api/trades/history/rows")
def get_trades_history_rows():
    return api.load_trades_history_rows()


@app.get("/api/scanner")
def get_scanner(
    exchange: str | None = Query(None, description="binance or mexc filter"),
):
    return api.load_scanner_filtered(exchange=exchange)


@app.get("/api/scanner/status")
def get_scanner_status():
    return api.load_scanner_status()


@app.get("/api/scanner/config")
def get_scanner_config():
    return api.load_scanner_config()


@app.get("/api/activity")
def get_activity(limit: int = Query(300, ge=1, le=500)):
    return api.load_activity(limit=limit)


@app.get("/api/performance")
def get_performance():
    return api.load_performance()


@app.get("/api/performance/equity")
def get_performance_equity():
    return api.load_performance_equity()


@app.get("/api/performance/details")
def get_performance_details():
    return api.load_performance_details()


@app.get("/api/wallet/binance")
def get_wallet_binance():
    return api.load_wallet_binance()


@app.get("/api/wallet/mexc")
def get_wallet_mexc():
    return api.load_wallet_mexc()


@app.get("/api/wallet/summary")
def get_wallet_summary():
    return api.load_wallet_summary()


@app.get("/api/rules/global")
def get_rules_global():
    return api.load_global_rules()


@app.get("/api/status/system")
def get_status_system():
    return api.load_status_system()


@app.get("/api/status/bot")
def get_status_bot():
    return api.load_status_bot()


@app.get("/api/markets/core")
def get_markets_core():
    return api.load_markets_core()


@app.get("/api/decisions")
def get_decisions(
    venue: str | None = Query(None, description="binance or mexc"),
    limit: int = Query(100, ge=1, le=500),
):
    return api.load_decisions(venue=venue, limit=limit)


@app.get("/api/diagnostics/trades")
def get_diagnostics_trades():
    return api.load_diagnostics_trades()


@app.get("/api/diagnostics/report")
def get_diagnostics_report():
    return api.load_diagnostics_report()


@app.get("/api/chart/candles")
def get_chart_candles(
    symbol: str = Query(..., description="e.g. BTCUSDT"),
    interval: str = Query("5m"),
    venue: str = Query("binance", description="binance or mexc"),
    limit: int = Query(500, ge=1, le=2000),
):
    return api.load_chart_candles(symbol=symbol, interval=interval, venue=venue, limit=limit)


@app.get("/api/chart/markers")
def get_chart_markers(symbol: str = Query(..., description="e.g. BTCUSDT")):
    return api.load_chart_markers(symbol=symbol)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("ALPHATRADE_API_PORT", "8000")),
        reload=False,
    )
