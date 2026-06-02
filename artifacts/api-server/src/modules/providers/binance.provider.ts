import type { Candle, FetchCandlesInput, MarketDataProvider } from "./types";

// api.binance.com is geo-blocked (HTTP 451) in some regions, but the public
// market-data mirror data-api.binance.vision serves identical klines with no key.
const BASE_URL = "https://data-api.binance.vision/api/v3/klines";

export class BinanceProvider implements MarketDataProvider {
  readonly name = "binance";

  async fetchCandles({ asset, timeframe, limit }: FetchCandlesInput): Promise<Candle[]> {
    const url = `${BASE_URL}?symbol=${encodeURIComponent(
      asset.binanceSymbol,
    )}&interval=${timeframe}&limit=${Math.min(limit, 1000)}`;

    const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) {
      throw new Error(`binance http ${res.status}`);
    }
    const raw = (await res.json()) as unknown;
    if (!Array.isArray(raw)) {
      throw new Error("binance: unexpected payload");
    }

    return raw
      .map((row): Candle => {
        const r = row as unknown[];
        return {
          openTime: Number(r[0]),
          open: Number(r[1]),
          high: Number(r[2]),
          low: Number(r[3]),
          close: Number(r[4]),
          volume: Number(r[5]),
        };
      })
      .filter((c) => Number.isFinite(c.close) && Number.isFinite(c.openTime))
      .sort((a, b) => a.openTime - b.openTime);
  }
}
