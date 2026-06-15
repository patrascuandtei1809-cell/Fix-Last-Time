import React from 'react';
import { Card, MetricGrid, SectionTitle, Warnings } from '../Ui';
import { fmtUsd } from '../../utils';

export function PerformanceTab({ data }) {
  const perf = data?.performance;
  const d = perf?.data || {};
  const summary = d.summary || {};

  return (
    <div className="tab-panel">
      <SectionTitle>Performance</SectionTitle>
      <Warnings items={perf?.warnings} />
      <MetricGrid>
        <Card title="Total trades" value={d.total_trades ?? 0} />
        <Card title="Closed" value={d.closed_count ?? 0} />
        <Card title="Wins" value={summary.wins ?? 0} accent="green" />
        <Card title="Win rate" value={`${(summary.win_rate || 0).toFixed(1)}%`} />
        <Card title="Gross PnL" value={fmtUsd(summary.total_gross, 4)} />
        <Card title="Net PnL" value={fmtUsd(summary.total_net, 4)} accent={Number(summary.total_net) >= 0 ? 'green' : 'red'} />
        <Card title="Avg win" value={fmtUsd(summary.avg_win, 4)} accent="green" />
        <Card title="Avg loss" value={fmtUsd(summary.avg_loss, 4)} accent="red" />
      </MetricGrid>

      <SectionTitle>By symbol (top)</SectionTitle>
      {!d.by_symbol?.length ? (
        <p className="muted">No closed trades for symbol breakdown.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Trades</th>
                <th>Win %</th>
                <th>Total PnL</th>
              </tr>
            </thead>
            <tbody>
              {d.by_symbol.map((row) => (
                <tr key={row.Symbol}>
                  <td className="mono bold">{row.Symbol}</td>
                  <td>{row.Trades}</td>
                  <td>{row['Win %']}%</td>
                  <td className={Number(row['Total PnL $']) >= 0 ? 'up' : 'dn'}>
                    ${Number(row['Total PnL $']).toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <SectionTitle>By hour (UTC)</SectionTitle>
      {!d.by_hour_utc?.length ? (
        <p className="muted">No hourly breakdown yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Hour UTC</th>
                <th>Trades</th>
                <th>PnL</th>
              </tr>
            </thead>
            <tbody>
              {d.by_hour_utc.map((row) => (
                <tr key={row['Hour (UTC)']}>
                  <td>{row['Hour (UTC)']}:00</td>
                  <td>{row.Trades}</td>
                  <td className={Number(row['PnL $']) >= 0 ? 'up' : 'dn'}>${Number(row['PnL $']).toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
