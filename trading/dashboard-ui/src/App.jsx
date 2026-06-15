import React, { useState } from 'react';
import { TopBar } from './components/TopBar';
import { OverviewSkeleton } from './components/Skeleton';
import { useDashboardData } from './hooks/useDashboardData';
import { OverviewTab } from './components/tabs/OverviewTab';
import { BinanceTab } from './components/tabs/BinanceTab';
import { MexcTab } from './components/tabs/MexcTab';
import { ScannerTab } from './components/tabs/ScannerTab';
import { HistoryTab } from './components/tabs/HistoryTab';
import { PerformanceTab } from './components/tabs/PerformanceTab';
import { DiagnosticsTab } from './components/tabs/DiagnosticsTab';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'binance', label: 'Binance' },
  { id: 'mexc', label: 'MEXC' },
  { id: 'scanner', label: 'Scanner' },
  { id: 'history', label: 'History' },
  { id: 'performance', label: 'Performance' },
  { id: 'diagnostics', label: 'Diagnostics' },
];

export default function App() {
  const [tab, setTab] = useState('overview');
  const { data, error, lastFetch, isRefreshing, hasLoaded } = useDashboardData();

  const renderTab = () => {
    if (!hasLoaded && !error) {
      return tab === 'overview' ? <OverviewSkeleton /> : <OverviewSkeleton />;
    }
    switch (tab) {
      case 'overview':
        return <OverviewTab data={data} />;
      case 'binance':
        return <BinanceTab data={data} />;
      case 'mexc':
        return <MexcTab data={data} />;
      case 'scanner':
        return <ScannerTab data={data} />;
      case 'history':
        return <HistoryTab data={data} />;
      case 'performance':
        return <PerformanceTab data={data} />;
      case 'diagnostics':
        return <DiagnosticsTab data={data} lastFetch={lastFetch} error={error} />;
      default:
        return null;
    }
  };

  return (
    <div className="app">
      <TopBar data={data} lastFetch={lastFetch} error={error} isRefreshing={isRefreshing} />
      <nav className="tab-nav">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`tab-btn ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main className="main-content">{renderTab()}</main>
      <footer className="footer">
        FastAPI :8000 · Streamlit :8501 (unchanged) · React polls every 3s — stale-while-revalidate
      </footer>
    </div>
  );
}
