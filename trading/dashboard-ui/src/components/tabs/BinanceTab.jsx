import React from 'react';
import { Card, MetricGrid, SectionTitle, TradeCard, TradeTable } from '../Ui';
import { fmtUsd, sumInvested } from '../../utils';

export function BinanceTab({ data }) {
  const open = data?.open?.by_exchange?.binance || [];
  const closed = data?.closed?.by_exchange?.binance || [];
  const perf = data?.performance?.data?.by_exchange?.binance;
  const deployed = sumInvested(open);

  return (
    <div className="tab-panel venue-binance">
      <div className="venue-hero binance">
        <div className="venue-hero-glow" aria-hidden="true" />
        <div className="venue-hero-content">
          <span className="venue-badge">BINANCE</span>
          <h1 className="venue-title">Major Pairs Terminal</h1>
          <p className="venue-desc">BTC · ETH · SOL — read-only from FastAPI trade files</p>
          <div className="venue-hero-stats">
            <div className="venue-stat">
              <span className="vs-lbl">Open</span>
              <span className="vs-val mono gold">{open.length}</span>
            </div>
            <div className="venue-stat">
              <span className="vs-lbl">Deployed</span>
              <span className="vs-val mono gold">{fmtUsd(deployed)}</span>
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
        <Card title="Open positions" value={open.length} accent="binance" glow />
        <Card title="Deployed USDT" value={fmtUsd(deployed)} accent="binance" glow />
        <Card title="Closed trades" value={closed.length} accent="binance" />
        <Card
          title="Net PnL"
          value={fmtUsd(perf?.total_net, 4)}
          sub={`Win rate ${(perf?.win_rate || 0).toFixed(1)}%`}
          accent={Number(perf?.total_net) >= 0 ? 'green' : 'red'}
          glow
        />
      </MetricGrid>

      <SectionTitle accent="binance">Open positions</SectionTitle>
      {!open.length ? (
        <p className="muted empty-hint">No open Binance trades.</p>
      ) : (
        <div className="trade-card-grid binance-grid">
          {open.map((t) => (
            <TradeCard key={t.id || `${t.coin}-${t.open_time}`} trade={t} venue="binance" />
          ))}
        </div>
      )}

      <SectionTitle accent="binance">Recent closed</SectionTitle>
      <TradeTable trades={closed.slice(0, 20)} empty="No closed Binance trades." compact />
    </div>
  );
}
