"""Tests for Phase D1 read-only API endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_support as api
from api_server import app

client = TestClient(app)
TRADING_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = TRADING_DIR / "data"


def test_health_uses_heartbeat_read_with_stale():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    hb = body["heartbeats"]
    assert "bot" in hb
    assert "scanner" in hb
    assert "dashboard" in hb
    for name in ("bot", "scanner", "dashboard"):
        assert "stale" in hb[name]
        assert "present" in hb[name]


def test_wallet_binance_no_creds_returns_warning():
    resp = client.get("/api/wallet/binance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["warnings"], list)
    assert body["data"]["connected"] is False


def test_wallet_mexc_no_creds_returns_warning():
    resp = client.get("/api/wallet/mexc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["warnings"], list)


def test_wallet_summary_shape():
    resp = client.get("/api/wallet/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "binance" in body["data"]
    assert "mexc" in body["data"]
    assert "deployed" in body["data"]


def test_trades_open_has_by_exchange():
    resp = client.get("/api/trades/open")
    assert resp.status_code == 200
    body = resp.json()
    assert "by_exchange" in body
    assert "binance" in body["by_exchange"]
    assert "mexc" in body["by_exchange"]


def test_trades_history_rows():
    resp = client.get("/api/trades/history/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert isinstance(body["data"], list)


def test_scanner_status():
    resp = client.get("/api/scanner/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "daemon_running" in body["data"]
    assert "heartbeat" in body["data"]


def test_scanner_config():
    resp = client.get("/api/scanner/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "exchange_mode" in body["data"]


def test_scanner_exchange_filter():
    resp = client.get("/api/scanner?exchange=mexc")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body


def test_global_rules():
    resp = client.get("/api/rules/global")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["buy_threshold_pct"] == -0.05
    assert data["take_profit_pct"] == 0.60
    assert data["stop_loss_pct"] == -0.30
    assert data["stop_loss_cooldown_sec"] == 60
    assert data["reentry_cooldown_sec"] == 60


def test_status_system_connections_bools_only():
    resp = client.get("/api/status/system")
    assert resp.status_code == 200
    body = resp.json()
    conns = body["data"]["connections"]
    assert set(conns.keys()) == {"binance", "mexc"}
    assert all(isinstance(v, bool) for v in conns.values())


def test_status_bot():
    resp = client.get("/api/status/bot")
    assert resp.status_code == 200
    body = resp.json()
    assert "running" in body["data"]
    assert "open_trades" in body["data"]


def test_decisions_empty_with_warning_when_no_bot():
    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"], list)
    assert isinstance(body["warnings"], list)


def test_diagnostics_trades():
    resp = client.get("/api/diagnostics/trades")
    assert resp.status_code == 200
    body = resp.json()
    assert "frequency" in body["data"]
    assert "block_summary" in body["data"]


def test_diagnostics_report():
    resp = client.get("/api/diagnostics/report")
    assert resp.status_code == 200
    body = resp.json()
    assert "report" in body["data"]


def test_settings_snapshot_redacts_secrets():
    settings_path = DATA_DIR / "settings.json"
    if not settings_path.is_file():
        pytest.skip("settings.json not present")
    resp = client.get("/api/settings/snapshot")
    body = resp.json()
    snap = body["data"]
    if "tg_token" in snap:
        assert snap["tg_token"] == "[redacted]"


def test_performance_equity_and_details():
    eq = client.get("/api/performance/equity").json()
    assert "series" in eq["data"]
    det = client.get("/api/performance/details").json()
    assert isinstance(det["data"], list)


def test_chart_candles_requires_symbol():
    resp = client.get("/api/chart/candles")
    assert resp.status_code == 422


def test_chart_candles_public_binance():
    resp = client.get("/api/chart/candles?symbol=BTCUSDT&interval=5m&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    if body["ok"]:
        assert len(body["data"]) > 0
    else:
        assert body["warnings"]


def test_chart_markers_requires_symbol():
    resp = client.get("/api/chart/markers")
    assert resp.status_code == 422


def test_chart_markers_btc():
    resp = client.get("/api/chart/markers?symbol=BTCUSDT")
    assert resp.status_code == 200
    body = resp.json()
    assert "buy" in body.get("data", {}) or body["warnings"]


def test_missing_scanner_file_warning_not_crash():
    result = api.load_scanner_filtered()
    if not (DATA_DIR / "multi_exchange_opportunities.json").is_file():
        assert result["ok"] is False
        assert result["warnings"]


def test_build_health_missing_files_no_crash():
    result = api.build_health()
    assert result["ok"] is True
    assert isinstance(result["warnings"], list)
