---
name: Streamlit st.cache_data underscore params
description: Why a cached function returned the wrong data for different inputs.
---

# st.cache_data: leading-underscore params are NOT hashed

`@st.cache_data` builds its cache key from the function's args. **Any parameter
whose name starts with `_` is deliberately excluded from the key** (Streamlit's
escape hatch for unhashable args like DB connections).

**Why this matters:** if you name *all* params with a `_` prefix (e.g.
`def f(_sym, _interval, _use_auth, _limit)`), the cache key becomes effectively
constant, so the first call's result is returned for every subsequent call
regardless of arguments — e.g. the BTC 1m chart would be served for ETH 5m.

**How to apply:** only `_`-prefix the args you intentionally want ignored
(unhashable handles). Keep value-distinguishing args (symbol, interval, limit,
flags) un-prefixed so they participate in the cache key. Pair with a short
`ttl=` for data that must stay fresh (e.g. live chart candles).
