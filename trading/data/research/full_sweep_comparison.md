# Full-Resolution Strategy Sweep — confirmation of the "no edge" finding

This run removes the doubt left by the earlier **trimmed** sweep, which was
constrained by the Replit sandbox's 120s command limit. The full sweep was run
off the bash cap as a managed background job, so every cell ran to completion.

**Verdict: 🔴 NO EDGE — unchanged.** Running the framework at full resolution
does NOT change the conclusion; it strengthens it.

## What changed between the trimmed run and this full run

| Dimension | Trimmed run (`research_20260602_181835`) | Full run (`research_20260602_184850`) |
|---|---|---|
| HTF timeframes | 1h, 4h only | **15m + 1h + 4h** |
| HTF windows | 180d only | **90d + 180d** |
| HTF symbols | BTC/ETH/SOL | BTC/ETH/SOL |
| 1m baseline | BTC only, 3d | **BTC + ETH + SOL, 7d** |
| Cells evaluated | 7 | **10** |
| Total trades | ~807 | **~3,935** |
| Verdict | 🔴 NO EDGE | 🔴 NO EDGE |

## Full leaderboard (ranked by NET expectancy/trade, after ~0.24% round-trip cost)

| # | strategy @ tf | trades | net_exp% | PF | win% | verdict |
|---|---|---:|---:|---:|---:|---|
| 1 | Trend Pullback (HTF) @ 1h | 250 | -0.171 | 0.81 | 37.2 | REJECT |
| 2 | Trend Pullback (HTF) @ 4h | 68 | -0.186 | 0.89 | 33.8 | REJECT |
| 3 | EMA/MACD/RSI/Volume V2 (HTF) @ 15m | 1054 | -0.222 | 0.59 | 32.2 | REJECT |
| 4 | Reversal Scalper (1m baseline) @ 1m | 1073 | -0.242 | 0.05 | 5.7 | REJECT |
| 5 | EMA/MACD/RSI/Volume V2 (HTF) @ 4h | 83 | -0.242 | 0.85 | 32.5 | REJECT |
| 6 | Trend Pullback (HTF) @ 15m | 1013 | -0.254 | 0.55 | 30.4 | REJECT |
| 7 | Donchian Breakout (HTF) @ 15m | 817 | -0.269 | 0.52 | 31.7 | REJECT |
| 8 | EMA/MACD/RSI/Volume V2 (HTF) @ 1h | 283 | -0.386 | 0.61 | 29.3 | REJECT |
| 9 | Donchian Breakout (HTF) @ 1h | 210 | -0.404 | 0.60 | 30.5 | REJECT |
| 10 | Donchian Breakout (HTF) @ 4h | 74 | -0.689 | 0.62 | 24.3 | REJECT |

**Every one of the 10 cells is negative after fees. No cell's aggregate clears
the fee hurdle.**

## Did the expanded resolution surface anything the trimmed run missed?

No. The two dimensions added by the full run both come back negative:

- **15m (newly added):** all three 15m cells are negative in aggregate
  (V2 -0.222%, Trend Pullback -0.254%, Donchian -0.269%). 15m sits between the
  proven-dead 1m and the marginal 4h frame and clears nothing.
- **90d windows (newly added):** adding the shorter window does not rescue any
  strategy. Where a single 180d sub-cell occasionally looked positive (e.g.
  Trend Pullback 4h SOL/180d +1.229%, V2 4h SOL/180d +0.092%), the matching
  90d sub-cell is sharply negative (SOL 4h/90d -2.864% and -1.006%
  respectively). These are isolated lucky windows, not a stable edge — exactly
  the curve-fit the multi-window acceptance rule is designed to reject.

This also explains why an earlier exploratory per-symbol matrix (the now-retired
`edge_search.py` tool, which scored each symbol on a single window) looked more
optimistic. Those "positive" 4h V2 ETH/SOL cells do **not** survive the strict,
multi-window, multi-symbol acceptance rule used here: their aggregate across
90d+180d is negative (V2 4h -0.242%, PF 0.85). That exploratory tool and its
report have been removed so this strict sweep is the single canonical verdict.

## Outcome

- The live auto-disable gate stays **default-safe**: the validated allowlist
  (`validated_strategies.json`) is empty → the bot will NOT auto-trade.
- Manual trading remains available and unaffected.
- The honest "no profitable edge after fees on price-chart strategies alone"
  finding is confirmed at full resolution.
