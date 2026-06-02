import type { Candle, FetchCandlesInput, MarketDataProvider } from "./types";
import { TIMEFRAME_SECONDS } from "./types";

// Free public Coinbase Exchange candles endpoint (no key). Used as failover
// when the primary provider is unavailable.
const BASE_URL = "https://api.exchange.coinbase.com/products";

export class CoinbaseProvider implements MarketDataProvider {
  readonly name = "coinbase";

  async fetchCandles({ asset, timeframe, limit }: FetchCandlesInput): Promise<Candle[]> {
    const granularity = TIMEFRAME_SECONDS[timeframe];
    const url = `${BASE_URL}/${encodeURIComponent(
      asset.coinbaseProduct,
    )}/candles?granularity=${granularity}`;

    const res = await fetch(url, {
      headers: { "User-Agent": "research-engine/1.0" },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) {
      throw new Error(`coinbase http ${res.status}`);
    }
    const raw = (await res.json()) as unknown;
    if (!Array.isArray(raw)) {
      throw new Error("coinbase: unexpected payload");
    }

    // Coinbase row: [ time(seconds), low, high, open, close, volume ], newest first.
    return raw
      .map((row): Candle => {
        const r = row as number[];
        return {
          openTime: Number(r[0]) * 1000,
          low: Number(r[1]),
          high: Number(r[2]),
          open: Number(r[3]),
          close: Number(r[4]),
          volume: Number(r[5]),
        };
      })
      .filter((c) => Number.isFinite(c.close) && Number.isFinite(c.openTime))
      .sort((a, b) => a.openTime - b.openTime)
      .slice(-limit);
  }
}
