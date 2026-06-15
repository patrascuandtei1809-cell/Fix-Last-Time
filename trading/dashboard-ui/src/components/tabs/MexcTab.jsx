import React from 'react';
import { Card, MetricGrid, SectionTitle, TradeTable } from '../Ui';
import { fmtUsd, sumInvested } from '../../utils';

export function MexcTab({ data }) {
  const open = data?.open?.by_exchange?.mexc || [];
  const closed = data?.closed?.by_exchange?.mexc || [];
  const perf = data?.performance?.data?.by_exchange?.mexc;

  return (
    <div className="tab-panel venue-mexc">
      <div className="venue-banner mexc">
        <h1>🔵 MEXC DASHBOARD</h1>
        <p>Scanner-driven volatile alts · max 15 positions · read-only</p>
      </div>
      <MetricGrid>
        <Card title="Open" value={open.length} accent="mexc" />
        <Card title="Deployed" value={fmtUsd(sumInvested(open))} accent="mexc" />
        <Card title="Closed" value={closed.length} />
        <Card title="Net PnL" value={fmtUsd(perf?.total_net, 4)} accent={Number(perf?.total_net) >= 0 ? 'green' : 'red'} />
      </MetricGrid>
      <SectionTitle accent="mexc">Open trades</SectionTitle>
      <TradeTable trades={open} empty="No open MEXC trades." />
      <SectionTitle accent="mexc">Recent closed</SectionTitle>
      <TradeTable trades={closed.slice(0, 20)} empty="No closed MEXC trades." />
    </div>
  );
}
