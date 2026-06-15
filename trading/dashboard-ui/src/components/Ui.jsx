import React from 'react';

export function Card({ title, value, sub, accent, children, className = '' }) {
  return (
    <div className={`card ${accent ? `card-${accent}` : ''} ${className}`.trim()}>
      {title && <div className="card-lbl">{title}</div>}
      {value != null && <div className="card-val">{value}</div>}
      {sub && <div className="card-sub">{sub}</div>}
      {children}
    </div>
  );
}

export function MetricGrid({ children }) {
  return <div className="metric-grid">{children}</div>;
}

export function SectionTitle({ children, accent }) {
  return (
    <h2 className={`section-title ${accent ? `section-${accent}` : ''}`.trim()}>
      {children}
    </h2>
  );
}

export function TradeTable({ trades, empty = 'No trades.' }) {
  if (!trades?.length) {
    return <p className="muted">{empty}</p>;
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Coin</th>
            <th>Ex</th>
            <th>Side</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Invested</th>
            <th>PnL</th>
            <th>Status</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const pnl = t.net_pnl ?? t.profit_loss;
            const pnlCls = pnl == null ? '' : Number(pnl) >= 0 ? 'up' : 'dn';
            return (
              <tr key={t.id || `${t.coin}-${t.open_time}`}>
                <td className="mono bold">{t.coin}</td>
                <td>{(t.exchange || 'binance').replace(/USDT/i, '')}</td>
                <td className={t.side === 'BUY' ? 'up' : 'dn'}>{t.side}</td>
                <td className="mono">{t.entry_price != null ? `$${Number(t.entry_price).toFixed(4)}` : '—'}</td>
                <td className="mono">{t.exit_price != null ? `$${Number(t.exit_price).toFixed(4)}` : '—'}</td>
                <td className="mono">{t.invested != null ? `$${Number(t.invested).toFixed(2)}` : '—'}</td>
                <td className={`mono ${pnlCls}`}>
                  {pnl != null ? `$${Number(pnl).toFixed(4)}` : '—'}
                </td>
                <td>{t.status}</td>
                <td className="reason" title={t.reason || t.close_reason}>
                  {(t.close_reason || t.reason || '—').slice(0, 48)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Warnings({ items }) {
  if (!items?.length) return null;
  return (
    <div className="warnings">
      {items.map((w, i) => (
        <div key={i} className="warning-item">
          <strong>{w.code}</strong>: {w.message?.split('\n')[0]}
        </div>
      ))}
    </div>
  );
}
