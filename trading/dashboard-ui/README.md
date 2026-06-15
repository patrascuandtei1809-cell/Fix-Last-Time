# AlphaTrade React Dashboard (Phase B)

Read-only professional trading terminal. **Data source: FastAPI only** (`:8000`).

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

## Production build

```bash
# If API is on same host, nginx can proxy /api to :8000:
npm run build

# Or bake API URL into the build:
VITE_API_BASE=http://YOUR_DROPLET_IP:8000 npm run build
```

Serve `dist/` with nginx, `vite preview`, or `npx serve -s dist -l 3000`.

## Stack

- React 18 + Vite
- Polls all read-only endpoints every **3 seconds**
- No full page reloads — React state updates only
