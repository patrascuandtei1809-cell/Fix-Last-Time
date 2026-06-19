"""Tests for BUY decision audit (observability only)."""
import json
from pathlib import Path

import buy_audit as ba


def test_classify_block_reason_volume():
    assert ba.classify_block_reason("Volume too low (1.2× < 1.5×)") == "volume_too_low"


def test_classify_block_reason_trend():
    assert ba.classify_block_reason("Trend filter: waiting for upturn") == "trend_failed"


def test_classify_block_reason_executed():
    assert ba.classify_block_reason("BUY order placed", "BUY", traded=True) == "executed"


def test_classify_block_reason_cooldown():
    assert ba.classify_block_reason("Cooldown active (45s left)") == "cooldown_active"


def test_classify_block_reason_engine_error():
    assert ba.classify_block_reason("Dip engine error: boom", "SKIP") == "engine_error"


def test_finish_cycle_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ba, "LIVE_AUDIT_PATH", tmp_path / "live_decision_audit.json")
    monkeypatch.setattr(ba, "BUY_AUDIT_PATH", tmp_path / "buy_audit.json")

    class Rec:
        reason = "Volume too low"
        decision = "HOLD"
        traded = False
        price = 1.23
        change_pct = -0.08
        volume_ratio = 1.1
        trend_ok = True
        free_usdt = 50.0
        buy_threshold = -0.05

    ba.begin_cycle()
    ba.record_from_activity(
        exchange="mexc",
        symbol="PEPEUSDT",
        rec=Rec(),
        score=85,
        settings=None,
    )
    ba.finish_cycle()

    live = json.loads((tmp_path / "live_decision_audit.json").read_text(encoding="utf-8"))
    assert live["summary"]["evaluated"] == 1
    assert live["decisions"][0]["exact_block_reason"] == "volume_too_low"
    assert live["decisions"][0]["score"] == 85

    buy = json.loads((tmp_path / "buy_audit.json").read_text(encoding="utf-8"))
    assert len(buy["history"]) == 1
