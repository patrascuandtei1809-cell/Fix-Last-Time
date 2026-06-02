"""LOCK-IN tests for the strict research ACCEPT/REJECT engine (`research._verdict`).

This is the gate that decides which (strategy × timeframe) cells make it onto the
live allowlist. It must REJECT on any of: too few trades, a negative aggregate,
a single bad sub-cell, too few symbols (breadth), too sparse, or weak walk-forward
— and only ACCEPT when every guard passes.
"""
import research as R


def _cell(exp, pf, trades):
    return {"expectancy_pct": exp, "profit_factor": pf, "trades": trades}


def _good_subcells():
    """Two symbols, both clearly positive after fees, each above MIN_TRADES."""
    return {
        "BTCUSDT/365d": _cell(0.5, 1.5, 40),
        "ETHUSDT/365d": _cell(0.4, 1.4, 35),
    }


def _good_agg():
    return _cell(0.45, 1.45, 75)


def _good_wf():
    return [_cell(0.4, 1.3, 18), _cell(0.5, 1.4, 19), _cell(0.45, 1.35, 18)]


def test_clean_setup_accepts():
    v, reasons = R._verdict(_good_subcells(), _good_agg(), _good_wf())
    assert v == "ACCEPT", reasons


def test_too_few_total_trades_rejects():
    v, _ = R._verdict(_good_subcells(), _cell(0.45, 1.45, R.MIN_TOTAL_TRADES - 1),
                      _good_wf())
    assert v == "REJECT"


def test_negative_aggregate_rejects():
    v, _ = R._verdict(_good_subcells(), _cell(-0.01, 0.9, 75), _good_wf())
    assert v == "REJECT"


def test_one_bad_subcell_rejects():
    subs = _good_subcells()
    subs["ETHUSDT/365d"] = _cell(-0.1, 0.8, 35)   # one cell negative after fees
    v, _ = R._verdict(subs, _good_agg(), _good_wf())
    assert v == "REJECT"


def test_single_symbol_breadth_rejects():
    subs = {"BTCUSDT/365d": _cell(0.5, 1.5, 80)}   # only one symbol
    v, _ = R._verdict(subs, _good_agg(), _good_wf())
    assert v == "REJECT"


def test_weak_walk_forward_rejects():
    wf = [_cell(-0.4, 0.8, 18), _cell(-0.3, 0.9, 19), _cell(0.5, 1.4, 18)]  # 1/3 +
    v, _ = R._verdict(_good_subcells(), _good_agg(), wf)
    assert v == "REJECT"
