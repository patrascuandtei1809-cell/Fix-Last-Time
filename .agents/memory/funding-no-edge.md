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

# Delta-neutral CARRY (long spot + short perp) — HORIZON-DEPENDENT, NOT free money

CAPTURE the perp-vs-spot gap delta-neutral instead of betting on its direction:
long spot + short perp cancels price exposure, so you harvest the perp 8h funding
stream while you hold. Modeled honestly as ONE buy-and-hold per symbol (the BEST
case — fees paid once and amortized): four taker legs (spot+perp open, spot+perp
close) at spot 0.10%/perp 0.05%/side + slip, plus realized funding settlements in
(t0,tN] and basis convergence.

**The verdict FLIPS with horizon — the short window hid a real multi-year carry:**

- **Short window (single-venue OKX, ~92d OKX-reachable cap) → 🔴 REJECT.**
  Per-symbol NET carry: ETH **+0.191%** (APR +0.78%), BTC **−0.072%**,
  SOL **−0.506%** → mean −0.129%/hold, 1/3 positive → fails breadth. Over 92d the
  funding harvest is real but thin and gets eaten by 4-leg fees + negative-funding
  stretches.
- **Multi-year (single-venue Binance, ~5y / 1825d, 1d granularity) → 🟢 ACCEPT.**
  Per-symbol NET carry over ~1821d held: BTC **+36.37%** (APR **+7.29%**, funding
  neg 845/5463), ETH **+35.31%** (APR **+7.08%**, neg 930/5463), SOL **−14.02%**
  (APR −2.81%, neg 1661/5538) → mean +19.22%/hold, **2/3 winners** (BTC+ETH both
  net AND funding-only positive) → passes breadth + funding-beats-fees. The 4-leg
  fee is a one-time 0.38% — trivial when amortized over 5y. Basis term is
  negligible (≤0.26%), confirming the gain is the FUNDING stream, not price luck.

**Why it flips:** over a full cycle, perp longs pay shorts on net for BTC/ETH
(persistent positive funding in bull regimes), so a delta-neutral short-perp holder
collects ~7% APR. The ~92d OKX window simply did not span enough settlements for the
funding sum to overcome fees. SOL funding is net negative even over 5y — alt-coin
perps lean short-funded — so SOL carry loses on both horizons.

- **Implementation is a cash-flow study, NOT a StrategySpec.** `backtest.carry_pnl`
  (pure accumulator, accepts `okx_close` OR `close` column) + `research.run_carry`
  (OKX) / `run_carry_multiyear` (Binance Vision) / `_carry_verdict` /
  `build_carry_cell`, injected via `run_research(extra_cells=[…])`, tagged
  `kind=="carry"`. Run: `python research.py --carry` (OKX 92d) or
  `python research.py --carry-multiyear` (Binance 5y). Both merge into latest.json.
- **Multi-year data path:** spot = `fetch_klines` (Binance), perp =
  `fetch_binance_vision_perp_klines` (monthly UM perp kline zips from
  data.binance.vision, close=parts[4], 404-skip/header-skip/µs-guard, 12h CSV
  cache), funding = `fetch_funding_rates(source="auto")` (Vision multi-year). 1d
  granularity is sufficient for buy-and-hold: only t0/tN prices + the 8h funding
  settlements between matter. Pre-warm caches per-symbol (each fits the per-command
  time budget) before the full `--carry-multiyear` run; backgrounded `&` processes
  get killed when the bash command returns, so don't nohup the full run.
- **Two non-obvious traps:** (1) carry MUST stay out of `CANDIDATES` or it trips
  the 5m-coverage / timeframe lock-in tests — inject it as an extra cell instead.
  (2) `run_research(specs=[])` must run NOTHING; the old `specs or CANDIDATES`
  treated `[]` as falsy and ran the FULL sweep — guard with `specs is None`.
**Maker-fee carry (does it work if you DON'T cross the spread?):** the taker carry
pays 4 CROSSING legs (~0.38% one-time). Re-priced as RESTING MAKER orders (spot
≈0.075%, perp ≈0% rebate, ~0 slippage → ~0.15% over 4 legs), the OKX ~92d carry
FLIPS from REJECT (mean −0.129%/hold) to a MARGINAL ACCEPT (mean **+0.101%/hold**).
So the thin funding harvest only clears costs once you stop crossing the spread —
which validates the standing "capture at sub-fee cost" lesson, but the ACCEPT is
**CONDITIONAL on maker fills that are NOT guaranteed** (a resting leg may not fill
when you want in/out). `research.run_carry_maker` (`--carry-maker`) runs maker +
taker baseline together so both reads sit in latest.json (cell
`carry_okx_delta_neutral_maker`). Still `kind=="carry"` → never wired live.
- **An ACCEPT here still NEVER goes live.** Carry cells are excluded from the
  directional allowlist by `kind` (asserted in test_carry.py), AND the live bot is
  SPOT-only — it physically cannot short a perp, so a delta-neutral carry is not
  executable by the current engine regardless of verdict. The leaderboard ACCEPT
  test (`test_only_v2_4h_is_accepted_in_leaderboard`) filters `kind!="carry"` for
  the same reason. The finding is real and documented; it is a *different product*
  (funding farming) than the directional spot bot, not a new live signal.

# Alternative-source edge probe: perpetual funding

Probed whether a non-price signal (perp-swap FUNDING rate) clears the after-fee
hurdle in the same fee-adjusted, walk-forward sweep as the technical strategies.

**Verdict: 🔴 NO EDGE.** Funding Contrarian @4h was the single best cell of the
whole sweep (+0.100%/trade, PF 1.06) but still REJECTs under the strict rule
(per-cell breadth guard — ETH leg negative). Momentum sign and all 8h cells were
clearly negative. Allowlist stayed empty; nothing wired live.

**Now a MULTI-YEAR, SINGLE-VENUE proof (June 2026):** funding history is sourced
from Binance's public data archive `data.binance.vision`
(`/data/futures/um/monthly/fundingRate/<SYM>/<SYM>-fundingRate-YYYY-MM.zip`),
which IS reachable from Replit even though the live `fapi.binance.com` API is
geo-blocked (451). Coverage ≈5y (BTC/ETH from 2020-08, SOL 2020-09) up to the
LAST COMPLETE month (no daily fundingRate dump exists, so the current partial
month is excluded). CSV cols `calc_time,funding_interval_hours,last_funding_rate`
(calc_time = settlement ms). This is a CLEAN single-venue study — Binance perp
funding paired with Binance spot candles — not the old cross-venue OKX pairing.
**Verdict over 5y is UNCHANGED: still 🔴 NO EDGE.** All 4h/8h/5m funding cells
REJECT (best = Funding Momentum @8h, +0.368%/trade but 1/3 symbol legs negative
→ per-cell breadth guard fails). The short window did not hide an edge.

`backtest.fetch_funding_rates` now: Binance Vision (PRIMARY, multi-year) →
OKX REST (FALLBACK, ~92d cap) if the archive yields nothing. Funding StrategySpecs
run 4h/8h over `periods=[1825]`; the coverage-only 5m cell stays `tf_periods={"5m":[90]}`
(funding is an 8h step function — 5m z-score is a meaningless step-edge detector,
and 5y of 5m candles = ~525k bars/symbol for no signal). `StrategySpec.tf_periods`
+ `period_for(interval, default)` give per-interval period overrides.

**Data-source reachability from Replit (futures/funding):** `data.binance.vision`
static archive = OK (multi-year); OKX REST = OK (~92d cap); Binance fapi = 451;
Bybit = 403; binance.us = no futures; Kraken futures schema messy. Prefer the
Vision archive for any multi-year funding history; OKX only for short windows.

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
