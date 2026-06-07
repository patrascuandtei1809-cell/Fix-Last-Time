"""Lock-in tests for the multi-exchange scanner (Step 3).

All OFFLINE — these exercise the PURE normalise / filter / score / rank
functions with synthetic tickers. No network, no keys.

Key guarantees:
  1. MEXC priceChangePercent is a FRACTION and gets ×100; Binance is already a
     percent and is left alone.
  2. Junk is filtered: stablecoins, leveraged tokens, illiquid, too-flat,
     too-volatile, pump/dump, non-standard (non-ASCII) symbols.
  3. Scoring is bounded 0-100 and ranks the obviously-better setup higher.
"""
import pytest

import scanner as sc


def _binance_row(sym, price=100.0, high=110.0, low=95.0, qv=50_000_000.0,
                 chg=5.0, bid=99.9, ask=100.1):
    return {"symbol": sym, "lastPrice": price, "highPrice": high,
            "lowPrice": low, "quoteVolume": qv, "priceChangePercent": chg,
            "bidPrice": bid, "askPrice": ask}


def test_mexc_change_is_fraction_scaled():
    raw = {"symbol": "SOLUSDT", "lastPrice": "150", "highPrice": "165",
           "lowPrice": "140", "quoteVolume": "80000000",
           "priceChangePercent": "0.08", "bidPrice": "149.9",
           "askPrice": "150.1"}
    m = sc.normalize_ticker(raw, "mexc")
    assert m["change"] == pytest.approx(8.0)  # 0.08 fraction → 8%


def test_binance_change_is_percent_unchanged():
    m = sc.normalize_ticker(_binance_row("BTCUSDT", chg="3.2"), "binance")
    assert m["change"] == pytest.approx(3.2)


def test_volatility_and_spread_derived():
    m = sc.normalize_ticker(_binance_row("BTCUSDT", high=110, low=100,
                                         bid=100, ask=101), "binance")
    assert m["volatility"] == pytest.approx(10.0)   # (110-100)/100*100
    assert m["spread"] == pytest.approx(1.0)         # (101-100)/100*100


def test_filters_reject_junk():
    cfg = sc.ScanConfig()

    def reason(row, exch="binance"):
        return sc.filter_reason(sc.normalize_ticker(row, exch), cfg)

    # stablecoin base
    assert "stable" in reason(_binance_row("FDUSDUSDT"))
    # leveraged token
    assert "leveraged" in reason(_binance_row("BTC3LUSDT"))
    assert "leveraged" in reason(_binance_row("ETHUPUSDT"))
    # non-standard (non-ASCII) symbol
    assert "non-standard" in reason(_binance_row("币安人生USDT"))
    # illiquid
    assert "illiquid" in reason(_binance_row("AAAUSDT", qv=100_000))
    # too flat
    assert "flat" in reason(_binance_row("BBBUSDT", high=100.1, low=100.0))
    # too volatile
    assert "volatile" in reason(_binance_row("CCCUSDT", high=300, low=100))
    # pumping
    assert "pump" in reason(_binance_row("DDDUSDT", chg=200))
    # dumping
    assert "dump" in reason(_binance_row("EEEUSDT", chg=-80))


def test_clean_symbol_passes():
    cfg = sc.ScanConfig()
    m = sc.normalize_ticker(_binance_row("NEARUSDT"), "binance")
    assert sc.filter_reason(m, cfg) is None


def test_score_is_bounded_and_ranks_quality():
    cfg = sc.ScanConfig()
    # Strong: deep liquidity, healthy volatility near ideal, positive momentum.
    strong = sc.normalize_ticker(
        _binance_row("STRONGUSDT", qv=150_000_000, high=115, low=100, chg=12,
                     bid=107.49, ask=107.51), "binance")
    # Weak-ish but still valid: thin-ish volume, mild move.
    weak = sc.normalize_ticker(
        _binance_row("WEAKUSDT", qv=3_000_000, high=104, low=100, chg=1,
                     bid=101.9, ask=102.1), "binance")
    s_strong, _ = sc.score_opportunity(strong, cfg)
    s_weak, _ = sc.score_opportunity(weak, cfg)
    assert 0 <= s_weak <= 100
    assert 0 <= s_strong <= 100
    assert s_strong > s_weak


def test_rank_sorts_desc_and_drops_junk():
    cfg = sc.ScanConfig()
    raw = {
        "binance": [
            _binance_row("ZECUSDT", qv=120_000_000, high=115, low=100, chg=14),
            _binance_row("FDUSDUSDT"),                 # stable → dropped
            _binance_row("SCAMUSDT", high=500, low=100, chg=300),  # pump → dropped
            _binance_row("NEARUSDT", qv=20_000_000, high=108, low=100, chg=5),
        ],
        "mexc": [],
    }
    ranked = sc.rank(raw, cfg)
    syms = [o["symbol"] for o in ranked]
    assert "FDUSDUSDT" not in syms and "SCAMUSDT" not in syms
    assert syms == sorted(syms, key=lambda s: -dict(
        (o["symbol"], o["score"]) for o in ranked)[s])
    # All survivors carry score + reason for the dashboard.
    for o in ranked:
        assert "score" in o and "reason" in o
