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

**FIXED (June 2026):** `close_trade()` now captures the closed dict + the
state-log string inside the lock, breaks out, and calls `log_activity()` only
AFTER `with _file_lock:` releases. `add_trade()` already logged outside its
lock; no other lock-held path calls `log_activity`. Tests may now drive the
REAL `close_trade` (redirect `bot.ACTIVITY_FILE` into tmp_path) without
needing the old `log_activity` no-op monkeypatch.

**Rule:** never call `log_activity()`/`_append_activity()` (or anything that
re-takes `_file_lock`) from inside a `with _file_lock:` block — `_file_lock`
is a plain non-reentrant `threading.Lock`. Collect the message, release, log.
