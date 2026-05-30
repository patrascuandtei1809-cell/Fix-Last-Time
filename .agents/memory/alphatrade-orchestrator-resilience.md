---
name: AlphaTrade orchestrator thread must be crash-proof
description: Why the bot "gets stuck after idle and needs restart-bot.sh", and the supervisor-loop rule.
---

# The orchestrator daemon thread must never die from an unhandled exception

Symptom the operator reported: leave the PC idle ~30 min and the bot "gets
stuck" — only a full `restart-bot.sh` recovers it.

**Why:** the bot trades from a single server-side daemon thread, independent of
the browser. The dashboard's `_maybe_resume_bot()` watchdog only revives a dead
bot on a Streamlit *rerun*, and reruns are driven by `st_autorefresh` (client-
side JS). When the browser sits idle the timer is throttled / the websocket
drops, so reruns stop and the watchdog stops. If the orchestrator thread *also*
died (an unhandled exception in the cycle body escaping `while self._running`),
nothing was left to revive it → permanently stuck until a process restart.

**Rule:** the thread target must be a thin **supervisor loop** that wraps the
real cycle in `while self._running: try: _inner_loop() except Exception: log +
backoff + re-enter`. The recovery handler itself must be wrapped in its own
try/except (a failing `log_activity` in the handler would re-kill the thread),
and crash alerts must be throttled (per exception-signature, e.g. 5 min) so a
persistent fault doesn't spam every backoff. The ONLY intentional exits remain
operator Stop and the daily-loss breaker (both set `self._running=False`).

**How to apply:** never rely on a UI rerun/watchdog to keep a trading thread
alive — the server thread must be self-healing on its own. Caveat: this catches
`Exception`, not `BaseException`, and does not rescue a *hung* (not crashed)
call — so all network calls still need request timeouts (Binance public klines/
price and the OpenAI client already set them).
