---
name: AlphaTrade after-fee edge verdict
description: Research sweep verdict — sub-4h all fee-dead; multi-year history flips V2 @ 4h to a canonical ACCEPT, but live is symbol-scoped to the lone deep-ROBUST cell (ETH).
---

## UPDATE — multi-year history flips ONE technical cell (June 2026)
Extending the canonical sweep to ~5y (1825d) on 1h/4h (via per-interval
`tf_periods` in `research.py` CANDIDATES; 5m/15m stay on 90/180d) **changed the
verdict for one cell**: `EMA_MACD_RSI_VOLUME_V2` @ **4h** now ACCEPTs over 5y —
BTC +0.33%, ETH +0.23%, SOL +0.92% (all 3 net-positive after fees), aggregate
+0.50%/trade PF1.25, 462 trades, WF 4/4. Donchian 4h (2/3 +) and Trend Pullback
4h (1/3 +) improve but still REJECT on breadth. EVERY 5m/15m/1h cell stays
clearly negative (fees ≫ gross). So the short 90/180d windows were *hiding* the
4h edge, not proving its absence — multi-year history matters for HTF cells.
**Live consequence (operator decision):** canonical ACCEPT is necessary but NOT
sufficient. The live allowlist is now driven by canonical-ACCEPT ∩ deep-ROBUST
(`validate_candidates.approved_symbols_by_strategy`): only **ETH V2 @ 4h** is
authorized; BTC/SOL stay OFF (canonical-positive but only WEAK in deep
validation). `validated_strategies.json` entries carry a `symbols` list and
`is_strategy_validated(strategy, interval, symbol)` enforces it (symbol-less
query never matches a scoped entry). The default LIVE config (dip @1m) never
presents V2/4h, so the entry is dormant until the operator runs that strategy.
Pinned by `test_approval_rules.py` (`test_only_v2_4h_is_accepted_in_leaderboard`,
`test_live_allowlist_snapshot_is_eth_v2_only`). The "NO after-fee edge" history
below is the *pre-multi-year* canonical picture — kept for context.

# Honest verdict: NO after-fee edge found (pre-multi-year canonical sweep)

The Strategy Research Framework (`trading/research.py`, engine `trading/backtest.py`)
sweeps strategy × timeframe × symbol × period and ranks by **net expectancy/trade
after fees**. Cost model: 0.1%/side fee + 0.02%/side slippage ≈ **0.24% round-trip**.

**Result: every candidate REJECTS.** No strategy×timeframe produced a robust
positive after-fee expectancy under the strict ACCEPT rule (net exp > 0 AND PF ≥ 1
on EVERY cell with ≥5 trades, ≥20 total trades, majority walk-forward folds positive).

- Closest: Trend Pullback @ 4h aggregate net **+0.051%/trade** — but that is NOT an
  edge: it is carried entirely by SOL (+1.23% on only 15 trades) while BTC (−0.20%)
  and ETH (−0.71%) are negative. One lucky symbol on a tiny sample → correctly REJECTED.
- EMA/MACD/RSI/Vol V2 @ 4h: −0.122%; Trend Pullback @ 1h: −0.214%; 1m reversal
  baseline: −0.250% (fees alone ≈ −0.24%, confirming the 1m scalper is structurally dead).
- Donchian breakout was the worst (−0.42% to −0.49%) — breakouts on crypto majors
  mostly fail/whipsaw at these timeframes.

**Why this matters / how to apply:**
- The pattern across ALL cells: gross edge (if any) is smaller than the ~0.24%
  round-trip cost. Win rates sit ~27–37% with PF < 1. Lowering thresholds to trade
  MORE only multiplies the fee drag — the historical failure mode of this project.
- The live bot now has an **auto-disable gate** (`is_strategy_validated` in
  research.py). It is enforced on BOTH live paths: the legacy orchestrator
  (`bot.py` before `execute_entry`) AND the default LIVE dip path
  (`DipLiveEngine` in `live_engine.py`, checked in the ENTRY branch after
  safe-mode, fail-closed). The dip path presents identity
  `("20-Minute Dip", "1m")` to the allowlist. The allowlist
  (`data/research/validated_strategies.json`) is EMPTY, so the bot will not place
  auto orders on EITHER path. This is the correct, honest default. Manual trades
  bypass the gate. **The gate is entry-only — open positions still get their
  stop-loss/take-profit exit** (exits run before the gate).
- Override only for deliberate experiments: `ALPHATRADE_ALLOW_UNVALIDATED=1`.
- To change the verdict you need a genuinely different edge source (e.g. funding/basis,
  cross-exchange, on-chain/news — Phase 3 paid feeds), NOT more threshold tuning.
- Runtime note: the weighted-gate 1m baseline is very slow per-candle (recomputes
  indicators on a sliced df each bar). Keep 1m baseline tiny (BTC, a few days); HTF
  (1h/4h) cells are fast. Background/detached jobs do NOT persist across tool calls
  in the Replit sandbox — run sweeps synchronously within the command timeout.

## Canonical pipeline must cover EVERY timeframe (incl. 5m)
The strict pipeline (`research.py` → `data/research/latest.json`) is the single
source of truth for the verdict + allowlist gate. It must sweep the FULL frame
set **1m/5m/15m/1h/4h under one acceptance rule** — do not let a timeframe live
only in an exploratory side-script (`edge_report.md`). 5m was once missing from
the strict report; now every HTF candidate's `timeframes` includes 5m and all
5m cells REJECT (≈ −0.24%, i.e. fees alone). Pinned by
`trading/tests/test_timeframe_coverage.py`.
**Why:** a code review correctly blocked completion because the canonical report
silently omitted 5m while a looser side-analysis covered it — two narratives, one
gate. **How to apply:** when adding a timeframe, add it to the StrategySpec
`timeframes` in `research.py` CANDIDATES (not just a script) and regenerate
`latest.json`. Long sweeps: set `RESEARCH_SUBCELL_CACHE=1` and run
`timeout 110 python research.py` repeatedly — subcells cache to
`data/research/subcells/` (gitignored) and resume until exit=0; detached/background
jobs are reaped by the sandbox between tool calls, so never rely on them.

## Hardened ACCEPT rule (breadth guards)
`_verdict()` requires ALL of: aggregate net exp > 0 AND PF ≥ 1; edge on ≥2
symbols (`MIN_SYMBOLS`, not one lucky coin); ≥60% of attempted subcells reach
`MIN_TRADES` (`MIN_TRADED_FRAC`, stops sparse cells hiding losers behind the
"every traded cell passes" test); every traded cell net exp > 0 AND PF ≥ 1;
≥20 total trades; majority walk-forward folds positive.
**Why:** the original rule only checked positivity on cells with ≥5 trades, so a
single strong subcell could pass while others were sparse/negative and excluded.
**How to apply:** when adding strategies/symbols, an ACCEPT must clear all guards
— do not relax them to get a green light.

## Refinement — where the edge actually lives (multi-TF × per-symbol sweep)
A full sweep (5m/15m/1h/4h × BTC/ETH/SOL separately, after ~0.24% round-trip)
moved the verdict from "nothing works" to a SPECIFIC, narrow finding:
- **5m, 15m, 1h are ALL negative** for every strategy/symbol — fee drag > gross edge.
- **4h is the only frame that clears fees**, and only for higher-timeframe
  momentum/trend (V2, Trend Pullback, Donchian) on **ETH and SOL**.
- Best: `EMA_MACD_RSI_VOLUME_V2` @ 4h — SOL +1.02%/trade (PF1.59), ETH +0.99%
  (PF1.72), but BTC ≈ break-even (−0.05%, PF0.95).
**Why it's still NOT a live green light:** not positive on all 3 symbols, single
360d window, only ~30–50 trades/symbol (too thin for meaningful walk-forward).
Auto-disable gate stays default-safe (allowlist empty). Next: out-of-sample
confirmation across more windows, run OFF the 120s sandbox.
**How to apply:** stop tuning sub-hour scalpers — the only place worth more
research is 4h HTF momentum on ETH/SOL. Driver: `trading/edge_search.py`
(chunked by TF to fit the sandbox cap; results in `data/research/edge_rows.json`,
report in `edge_report.md`).

## Rigorous validation of the 4h candidates (max history + WF + Monte Carlo + sensitivity)
The 360d edge-search positives held up — and STRENGTHENED — on MAXIMUM history
(ETH 4h 8.8y/295 trades, SOL 4h 5.8y/200 trades). The small 360d window had
under-counted the edge, not over-counted it.
- **🟢 ROBUST: `EMA_MACD_RSI_VOLUME_V2` @ 4h ETH** — +0.84%/trade, PF1.40,
  Sharpe2.43, +616% total; 5/5 walk-forward folds positive (incl. 2018/2022
  bears); Monte Carlo P(exp>0)=99.3% with bootstrap 90% CI LOWER bound still
  positive; survives fee±50%, slip±50%, ATR SL/TP ±25%. BUT reshuffled maxDD
  worst-5% ≈ −60%.
- **🟡 WEAK: V2 @ 4h SOL** — base good but edge collapses to break-even under a
  25%-tighter stop (narrow-param dependence) and MC CI lower bound is negative.
- **🟡 WEAK: Trend Pullback @ 4h SOL** — survives sensitivity but MC CI dips
  negative (P(exp>0)=83%) and 1 WF fold negative.
- **🟡 WEAK (NEAR-MISS): V2 @ 4h BTC** — re-checked June 2026 over MAX history
  (8.8y/275 trades, back to 2017; BTC was never previously a deep-validation
  candidate). It passes EVERY gate — base +0.65%/trade PF1.42 Sharpe2.52, all 8
  sensitivity scenarios positive, walk-forward stable, Monte-Carlo P(exp>0)≥95%
  with CI lower bound > 0, maxDD −31.5% within ±40% — EXCEPT the
  **relative-Sharpe-deterioration gate**: worst stress Sharpe (+1.20, under
  TP−25%) is just below 0.5×its unusually-high base Sharpe (0.5×2.52=1.26). The
  gate is RELATIVE, so a HIGHER base Sharpe makes it HARDER to clear even though
  +1.20 is excellent in absolute terms.
  **Why it matters:** do NOT promote BTC by moving goalposts — the rule is locked
  by `test_approval_rules.py` (`test_btc_v2_4h_stays_weak_near_miss` pins that the
  ONLY failing gate is Sharpe). Honest verdict: BTC + SOL stay WEAK → **ETH-only
  live allowlist stands**. If the relative-Sharpe gate is ever revisited as a
  design question, BTC is the candidate most affected. Deep-validation candidates
  live ONLY in `validate_candidates.CANDS`; adding a symbol there + re-running all
  7 scenarios is the whole re-check (deterministic for fixed data; ETH stayed
  ROBUST on re-run).
**Why it matters:** the right edge bar is not "positive on one 360d window" but
positive-on-max-history AND walk-forward-stable AND Monte-Carlo lower-CI>0 AND
parameter-stable. Only ETH 4h V2 clears all four. Tooling:
`trading/validate_candidates.py` (fee sensitivity is ANALYTIC since ret=gross−2·fee;
slip/param need re-runs because slippage changes fills→SL/TP timing). Live gate
left default-safe; nothing deployed. All candidates are LONG-ONLY → a long bear
regime is the main un-modeled risk.
