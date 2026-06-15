import React from 'react';
import { Card, MetricGrid, SectionTitle, Warnings } from '../Ui';

export function ScannerTab({ data }) {
  const scanner = data?.scanner;
  const payload = scanner?.data || {};
  const raw = payload.count_raw || {};
  const opps = payload.opportunities || [];
  const rejects = payload.rejection_samples || [];

  return (
    <div className="tab-panel">
      <SectionTitle accent="mexc">Scanner universe</SectionTitle>
      <Warnings items={scanner?.warnings} />
      <MetricGrid>
        <Card title="Raw symbols" value={payload.count_raw_total ?? '—'} accent="mexc" />
        <Card title="Binance raw" value={raw.binance ?? '—'} />
        <Card title="MEXC raw" value={raw.mexc ?? '—'} accent="mexc" />
        <Card title="Scored" value={payload.count_scored ?? '—'} />
        <Card title="Top selected" value={opps.length} accent="mexc" />
        <Card title="Last run" value={payload.updated_at ? payload.updated_at.slice(0, 19) : '—'} sub={`MEXC scored: ${payload.count_mexc_scored ?? '—'}`} />
      </MetricGrid>

      <SectionTitle accent="mexc">Top 15 opportunities</SectionTitle>
      {!opps.length ? (
        <p className="muted">No scanner opportunities in API response.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Symbol</th>
                <th>Score</th>
                <th>Vol %</th>
                <th>Volume $</th>
                <th>Exchange</th>
              </tr>
            </thead>
            <tbody>
              {opps.map((o, i) => (
                <tr key={o.symbol || i}>
                  <td>{i + 1}</td>
                  <td className="mono bold">{o.symbol}</td>
                  <td className="up">{o.score ?? '—'}</td>
                  <td>{o.volatility != null ? `${Number(o.volatility).toFixed(1)}%` : '—'}</td>
                  <td className="mono">{o.volume != null ? `$${Number(o.volume).toLocaleString()}` : '—'}</td>
                  <td>{o.exchange}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rejects.length > 0 && (
        <>
          <SectionTitle>Rejection samples</SectionTitle>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {rejects.slice(0, 20).map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{r.symbol}</td>
                    <td className="reason">{r.rejection}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
