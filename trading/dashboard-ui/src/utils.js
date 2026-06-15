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
