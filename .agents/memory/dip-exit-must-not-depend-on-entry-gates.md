---
name: Exit management must not depend on entry-only gates
description: In the AlphaTrade dip live engine, managing an open position's stop-loss/take-profit must run before entry-only gates so it can never be stranded.
---

In a LIVE money bot, an already-open position's exit (TAKE-PROFIT / STOP-LOSS)
must be evaluated as early as possible — right after the price is known — and
must require ONLY the current price.

**Rule:** the exit branch runs before safe-mode, balance, klines, spending-limit,
max-position, and cooldown checks. Those gate NEW ENTRIES only.

**Why:** a transient klines/balance API outage, or the operator toggling safe
mode, must never prevent a -1.50% stop-loss from firing on a position the bot
already holds. Code review caught a version where position-detection happened
after klines+balance fetch, so an API hiccup could silently abandon the
stop-loss. Emergency stop is the ONE control that halts everything (including
exits) — it is a true halt.

**How to apply:** when ordering gates in any live-trading evaluate() pipeline,
split "can I OPEN a new trade" gates from "must I CLOSE an existing one". Run the
close path first using only price. Keep the dashboard copy ("existing TP/SL still
run while safe mode blocks new entries") consistent with this behavior.
