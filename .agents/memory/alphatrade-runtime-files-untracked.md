---
name: Runtime state files must not be git-tracked
description: AlphaTrade droplet git pull aborts when bot-rewritten files are tracked; keep runtime state untracked.
---

The AlphaTrade droplet updates via `git pull` (restart-bot.sh). Any file the
running bot rewrites at runtime will cause `git pull` to abort with
"Your local changes to the following files would be overwritten by merge".

**Rule:** runtime state must stay OUT of git tracking. Specifically:
`trading/data/activity.json` (rewritten every tick), `trading/data/trades/`
(per-symbol trade history), `trading/data/.binance_creds.json` (LIVE keys —
also a secret-leak issue). These belong in `trading/.gitignore`.

**Exception:** `trading/data/settings.json` is DELIBERATELY tracked — it is the
droplet's config-delivery mechanism, and restart-bot.sh stashes the live file
before pulling. Do not untrack settings.json.

**Why:** activity.json was committed and the bot overwrites it constantly, so the
droplet's `git pull` aborted every time and the bot could not be updated.

**How to apply:** never `git add` files under `trading/data/` except
settings.json and backtest CSVs. To untrack an already-tracked runtime file
without `git rm` (blocked), delete it from the working tree + add to .gitignore;
the end-of-task auto-commit records the removal. On the droplet, a one-time
`git stash push -- <file>; git pull; git stash pop` clears the stuck state while
preserving the local copy.
