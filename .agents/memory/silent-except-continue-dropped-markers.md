---
name: try/except:continue can silently hide a NameError that drops all chart markers
description: Why AlphaTrade BUY/SELL chart markers vanished — an undefined var swallowed by a per-item except.
---

# A per-item `try/except: continue` will silently eat a NameError and drop EVERY item

Symptom: BUY/SELL trade markers stopped rendering on the dashboard candlestick
chart — no error shown, candles fine, just no markers.

**Root cause class:** the marker loop converts each trade timestamp via a helper
that reads a variable (`_xtz`, the chart tz). That variable was only assigned in
ONE branch of an earlier if/else (the no-data path). When a refactor added a
second branch (bounded-window, the normal data-present path) that did NOT set it,
the helper raised `NameError` on the FIRST trade — and because the loop body was
wrapped in `try: ... except Exception: continue`, every single marker was
silently skipped. Looks like "feature removed," is actually "100% caught
exception."

**Why it's dangerous:** a broad per-item `except: continue` turns a hard crash
(which you'd notice) into invisible empty output. A var that is conditionally
defined upstream is a latent landmine for it.

**How to apply:** when a render loop produces NOTHING (not "some rows wrong"),
suspect a swallowed exception in its `try/except`, not a data problem. Grep where
every variable the loop reads is *assigned* and confirm it's set on ALL code
paths before the loop — especially after someone adds a new if/else branch. Define
shared render vars (timezone, formatters, scalers) UNCONDITIONALLY before the
branching that might skip them.
