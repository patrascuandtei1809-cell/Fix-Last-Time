const API_BASE = import.meta.env.VITE_API_BASE || '';

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`${path} → HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => get('/api/health'),
  tradesOpen: () => get('/api/trades/open'),
  tradesClosed: () => get('/api/trades/closed'),
  tradesHistory: () => get('/api/trades/history'),
  scanner: () => get('/api/scanner'),
  activity: () => get('/api/activity'),
  performance: () => get('/api/performance'),
};

export async function fetchAllDashboardData() {
  const [health, open, closed, history, scanner, activity, performance] =
    await Promise.all([
      api.health(),
      api.tradesOpen(),
      api.tradesClosed(),
      api.tradesHistory(),
      api.scanner(),
      api.activity(),
      api.performance(),
    ]);
  return { health, open, closed, history, scanner, activity, performance };
}
