export function fmtUsd(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  const sign = v >= 0 ? '' : '-';
  return `${sign}$${Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function fmtPct(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

export function sumInvested(trades = []) {
  return trades.reduce((s, t) => s + (Number(t.invested) || 0), 0);
}

export function exchangeOf(trade) {
  const ex = (trade?.exchange || 'binance').toLowerCase();
  return ex.includes('mexc') ? 'mexc' : 'binance';
}

export function botStatus(health) {
  const hb = health?.heartbeats?.bot;
  if (!hb) return { label: 'UNKNOWN', ok: false };
  if (hb.stale) return { label: 'STALE', ok: false };
  return { label: 'ACTIVE', ok: true };
}

export function scannerStatus(scanner, health) {
  if (scanner?.ok && scanner?.data?.updated_at) {
    return { label: 'LIVE', ok: true, at: scanner.data.updated_at };
  }
  const hb = health?.heartbeats?.scanner;
  if (hb && !hb.stale) return { label: 'HEARTBEAT', ok: true, at: hb.at };
  return { label: 'OFFLINE', ok: false, at: null };
}

export function formatTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function todayUtcDateString() {
  return new Date().toISOString().slice(0, 10);
}

export function dailyPnlFromClosed(closedTrades = []) {
  const today = todayUtcDateString();
  return closedTrades.reduce((sum, t) => {
    const ct = t.close_time || t.closed_at || '';
    if (!String(ct).startsWith(today)) return sum;
    const pnl = t.net_pnl ?? t.profit_loss;
    return sum + (Number(pnl) || 0);
  }, 0);
}

export function closedTodayCount(closedTrades = []) {
  const today = todayUtcDateString();
  return closedTrades.filter((t) => {
    const ct = t.close_time || t.closed_at || '';
    return String(ct).startsWith(today);
  }).length;
}
