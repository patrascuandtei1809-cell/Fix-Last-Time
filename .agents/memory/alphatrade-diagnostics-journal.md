---
name: AlphaTrade diagnostics journal & ghost reconcile
description: Invariants for the diagnostics decision-journal attribution and ghost-trade auto-reconcile so they stay accurate and safe.
---

# Diagnostics decision-journal attribution must mirror the orchestrator gate ORDER

The "WHY NO TRADE?" reason for each symbol is reconstructed in `diagnostics._derive_reason`
from a per-cycle snapshot, NOT read from the orchestrator. So it must follow the SAME gate
order the orchestrator actually uses: directional signal → score>0 → (score>=threshold OR
conf>=confidence_floor) → one winner per cycle → GPT veto → global cap → throttle → worker block.
If the orchestrator's entry rule changes, update `_derive_reason` in lockstep or the report lies.

**Why:** The orchestrator nulls `winner` when GPT vetoes, so passing `(winner or {}).get("symbol")`
makes the vetoed symbol look "awaiting selection" instead of "GPT veto". Fix: bot.py passes the
SELECTED symbol (pre-veto top qualified candidate) as `winner_symbol`, and `_derive_reason` only
emits the GPT reason when `gpt_block` is set for that selected symbol.

**How to apply:** When editing entry gates in `bot._inner_loop`, re-check `_derive_reason` and the
`record_cycle(...)` call args (especially `winner_symbol` / `gpt_block`).

# Ghost-trade auto-reconcile must close ONLY true dust, never a fraction of recorded qty

`diagnostics.reconcile_ghost_trades` runs automatically every 300s with dry_run=False and mutates
real local trade state. It must auto-close a spot BUY only when the base-asset Binance total is at
or below `min_qty` (dust / 1e-8 fallback). A partial holding (some balance, less than recorded) is a
MISMATCH — logged and left OPEN, never auto-closed. Closes at ENTRY price (P&L=0) — never invent P&L
from current price. Compare against TOTAL base balance across all open BUYs for that asset.

**Why:** An earlier threshold of `max(min_qty, recorded_qty*0.5)` would close a position that still
had a real (just smaller) balance — falsely closing real trades and corrupting caps/state.

**How to apply:** Keep the dust rule independent of recorded qty. Any change here needs the
multi-trade-same-base + partial-hold unit cases (ghost→closed, partial→mismatch, full→untouched).
