"""AlphaTrade read-only FastAPI dashboard API (Phase A).

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
    version="0.1.0",
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
        ],
    }


@app.get("/api/health")
def get_health():
    return api.build_health()


@app.get("/api/settings")
def get_settings():
    return api.load_settings()


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


@app.get("/api/scanner")
def get_scanner():
    return api.load_scanner()


@app.get("/api/activity")
def get_activity(limit: int = Query(300, ge=1, le=500)):
    return api.load_activity(limit=limit)


@app.get("/api/performance")
def get_performance():
    return api.load_performance()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("ALPHATRADE_API_PORT", "8000")),
        reload=False,
    )
