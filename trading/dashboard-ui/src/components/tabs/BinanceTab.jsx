import React from 'react';
import { Card, MetricGrid, SectionTitle, TradeTable } from '../Ui';
import { fmtUsd, sumInvested } from '../../utils';

export function BinanceTab({ data }) {
  const open = data?.open?.by_exchange?.binance || [];
  const closed = data?.closed?.by_exchange?.binance || [];
  const perf = data?.performance?.data?.by_exchange?.binance;

  return (
    <div className="tab-panel venue-binance">
      <div className="venue-banner binance">
        <h1>🟡 BINANCE DASHBOARD</h1>
        <p>Pinned majors BTC · ETH · SOL · read-only from FastAPI trade files</p>
      </div>
      <MetricGrid>
        <Card title="Open" value={open.length} accent="binance" />
        <Card title="Deployed" value={fmtUsd(sumInvested(open))} accent="binance" />
        <Card title="Closed" value={closed.length} />
        <Card title="Net PnL" value={fmtUsd(perf?.total_net, 4)} accent={Number(perf?.total_net) >= 0 ? 'green' : 'red'} />
      </MetricGrid>
      <SectionTitle accent="binance">Open trades</SectionTitle>
      <TradeTable trades={open} empty="No open Binance trades." />
      <SectionTitle accent="binance">Recent closed</SectionTitle>
      <TradeTable trades={closed.slice(0, 20)} empty="No closed Binance trades." />
    </div>
  );
}
