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

# Delta-neutral CARRY (long spot + short perp) — also NO edge

The one remaining angle after the directional spread probes all rejected:
CAPTURE the perp-vs-spot gap delta-neutral instead of betting on its direction.
Long spot + short perp cancels the price exposure, so you harvest the perp 8h
funding stream while you hold. Modeled honestly as ONE buy-and-hold per symbol
(the BEST case — fees paid once and amortized): four taker legs
(spot+perp open, spot+perp close) at spot 0.10%/perp 0.05%/side + slip, plus the
realized funding settlements in (t0,tN] and the basis convergence.

**Verdict: 🔴 NO EDGE** (single-venue OKX, ~92d OKX-reachable window). Per-symbol
NET carry: ETH **+0.191%** (APR +0.78%), BTC **−0.072%**, SOL **−0.506%** → mean
−0.129%/hold, only 1/3 symbols positive → fails breadth → REJECT. The funding
harvest is REAL (ETH funding-only-net +0.312%, BTC +0.037%) but thin, and gets
eaten by the four-leg fees + negative-funding stretches (SOL funding was net
negative). Consistent with every other AlphaTrade finding.

- **Implementation is a cash-flow study, NOT a StrategySpec.** `backtest.carry_pnl`
  (pure accumulator) + `research.run_carry`/`_carry_verdict`/`build_carry_cell`,
  injected into the report via `run_research(extra_cells=[…])` and tagged
  `kind=="carry"`. Run with `python research.py --carry --merge`.
- **Two non-obvious traps:** (1) carry MUST stay out of `CANDIDATES` or it trips
  the 5m-coverage / timeframe lock-in tests — inject it as an extra cell instead.
  (2) `run_research(specs=[])` must run NOTHING; the old `specs or CANDIDATES`
  treated `[]` as falsy and ran the FULL sweep — guard with `specs is None`.
- **Funding source for carry = `fetch_funding_rates(source="okx")`** (forced OKX,
  ~92d, distinct `_okx` cache suffix) so the funding harvested is from the SAME
  perp being shorted (single-venue consistency), not the Vision archive.
- **Lesson:** carry/funding is not free money on majors — the 8h funding you
  collect does not reliably beat a 4-leg round trip. Don't re-probe carry as a
  live strategy; it never feeds the directional allowlist (excluded by `kind`).

## Multi-year carry DOES clear costs — the ~92d "no edge" was a window artifact
**The 4-leg fee is ONE-TIME; funding is a continuous cash-flow.** Over a
multi-year buy-and-hold the fee is amortized to nothing, so the carry result is
dominated by the SIGN/size of the funding stream, not the fees. Single-venue
Binance (Vision funding + Binance spot, perp price PROXIED by spot so the basis
term is exactly 0 — conservative funding-only read) over the Vision-reachable
window: BTC NET **+36.4%**, ETH **+35.3%** (taker), SOL **−14.3%** (its funding
was net negative the whole window). 2/3 positive → ACCEPT. The maker variant
(spot+perp 0.02%, 0 slip → 0.08% total vs taker 0.38%) is only marginally better
per hold — over years the fee gap is noise; **funding sign is everything.**
**Why CONDITIONAL:** the maker ACCEPT assumes every leg fills as a resting maker
(it may not), and the funding-only model ignores perp margin/liquidation/rollover
risk over a multi-year hold. Flag any maker carry ACCEPT as CONDITIONAL.
**How to apply:** `python research.py --carry-multiyear` (taker + maker, merged
as `carry_binance_multiyear_{taker,maker}`, `kind=="carry"`). Use
`fetch_funding_rates(source="binance-vision")` — STRICT, no OKX fallback — so a
cell labeled Binance can't silently harvest OKX funding. Still NEVER wired live
(carry excluded from the allowlist by `kind`); it's a cash-flow study, and
leaderboard/`edge_found` tests must also exclude `kind=="carry"`. Note: `1d`
spot from Vision caps ~1000 candles (~2.7y), so the hold is multi-year but
shorter than the ~5y funding coverage — honest, just not the full 5y.

## Rolling-window carry: the multi-year ACCEPT is NOT an endpoint artifact (but SOL is fragile)
**A single ~5y buy-and-hold is endpoint-sensitive — the +7% APR could be an
artifact of the 2021→2026 dates.** So model the carry as MANY overlapping
fixed-length holds and judge by BREADTH OF POSITIVE WINDOWS, not one endpoint.
`backtest.rolling_carry(spot, perp, funding, window_days, step_days, …)` enters a
fixed hold at every `step_days` start across the full series and returns the net %
/ APR distribution + `pct_windows_positive` + worst/best window.
`research.run_carry_rolling` runs 90d & 180d holds stepped monthly across the full
~5y Vision funding archive (flat spot-proxy → basis≡0, so net = realized funding −
one-time fees; intentionally spans the WHOLE 5y instead of the ~2.7y the 1d spot
candle cap allows).
**Verdict: 🟢 ACCEPT (both windows), but on 2/3 breadth only.** BTC/ETH are
robustly positive (90d: BTC 97% / ETH 86% of windows positive; 180d: BTC 100% /
ETH 91%, median net +2.7%). **SOL is the fragile leg** — positive median but only
59–64% of windows positive AND a catastrophic **−42% worst window** (its
2021-era deeply-negative funding stretch wipes out years of carry; the MEAN goes
negative even though the median is positive). So the multi-year ACCEPT is
confirmed real for BTC/ETH and NOT a pure endpoint artifact, but SOL must not be
treated as a carry symbol.
**Why median > 0 isn't enough:** `_carry_rolling_verdict` requires
`pct_windows_positive ≥ 70` AND `median > 0` per symbol — a positive median with
fragile breadth (SOL) is rejected at the symbol level even when the basket
ACCEPTs on the other two.
**How to apply:** `python research.py --carry-rolling --merge` → two
`kind=="carry"` cells `carry_binance_rolling_{90,180}d`, STILL never wired live
(carry excluded from the allowlist by `kind`). Use `--merge` so latest.json stays
COMPLETE.

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

## Maker-fill assumption is NOT load-bearing on current windows (but window-dependent)
**The carry's break-even cost is a SYMBOL PROPERTY, not a fee-model choice:**
net = gross − fees, so a symbol clears iff `four-leg fee < its gross carry`, and
the breadth verdict (≥2 symbols) flips ACCEPT→REJECT once the fee exceeds the
**2nd-highest symbol's gross**. So sweeping the fee grid is informative only
relative to that break-even. On the current OKX ~92d and Binance ~5y windows the
2nd-highest gross (BTC ≈0.41% OKX / ETH ≈35.6% Binance) sits ABOVE the dearest
maker-grid cost (0.32%) AND above achievable taker (~0.38% OKX), so the carry
ACCEPTs at EVERY fill assumption — the maker-fill assumption is robust here, not
fragile. SOL never clears (negative gross) under any fee.
**Why this matters:** the task premised a "razor-thin +0.101%/hold" maker ACCEPT;
that was a leaner earlier funding window. Fragility is window-dependent — judge it
by `gross vs break-even`, never by the fee model in isolation.
**How to apply:** `python research.py --carry-maker-sweep --merge` → two
`kind=="carry"` cells (`carry_okx_fee_sensitivity` / `carry_binance_fee_sensitivity`)
with `fee_sweep.grid` + `verdict_breakeven_four_leg_pct`. Verdict is ACCEPT only
if it clears at EVERY grid corner (robust); a flip inside the grid = REJECT
(fragile, load-bearing). Still never wired live (carry excluded by `kind`).

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
