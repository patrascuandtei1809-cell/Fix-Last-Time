---
name: Trading Streamlit dashboard is not a registered artifact
description: Why the screenshot tool can't target the trading/ dashboard and how to verify it instead.
---

# The `trading/` Streamlit dashboard is a standalone workflow, not an artifact

`trading/dashboard.py` runs via the `AlphaTrade Dashboard` workflow on port 5000. It is
NOT one of the registered artifacts (only `api-server` and `mockup-sandbox` are), so:

- `screenshot type=app_preview artifact_dir_name=trading` fails ("Artifact not found").
- `screenshot type=external_url` against the dev-domain ROOT routes to the mockup-sandbox
  (`/__mockup`, "Component Preview Server"), not the Streamlit app — the shared proxy only
  routes registered artifacts by path; the trading app on :5000 is not in that table.

**How to verify the dashboard instead:** restart the `AlphaTrade Dashboard` workflow
(it auto-captures a preview), check `/tmp/logs/AlphaTrade_Dashboard_*.log` for clean
startup + the `[SETTINGS] loaded N keys` line, and `curl -s -o /dev/null -w "%{http_code}"
http://localhost:5000/` (expect 200). Binance auto-connect ALWAYS fails on Replit with a
451 "restricted location" — that is environment geo-blocking, not a code bug.
