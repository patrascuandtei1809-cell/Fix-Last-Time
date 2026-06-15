import React from 'react';
import { exchangeOf } from '../utils';

export function Card({ title, value, sub, accent, children, className = '', glow = false }) {
  return (
    <div
      className={`card ${accent ? `card-${accent}` : ''} ${glow ? 'card-glow' : ''} ${className}`.trim()}
    >
      {title && <div className="card-lbl">{title}</div>}
      {value != null && <div className="card-val">{value}</div>}
      {sub && <div className="card-sub">{sub}</div>}
      {children}
    </div>
  );
}

export function MetricGrid({ children, className = '' }) {
  return <div className={`metric-grid ${className}`.trim()}>{children}</div>;
}

export function SectionTitle({ children, accent }) {
  return (
    <h2 className={`section-title ${accent ? `section-${accent}` : ''}`.trim()}>
      {children}
    </h2>
  );
}

export function TradeCard({ trade, venue }) {
  const ex = venue || exchangeOf(trade);
  const pnl = trade.net_pnl ?? trade.profit_loss;
  const pnlCls = pnl == null ? '' : Number(pnl) >= 0 ? 'up' : 'dn';
  const venueCls = ex === 'mexc' ? 'mexc' : 'binance';

  return (
    <div className={`trade-card ${venueCls}`}>
      <div className="tc-head">
        <span className="tc-coin mono">{trade.coin}</span>
        <span className={`tc-side ${trade.side === 'BUY' ? 'up' : 'dn'}`}>{trade.side}</span>
      </div>
      <div className="tc-row">
        <span className="tc-lbl">Entry</span>
        <span className="tc-val mono">
          {trade.entry_price != null ? `$${Number(trade.entry_price).toFixed(4)}` : '—'}
        </span>
      </div>
      <div className="tc-row">
        <span className="tc-lbl">Invested</span>
        <span className="tc-val mono">
          {trade.invested != null ? `$${Number(trade.invested).toFixed(2)}` : '—'}
        </span>
      </div>
      <div className="tc-row">
        <span className="tc-lbl">PnL</span>
        <span className={`tc-val mono ${pnlCls}`}>
          {pnl != null ? `$${Number(pnl).toFixed(4)}` : '—'}
        </span>
      </div>
      <div className="tc-foot">
        <span className="tc-status">{trade.status}</span>
        <span className="tc-ex">{ex.toUpperCase()}</span>
      </div>
    </div>
  );
}

export function TradeTable({ trades, empty = 'No trades.', compact = false }) {
  if (!trades?.length) {
    return <p className="muted empty-hint">{empty}</p>;
  }
  return (
    <div className={`table-wrap ${compact ? 'table-compact' : ''}`}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Coin</th>
            <th>Ex</th>
            <th>Side</th>
            <th>Entry</th>
            {!compact && <th>Exit</th>}
            <th>Invested</th>
            <th>PnL</th>
            <th>Status</th>
            {!compact && <th>Reason</th>}
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
                {!compact && (
                  <td className="mono">{t.exit_price != null ? `$${Number(t.exit_price).toFixed(4)}` : '—'}</td>
                )}
                <td className="mono">{t.invested != null ? `$${Number(t.invested).toFixed(2)}` : '—'}</td>
                <td className={`mono ${pnlCls}`}>
                  {pnl != null ? `$${Number(pnl).toFixed(4)}` : '—'}
                </td>
                <td>{t.status}</td>
                {!compact && (
                  <td className="reason" title={t.reason || t.close_reason}>
                    {(t.close_reason || t.reason || '—').slice(0, 48)}
                  </td>
                )}
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
