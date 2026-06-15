import React from 'react';
import { botStatus, fmtUsd, scannerStatus, sumInvested } from '../utils';

export function TopBar({ data, lastFetch, error }) {
  const open = data?.open?.data || [];
  const deployed = sumInvested(open);
  const bot = botStatus(data?.health);
  const scan = scannerStatus(data?.scanner, data?.health);
  const netPnl = data?.performance?.data?.summary?.total_net;

  return (
    <header className="top-bar">
      <div className="brand">
        <span className="brand-icon">⚡</span>
        <span className="brand-name">
          Alpha<span className="brand-accent">Trade</span>
        </span>
        <span className="brand-tag">Terminal · React</span>
      </div>

      <div className="top-metrics">
        <div className="top-metric">
          <span className="tm-lbl">Deployed USDT</span>
          <span className="tm-val gold">{fmtUsd(deployed)}</span>
        </div>
        <div className="top-metric">
          <span className="tm-lbl">Closed net PnL</span>
          <span className={`tm-val ${Number(netPnl) >= 0 ? 'up' : 'dn'}`}>
            {fmtUsd(netPnl, 4)}
          </span>
        </div>
        <div className="top-metric">
          <span className="tm-lbl">Open trades</span>
          <span className="tm-val">{open.length}</span>
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
        {error ? (
          <span className="pill pill-red">API ERR</span>
        ) : (
          <span className="pill pill-green">LIVE 3s</span>
        )}
        <span className="pill pill-gold">READ-ONLY</span>
        {lastFetch && (
          <span className="top-ts">{lastFetch.toLocaleTimeString()}</span>
        )}
      </div>
    </header>
  );
}
