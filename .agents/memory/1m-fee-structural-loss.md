---
name: 1m scalping is structurally unprofitable on Binance spot
description: Why every 1m strategy backtest (reversal AND strict trend-confluence) fails after fees, and what would actually be needed to find an edge.
---

# 1m scalping fails after fees — it's the timeframe, not the entry filter

Two completely different 1m strategies have now failed the full 9-cell backtest
(BTC/ETH/SOL × 30/90/180d) with nearly identical results:

- **Reversal Scalper** (reactive, fires ~48 trades/day/symbol): ~−0.24%/trade, PF 0.02–0.10.
- **EMA_MACD_RSI_VOLUME_V2** (strict LONG-only trend confluence: EMA50>EMA200 +
  RSI>55 + MACD hist>0 + vol>1.5×20-bar + regime∉{RANGE,DEAD}, ATR SL×1.5/TP×3):
  ~−0.23 to −0.24%/trade, PF 0.02–0.11, win rate 5–14%, REJECTED on all 9 cells.

**Why:** Binance spot taker round-trip cost ≈ 0.24% (2 × (0.1% fee + 0.02% slip)).
On 1m candles the ATR-scaled target (ATR×3 ≈ 0.06–0.15% of price) is SMALLER than
that 0.24% hurdle, so even a gross-TP win records as a NET loss. In the backtest's
gross/fees/net split, **fees are 2–6× the entire gross price move** — e.g. SOL 180d
gross −104% but fees −674%. The avg loss ≈ −0.24% ≈ the fee drag itself.

**The lesson:** entry quality CANNOT overcome a timeframe/fee mismatch. Tightening
filters only reduces trade count (V2 cut frequency ~67%, still ~16 trades/day/symbol),
not the per-trade fee hurdle. Stricter confluence on 1m still fires often because 1m
is noisy.

**What would actually be needed before any 1m strategy can win** (none yet validated):
- Higher timeframe (15m/1h+) so targets dwarf the 0.24% cost, OR
- Maker (limit) entries/exits to pay ~0.0% instead of 0.1% taker, OR
- A genuinely different, validated edge — not a re-tuned indicator stack.

**How to apply:** When asked to "make the bot trade / be more aggressive / add a new
1m indicator strategy", do NOT promise profit. Run the backtest first; expect rejection
on 1m. The auto-reject rule (PF<1 OR expectancy<0 after fees → REJECT) lives in
`suite_runner.report()`. A rejected strategy must NOT be wired as the live
auto-trading default — replacing one proven loser with another is not progress.
