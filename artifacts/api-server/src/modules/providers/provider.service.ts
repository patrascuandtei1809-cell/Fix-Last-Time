import type { Candle, FetchCandlesInput, MarketDataProvider } from "./types";
import { BinanceProvider } from "./binance.provider";
import { CoinbaseProvider } from "./coinbase.provider";

const MAX_RETRIES = 3;
const BASE_BACKOFF_MS = 200;
const BREAKER_THRESHOLD = 3; // consecutive failures before the breaker opens
const BREAKER_COOLDOWN_MS = 30_000;

type BreakerState = { failures: number; openUntil: number };

export type FetchResult = {
  candles: Candle[];
  source: string;
  attempts: Array<{ provider: string; ok: boolean; error?: string }>;
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Ordered failover across market-data providers with per-provider retry,
 * exponential backoff and a lightweight circuit breaker. The ordered list is
 * the "PRIMARY → FALLBACK" chain from the spec (Binance → Coinbase).
 */
export class MarketDataService {
  private readonly providers: MarketDataProvider[];
  private readonly breakers = new Map<string, BreakerState>();

  constructor(providers?: MarketDataProvider[]) {
    this.providers = providers ?? [new BinanceProvider(), new CoinbaseProvider()];
  }

  private breaker(name: string): BreakerState {
    let b = this.breakers.get(name);
    if (!b) {
      b = { failures: 0, openUntil: 0 };
      this.breakers.set(name, b);
    }
    return b;
  }

  async getCandles(input: FetchCandlesInput): Promise<FetchResult> {
    const attempts: FetchResult["attempts"] = [];

    for (const provider of this.providers) {
      const breaker = this.breaker(provider.name);
      if (Date.now() < breaker.openUntil) {
        attempts.push({ provider: provider.name, ok: false, error: "circuit_open" });
        continue;
      }

      let lastErr: unknown;
      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        try {
          const candles = await provider.fetchCandles(input);
          if (candles.length === 0) {
            throw new Error("empty candles");
          }
          breaker.failures = 0;
          breaker.openUntil = 0;
          attempts.push({ provider: provider.name, ok: true });
          return { candles, source: provider.name, attempts };
        } catch (err) {
          lastErr = err;
          if (attempt < MAX_RETRIES - 1) {
            await sleep(BASE_BACKOFF_MS * 2 ** attempt);
          }
        }
      }

      breaker.failures += 1;
      if (breaker.failures >= BREAKER_THRESHOLD) {
        breaker.openUntil = Date.now() + BREAKER_COOLDOWN_MS;
      }
      attempts.push({
        provider: provider.name,
        ok: false,
        error: lastErr instanceof Error ? lastErr.message : String(lastErr),
      });
    }

    return { candles: [], source: "none", attempts };
  }
}
