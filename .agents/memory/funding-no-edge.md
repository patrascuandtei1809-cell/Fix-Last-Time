---
name: Funding/alt-source edge probe (no edge)
description: Result of wiring perp funding, perp-vs-spot basis, and cross-exchange spread as alternative signal sources into the research sweep, and the data-access constraints that bound them.
---

# Alternative-source edge probes: ALL three (funding, basis, cross-exchange) — NO edge

All three non-price sources named in the original "look beyond price charts" ask
have now been wired as z-score positioning StrategySpecs (contrarian + momentum
each) and run through the SAME strict fee-adjusted sweep. **Verdict for all
three: 🔴 NO EDGE** — every cell REJECTs at ~−0.24% to −0.41%/trade (i.e. the
gross move never beats the ~0.24% round-trip cost). Allowlist stays empty.

- **Basis** = (OKX perp-SWAP close − Binance spot close)/spot. `basis_contrarian`/
  `basis_momentum`, swept 5m/1h/4h. Best was barely-break-even noise; all REJECT.
- **Cross-exchange spread** = (OKX spot close − Binance spot close)/spot.
  `xspread_contrarian`/`xspread_momentum`, swept 5m/1h/4h. Same null result.
- All four read a single pre-merged column via the shared
  `strategy._positioning_zscore_signal` helper (mirrors `funding_signal`); data
  merged no-look-ahead with `backtest.merge_basis`/`merge_xspread` (merge_asof
  backward) from `backtest.fetch_okx_candles(kind=SWAP|SPOT)`.
- **Lesson:** these spreads are tiny/fast-mean-reverting on majors; sampling them
  as a z-score and trading SPOT round-trips just pays fees. A genuine basis/x-venue
  edge would need to capture the spread directly (perp-vs-spot delta-neutral carry,
  or sub-fee maker arb), not a directional long-only spot entry. Don't re-probe
  this as another spot directional signal.

# Alternative-source edge probe: perpetual funding

Probed whether a non-price signal (perp-swap FUNDING rate) clears the after-fee
hurdle in the same fee-adjusted, walk-forward sweep as the technical strategies.

**Verdict: 🔴 NO EDGE.** Funding Contrarian @4h was the single best cell of the
whole sweep (+0.100%/trade, PF 1.06) but still REJECTs under the strict rule
(per-cell breadth guard — ETH leg negative). Momentum sign and all 8h cells were
clearly negative. Allowlist stayed empty; nothing wired live.

**Why this is a short-window probe, not a proof:** Binance fapi funding is
geo-blocked (451) from Replit. OKX `funding-rate-history` IS reachable (per-asset
SWAP, 8h cadence) but caps at ~92 days. So the funding test is ~90d only, vs the
multi-year technical sweep. Cross-source pairing (OKX perp funding merged onto
Binance/`data-api.binance.vision` spot candles via `merge_asof` backward, no
look-ahead) is acceptable for an exploratory study but not a clean single-venue
backtest.

**Data-source reachability from Replit (futures/funding):** OKX = OK;
Binance fapi = 451; Bybit = 403; binance.us = no futures; Kraken futures schema
messy. Use OKX for any funding/basis probe.

## latest.json must stay COMPLETE — use the merge path
**Rule:** Running `research.py --only <subset>` OVERWRITES `latest.json` with
only those cells, which breaks tests that assert the full technical sweep is
present (and makes the canonical report lie). The full sweep exceeds the sandbox
per-command time budget (slow 1m weighted-gate + large 15m cells), so you cannot
just "re-run everything".
**How to apply:** Run a subset probe with `--merge` (`run_research(merge_latest=
True)`): it loads `latest.json`, drops cells whose `strategy_key` is being re-run,
and carries the rest forward, then re-ranks/re-verdicts/re-writes the allowlist
over the full merged set. To rebuild from scratch after a clobber: copy the last
full-sweep snapshot (`research_<stamp>.json`) onto `latest.json`, then run the
subset with `--merge`.

## Canonical pipeline forces 5m on every HTF candidate
**Rule:** A lock-in test (`tests/test_timeframe_coverage.py`) requires EVERY
non-1m candidate to include `5m` in its `timeframes`. Any new alt-data strategy
must sweep 5m too, even when its native cadence is coarser.
**Why:** Funding is a step function (one value per 8h). At 5m the rolling z-score
degenerates into a step-edge detector that fires whenever its window straddles an
8h funding change — economically meaningless, but it duly REJECTs, so coverage
parity costs nothing and keeps the canonical report honest. Don't read the funding
5m cell as a real edge.
**How to apply:** add `5m` to the spec `timeframes` for coverage; rely on the
4h/8h cells for the genuine funding read.
