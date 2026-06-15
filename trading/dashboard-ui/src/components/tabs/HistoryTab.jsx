import React from 'react';
import { SectionTitle, TradeTable, Warnings } from '../Ui';

export function HistoryTab({ data }) {
  const history = data?.history;
  const trades = [...(history?.data || [])].reverse();

  return (
    <div className="tab-panel">
      <SectionTitle>Trade history</SectionTitle>
      <Warnings items={history?.warnings} />
      <p className="muted">{history?.count ?? 0} trades from data/trades/*.json</p>
      <TradeTable trades={trades} empty="No trade history files yet." />
    </div>
  );
}
