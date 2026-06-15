import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchAllDashboardData } from '../api';

const POLL_MS = 3000;

export function useDashboardData() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [lastFetch, setLastFetch] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const hasLoadedRef = useRef(false);
  const intervalRef = useRef(null);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (hasLoadedRef.current) setIsRefreshing(true);

    try {
      const next = await fetchAllDashboardData();
      if (!mounted.current) return;
      setData(next);
      setError(null);
      setLastFetch(new Date());
      hasLoadedRef.current = true;
      setHasLoaded(true);
    } catch (e) {
      if (!mounted.current) return;
      setError(e.message || String(e));
    } finally {
      inFlight.current = false;
      if (mounted.current) setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;

    if (intervalRef.current != null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    refresh();
    intervalRef.current = setInterval(refresh, POLL_MS);

    return () => {
      mounted.current = false;
      if (intervalRef.current != null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [refresh]);

  return { data, error, lastFetch, refresh, isRefreshing, hasLoaded };
}
