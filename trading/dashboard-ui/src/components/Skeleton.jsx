import React from 'react';

export function SkeletonBlock({ className = '' }) {
  return <div className={`skeleton-block ${className}`.trim()} aria-hidden="true" />;
}

export function SkeletonCard() {
  return (
    <div className="card skeleton-card" aria-hidden="true">
      <SkeletonBlock className="skeleton-lbl" />
      <SkeletonBlock className="skeleton-val" />
      <SkeletonBlock className="skeleton-sub" />
    </div>
  );
}

export function SkeletonMetricGrid({ count = 6 }) {
  return (
    <div className="metric-grid">
      {Array.from({ length: count }, (_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 5, cols = 6 }) {
  return (
    <div className="table-wrap skeleton-table" aria-hidden="true">
      <div className="skeleton-table-head">
        {Array.from({ length: cols }, (_, i) => (
          <SkeletonBlock key={i} className="skeleton-th" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="skeleton-table-row">
          {Array.from({ length: cols }, (_, c) => (
            <SkeletonBlock key={c} className="skeleton-td" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function OverviewSkeleton() {
  return (
    <div className="tab-panel">
      <SkeletonMetricGrid count={6} />
      <SkeletonBlock className="skeleton-section-title" />
      <div className="scanner-grid skeleton-scanner-grid">
        {Array.from({ length: 15 }, (_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
      <SkeletonBlock className="skeleton-section-title" />
      <SkeletonTable rows={4} cols={5} />
      <SkeletonBlock className="skeleton-section-title" />
      <SkeletonTable rows={3} cols={5} />
    </div>
  );
}
