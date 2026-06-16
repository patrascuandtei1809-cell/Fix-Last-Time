# AlphaTrade React Dashboard (Phase B) — **EXPERIMENTAL**

> **Experimental read-only preview.** Production operator terminal is Streamlit
> (`trading/dashboard.py` on port **8501**). This React UI is optional and may
> lag behind Streamlit; do not use it for trading controls.

Read-only professional crypto trading terminal. **Data source: FastAPI only** (`:8000`).

Streamlit (`:8501`) and the live bot are unchanged.

## Prerequisites

- Node.js 18+ and npm
- FastAPI running: `uvicorn api_server:app --port 8000` (from `../`)

## Install

```bash
cd trading/dashboard-ui
npm install
```

## Local development (port 3000)

```bash
npm run dev
```

Open http://localhost:3000 — Vite proxies `/api/*` → `http://127.0.0.1:8000`.

`.env.development` leaves `VITE_API_BASE` empty so all API calls use relative `/api/...` paths through the dev proxy.

## Production build

Production API base is baked in via `.env.production`:

```
VITE_API_BASE=http://134.122.107.236:8000
```

```bash
npm run build
```

Vite loads `.env.production` automatically for `npm run build`, so the built assets call the droplet IP — **not localhost**.

To override for a different host:

```bash
VITE_API_BASE=http://YOUR_DROPLET_IP:8000 npm run build
```

Serve `dist/` with nginx, `vite preview`, or `npx serve -s dist -l 3000`.

## API base resolution

`src/api.js` uses:

```js
const API_BASE = import.meta.env.VITE_API_BASE || '';
```

- **Development:** empty base → `/api/health` etc. hit the Vite proxy.
- **Production:** `http://134.122.107.236:8000` → full URL to the droplet FastAPI.

## Polling behavior

- Polls all read-only endpoints every **3 seconds**
- **Stale-while-revalidate:** previous data stays visible during refresh
- First load shows skeleton placeholders; subsequent polls show a subtle sync dot in the top bar
- API errors set a pill in the top bar but never wipe loaded data

## Stack

- React 18 + Vite
- JetBrains Mono metrics · Binance gold / MEXC purple glow theme
- No full page reloads — React state updates only
