import React from 'react';
import { Card, MetricGrid, SectionTitle, TradeCard, TradeTable } from '../Ui';
import {
  botStatus,
  closedTodayCount,
  dailyPnlFromClosed,
  fmtUsd,
  scannerStatus,
  sumInvested,
} from '../../utils';

function ScannerPickCard({ opp, rank }) {
  const vol = opp.volatility != null ? `${Number(opp.volatility).toFixed(1)}%` : '—';
  const isMexc = String(opp.exchange || '').toLowerCase().includes('mexc');
  return (
    <div className={`scanner-pick ${isMexc ? 'mexc' : 'binance'}`}>
      <div className="sp-rank">#{rank}</div>
      <div className="sp-symbol mono">{opp.symbol}</div>
      <div className="sp-score up mono">{opp.score ?? '—'}</div>
      <div className="sp-meta">
        <span>{vol}</span>
        <span className="sp-ex">{opp.exchange || '—'}</span>
      </div>
    </div>
  );
}

export function OverviewTab({ data }) {
  const open = data?.open?.data || [];
  const closed = data?.closed?.data || [];
  const perf = data?.performance?.data?.summary;
  const scanner = data?.scanner?.data;
  const opps = (scanner?.opportunities || []).slice(0, 15);
  const deployed = sumInvested(open);
  const dailyPnl = dailyPnlFromClosed(closed);
  const todayCount = closedTodayCount(closed);
  const bot = botStatus(data?.health);
  const scan = scannerStatus(data?.scanner, data?.health);

  return (
    <div className="tab-panel overview-panel">
      <MetricGrid className="overview-metrics">
        <Card
          title="Deployed USDT"
          value={fmtUsd(deployed)}
          sub={`${open.length} open positions`}
          accent="binance"
          glow
        />
        <Card
          title="Open risk / exposure"
          value={fmtUsd(deployed)}
          sub={`Binance ${data?.open?.by_exchange?.binance?.length || 0} · MEXC ${data?.open?.by_exchange?.mexc?.length || 0}`}
          glow
        />
        <Card
          title="Daily PnL (UTC)"
          value={fmtUsd(dailyPnl, 4)}
          sub={`${todayCount} closed today`}
          accent={Number(dailyPnl) >= 0 ? 'green' : 'red'}
          glow
        />
        <Card
          title="Closed net PnL"
          value={fmtUsd(perf?.total_net, 4)}
          sub={`${perf?.count || 0} closed · win ${(perf?.win_rate || 0).toFixed(1)}%`}
          accent={Number(perf?.total_net) >= 0 ? 'green' : 'red'}
          glow
        />
        <Card
          title="Scanner"
          value={scan.label}
          sub={scanner?.updated_at ? `Last scan ${scanner.updated_at.slice(0, 19)}` : 'Awaiting scan data'}
          accent="mexc"
          glow
        />
        <Card
          title="Bot heartbeat"
          value={bot.label}
          sub={data?.health?.heartbeats?.bot?.at ? `At ${data.health.heartbeats.bot.at.slice(0, 19)}` : 'No heartbeat file'}
          accent={bot.ok ? 'green' : 'red'}
          glow
        />
      </MetricGrid>

      <SectionTitle accent="mexc">Top scanner picks</SectionTitle>
      {!opps.length ? (
        <p className="muted empty-hint">No scanner opportunities in API response.</p>
      ) : (
        <div className="scanner-grid">
          {opps.map((o, i) => (
            <ScannerPickCard key={o.symbol || i} opp={o} rank={i + 1} />
          ))}
        </div>
      )}

      <SectionTitle>Active positions</SectionTitle>
      {!open.length ? (
        <p className="muted empty-hint">No open trades in data/trades/*.json</p>
      ) : (
        <div className="trade-card-grid">
          {open.slice(0, 12).map((t) => (
            <TradeCard key={t.id || `${t.coin}-${t.open_time}`} trade={t} />
          ))}
        </div>
      )}

      <SectionTitle>Recent closed</SectionTitle>
      <TradeTable trades={closed.slice(0, 10)} empty="No closed trades yet." compact />
    </div>
  );
}
