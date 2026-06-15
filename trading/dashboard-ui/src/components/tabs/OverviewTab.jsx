import React from 'react';
import { Card, MetricGrid, SectionTitle, TradeTable } from '../Ui';
import { fmtUsd, sumInvested } from '../../utils';

export function OverviewTab({ data }) {
  const open = data?.open?.data || [];
  const perf = data?.performance?.data?.summary;
  const scanner = data?.scanner?.data;
  const deployed = sumInvested(open);

  return (
    <div className="tab-panel">
      <MetricGrid>
        <Card title="Deployed USDT (open)" value={fmtUsd(deployed)} sub="Sum of open position invested — from trade files" accent="binance" />
        <Card title="Open positions" value={open.length} sub={`Binance ${data?.open?.by_exchange?.binance?.length || 0} · MEXC ${data?.open?.by_exchange?.mexc?.length || 0}`} />
        <Card title="Closed net PnL" value={fmtUsd(perf?.total_net, 4)} sub={`${perf?.count || 0} closed · win ${(perf?.win_rate || 0).toFixed(1)}%`} accent={Number(perf?.total_net) >= 0 ? 'green' : 'red'} />
        <Card title="Scanner top picks" value={scanner?.opportunities?.length ?? '—'} sub={scanner?.updated_at ? `Last scan ${scanner.updated_at}` : 'No scan file yet'} accent="mexc" />
      </MetricGrid>

      <SectionTitle>Recent open positions</SectionTitle>
      <TradeTable trades={open.slice(0, 8)} empty="No open trades in data/trades/*.json" />
    </div>
  );
}
