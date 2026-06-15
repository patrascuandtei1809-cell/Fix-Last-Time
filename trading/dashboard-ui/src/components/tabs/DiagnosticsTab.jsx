import React from 'react';
import { Card, MetricGrid, SectionTitle, Warnings } from '../Ui';
import { formatTime } from '../../utils';

export function DiagnosticsTab({ data, lastFetch, error }) {
  const health = data?.health;
  const activity = data?.activity;

  return (
    <div className="tab-panel">
      <SectionTitle>Diagnostics</SectionTitle>
      {error && <div className="error-banner">API error: {error}</div>}
      <MetricGrid>
        <Card title="API mode" value={health?.mode || '—'} />
        <Card title="Last UI poll" value={lastFetch ? formatTime(lastFetch) : '—'} sub="Updates every 3s — no full page reload" />
        <Card title="Trade files" value={health?.files?.trade_file_count ?? 0} />
        <Card title="Settings file" value={health?.files?.settings ? 'YES' : 'NO'} />
      </MetricGrid>

      <SectionTitle>API health warnings</SectionTitle>
      <Warnings items={health?.warnings} />

      <SectionTitle>Heartbeats</SectionTitle>
      {!health?.heartbeats || !Object.keys(health.heartbeats).length ? (
        <p className="muted">No heartbeat files yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Process</th>
                <th>At</th>
                <th>Stale</th>
                <th>Extra</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(health.heartbeats).map(([name, hb]) => (
                <tr key={name}>
                  <td className="bold">{name}</td>
                  <td className="mono">{hb.at?.slice(0, 19) || '—'}</td>
                  <td>{hb.stale ? 'yes' : 'no'}</td>
                  <td className="reason mono">{JSON.stringify({ ...hb, at: undefined, name: undefined, ts: undefined }).slice(0, 80)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <SectionTitle>Activity log (tail)</SectionTitle>
      <Warnings items={activity?.warnings} />
      <div className="log-wrap">
        {(activity?.data || []).slice().reverse().slice(0, 80).map((e, i) => (
          <div key={i} className="log-line">
            <span className="log-ts">{(e.time || '').slice(11, 19)}</span>
            <span className={`log-lvl log-${e.level || 'INFO'}`}>[{e.level || 'INFO'}]</span>
            <span className="log-msg">{e.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
