import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchAllDashboardData } from '../api';

const POLL_MS = 3000;

export function useDashboardData() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [lastFetch, setLastFetch] = useState(null);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchAllDashboardData();
      if (!mounted.current) return;
      setData(next);
      setError(null);
      setLastFetch(new Date());
    } catch (e) {
      if (!mounted.current) return;
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [refresh]);

  return { data, error, lastFetch, refresh };
}
