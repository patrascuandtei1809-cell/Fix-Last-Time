export type Timeframe = "15m" | "1h" | "4h" | "1d";

export type Candle = {
  openTime: number; // epoch ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type AssetDescriptor = {
  symbol: string; // canonical, e.g. BTC
  binanceSymbol: string; // e.g. BTCUSDT
  coinbaseProduct: string; // e.g. BTC-USD
};

export type FetchCandlesInput = {
  asset: AssetDescriptor;
  timeframe: Timeframe;
  limit: number;
};

/**
 * Free market-data providers implement this. New providers (or paid feeds)
 * only need to satisfy this interface to participate in failover.
 */
export interface MarketDataProvider {
  readonly name: string;
  fetchCandles(input: FetchCandlesInput): Promise<Candle[]>;
}

export const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  "15m": 15 * 60,
  "1h": 60 * 60,
  "4h": 4 * 60 * 60,
  "1d": 24 * 60 * 60,
};

/* -------------------------------------------------------------------------- */
/* Future paid-feed interfaces (Phase 3). Defined now so the pipeline and the  */
/* normalization layer can depend on stable contracts. No implementations yet. */
/* -------------------------------------------------------------------------- */

export interface OnChainProvider {
  readonly name: string;
  fetchMetrics(asset: AssetDescriptor): Promise<Record<string, number>>;
}

export interface NewsProvider {
  readonly name: string;
  fetchHeadlines(asset: AssetDescriptor): Promise<Array<{ title: string; source: string; publishedAt: number }>>;
}

export interface MacroProvider {
  readonly name: string;
  fetchIndicators(): Promise<Record<string, number>>;
}
