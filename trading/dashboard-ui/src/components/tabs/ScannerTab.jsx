import React from 'react';
import { Card, MetricGrid, SectionTitle } from '../Ui';

export function ScannerTab({ data }) {
  const scanner = data?.scanner;
  const payload = scanner?.data || {};
  const raw = payload.count_raw || {};
  const opps = payload.opportunities || [];
  const rejects = payload.rejection_samples || [];

  return (
    <div className="tab-panel scanner-panel">
      <div className="scanner-hero mexc">
        <div className="venue-hero-glow" aria-hidden="true" />
        <div className="venue-hero-content">
          <span className="venue-badge mexc">SCANNER</span>
          <h1 className="venue-title">Universe Scanner</h1>
          <p className="venue-desc">
            {payload.updated_at
              ? `Last run ${payload.updated_at.slice(0, 19)} UTC`
              : 'Awaiting scanner output'}
          </p>
        </div>
      </div>

      <MetricGrid>
        <Card title="Raw symbols" value={payload.count_raw_total ?? '—'} accent="mexc" glow />
        <Card title="Binance raw" value={raw.binance ?? '—'} accent="binance" glow />
        <Card title="MEXC raw" value={raw.mexc ?? '—'} accent="mexc" glow />
        <Card title="Scored" value={payload.count_scored ?? '—'} glow />
        <Card title="Top selected" value={opps.length} accent="mexc" glow />
        <Card
          title="MEXC scored"
          value={payload.count_mexc_scored ?? '—'}
          sub={payload.updated_at ? payload.updated_at.slice(0, 19) : '—'}
          accent="mexc"
        />
      </MetricGrid>

      <SectionTitle accent="mexc">Top 15 opportunities</SectionTitle>
      {!opps.length ? (
        <p className="muted empty-hint">No scanner opportunities in API response.</p>
      ) : (
        <div className="table-wrap glow-mexc">
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
                  <td className="mono muted">{i + 1}</td>
                  <td className="mono bold">{o.symbol}</td>
                  <td className="up mono">{o.score ?? '—'}</td>
                  <td className="mono">{o.volatility != null ? `${Number(o.volatility).toFixed(1)}%` : '—'}</td>
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
