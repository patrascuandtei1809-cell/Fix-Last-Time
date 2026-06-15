import React from 'react';
import {
  botStatus,
  closedTodayCount,
  dailyPnlFromClosed,
  fmtUsd,
  scannerStatus,
  sumInvested,
} from '../utils';

export function TopBar({ data, lastFetch, error, isRefreshing }) {
  const open = data?.open?.data || [];
  const closed = data?.closed?.data || [];
  const deployed = sumInvested(open);
  const bot = botStatus(data?.health);
  const scan = scannerStatus(data?.scanner, data?.health);
  const netPnl = data?.performance?.data?.summary?.total_net;
  const dailyPnl = dailyPnlFromClosed(closed);
  const todayCount = closedTodayCount(closed);

  return (
    <header className="top-bar">
      <div className="top-bar-glow" aria-hidden="true" />

      <div className="top-bar-inner">
        <div className="brand-block">
          <div className="brand">
            <span className="brand-icon">⚡</span>
            <div className="brand-text">
              <span className="brand-name">
                Alpha<span className="brand-accent">Trade</span>
              </span>
              <span className="brand-live">
                <span className="live-dot" />
                LIVE
              </span>
            </div>
          </div>
          <span className="brand-tag">Crypto Trading Terminal</span>
        </div>

        <div className="top-metrics">
          <div className="top-metric hero-metric">
            <span className="tm-lbl">Deployed USDT</span>
            <span className="tm-val gold xl">{fmtUsd(deployed)}</span>
          </div>
          <div className="top-metric hero-metric">
            <span className="tm-lbl">Open risk</span>
            <span className="tm-val xl">
              {fmtUsd(deployed)}
              <span className="tm-sub-inline">{open.length} pos</span>
            </span>
          </div>
          <div className="top-metric hero-metric">
            <span className="tm-lbl">Daily PnL (UTC)</span>
            <span className={`tm-val xl ${Number(dailyPnl) >= 0 ? 'up' : 'dn'}`}>
              {fmtUsd(dailyPnl, 4)}
            </span>
          </div>
          <div className="top-metric">
            <span className="tm-lbl">Closed net PnL</span>
            <span className={`tm-val ${Number(netPnl) >= 0 ? 'up' : 'dn'}`}>
              {fmtUsd(netPnl, 4)}
            </span>
          </div>
          <div className="top-metric">
            <span className="tm-lbl">Scanner</span>
            <span className={`tm-val ${scan.ok ? 'purple' : 'muted'}`}>{scan.label}</span>
          </div>
          <div className="top-metric">
            <span className="tm-lbl">Bot heartbeat</span>
            <span className={`tm-val ${bot.ok ? 'up' : 'dn'}`}>{bot.label}</span>
          </div>
        </div>

        <div className="top-status">
          {isRefreshing && (
            <span className="sync-indicator" title="Syncing data">
              <span className="sync-dot" />
            </span>
          )}
          {error ? (
            <span className="pill pill-red" title={error}>API ERR</span>
          ) : (
            <span className="pill pill-green">LIVE 3s</span>
          )}
          <span className="pill pill-gold">READ-ONLY</span>
          {lastFetch && (
            <span className="top-ts">{lastFetch.toLocaleTimeString()}</span>
          )}
          {todayCount > 0 && (
            <span className="top-ts muted">{todayCount} closed today</span>
          )}
        </div>
      </div>
    </header>
  );
}
