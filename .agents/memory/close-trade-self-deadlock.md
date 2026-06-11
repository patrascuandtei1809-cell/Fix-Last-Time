---
name: close_trade self-deadlock
description: bot.close_trade re-enters the non-reentrant _file_lock via log_activity, hanging any caller (incl. the bot daemon thread and tests).
---
# close_trade() self-deadlock via log_activity

`trading/bot.py` `_file_lock = threading.Lock()` is **non-reentrant**.
`close_trade()` does its work inside `with _file_lock:` and, before returning,
calls `log_activity(...)` which → `_append_activity()` → `with _file_lock:`
again. Re-acquiring a non-reentrant lock on the same thread blocks forever.

**Confirmed:** a clean repro (write one open trade, call close_trade) hangs
(RC=124). It is wired as the bot's `on_close_trade` callback (bot.py ~1681)
and called by dashboard manual-close + diagnostics, so a real auto/manual
close can freeze the calling thread AFTER the trade file is already saved
(_save_trade_file runs before log_activity), i.e. close persists but the
thread hangs.

**Why it matters:** could silently freeze the bot daemon thread on the first
SL/TP close. Discovered during the UTC-timestamp task (#48); NOT fixed there
to stay in scope.

**How to apply / fix options:** make `_file_lock` an `RLock`, OR move the
`log_activity(...)` call outside the `with _file_lock:` block (after it
releases). Any test that exercises close_trade must monkeypatch
`bot.log_activity` to a no-op until this is fixed (see
tests/test_trade_timestamp_utc.py).
