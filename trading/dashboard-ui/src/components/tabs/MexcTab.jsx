import React from 'react';
import { Card, MetricGrid, SectionTitle, TradeCard, TradeTable } from '../Ui';
import { fmtUsd, sumInvested } from '../../utils';

export function MexcTab({ data }) {
  const open = data?.open?.by_exchange?.mexc || [];
  const closed = data?.closed?.by_exchange?.mexc || [];
  const perf = data?.performance?.data?.by_exchange?.mexc;
  const deployed = sumInvested(open);

  return (
    <div className="tab-panel venue-mexc">
      <div className="venue-hero mexc">
        <div className="venue-hero-glow" aria-hidden="true" />
        <div className="venue-hero-content">
          <span className="venue-badge mexc">MEXC</span>
          <h1 className="venue-title">Scanner Alts Terminal</h1>
          <p className="venue-desc">Volatile alts · max 15 positions · read-only</p>
          <div className="venue-hero-stats">
            <div className="venue-stat">
              <span className="vs-lbl">Open</span>
              <span className="vs-val mono purple">{open.length}</span>
            </div>
            <div className="venue-stat">
              <span className="vs-lbl">Deployed</span>
              <span className="vs-val mono purple">{fmtUsd(deployed)}</span>
            </div>
            <div className="venue-stat">
              <span className="vs-lbl">Net PnL</span>
              <span className={`vs-val mono ${Number(perf?.total_net) >= 0 ? 'up' : 'dn'}`}>
                {fmtUsd(perf?.total_net, 4)}
              </span>
            </div>
          </div>
        </div>
      </div>

      <MetricGrid>
        <Card title="Open positions" value={open.length} accent="mexc" glow />
        <Card title="Deployed USDT" value={fmtUsd(deployed)} accent="mexc" glow />
        <Card title="Closed trades" value={closed.length} accent="mexc" />
        <Card
          title="Net PnL"
          value={fmtUsd(perf?.total_net, 4)}
          sub={`Win rate ${(perf?.win_rate || 0).toFixed(1)}%`}
          accent={Number(perf?.total_net) >= 0 ? 'green' : 'red'}
          glow
        />
      </MetricGrid>

      <SectionTitle accent="mexc">Open positions</SectionTitle>
      {!open.length ? (
        <p className="muted empty-hint">No open MEXC trades.</p>
      ) : (
        <div className="trade-card-grid mexc-grid">
          {open.map((t) => (
            <TradeCard key={t.id || `${t.coin}-${t.open_time}`} trade={t} venue="mexc" />
          ))}
        </div>
      )}

      <SectionTitle accent="mexc">Recent closed</SectionTitle>
      <TradeTable trades={closed.slice(0, 20)} empty="No closed MEXC trades." compact />
    </div>
  );
}
