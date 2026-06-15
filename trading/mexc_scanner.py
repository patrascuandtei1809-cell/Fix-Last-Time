import requests
import json
import os
import re
from datetime import datetime, timezone

MEXC_URL = "https://api.mexc.com/api/v3/ticker/24hr"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/24hr"
OUTPUT_FILE = "data/multi_exchange_opportunities.json"

BLACKLIST = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "USDCUSDT", "BUSDUSDT", "FDUSDUSDT", "TUSDUSDT", "DAIUSDT"
}

def valid_symbol(symbol):
    return bool(re.match(r"^[A-Z0-9]{2,20}USDT$", symbol))

def fetch(exchange, url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    rows = []

    for x in data:
        symbol = x.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        if symbol in BLACKLIST:
            continue
        if not valid_symbol(symbol):
            continue

        try:
            price = float(x.get("lastPrice", 0))
            high = float(x.get("highPrice", 0))
            low = float(x.get("lowPrice", 0))
            change = float(x.get("priceChangePercent", 0))
            volume = float(x.get("quoteVolume", 0))

            if price <= 0 or volume < 2_000_000:
                continue

            volatility = ((high - low) / price) * 100

            if volatility < 10 or volatility > 120:
                continue

            if change < -35 or change > 80:
                continue

            score = (volume / 1_000_000) + max(change, 0) * 2 + volatility * 3

            rows.append({
                "exchange": exchange,
                "symbol": symbol,
                "price": price,
                "change_24h_percent": round(change, 2),
                "volume_usdt": round(volume, 2),
                "volatility_percent": round(volatility, 2),
                "score": round(score, 2),
            })
        except Exception:
            continue

    return rows

def main():
    all_rows = []

    for exchange, url in [("MEXC", MEXC_URL), ("BINANCE", BINANCE_URL)]:
        try:
            all_rows.extend(fetch(exchange, url))
        except Exception as e:
            print(f"[WARN] {exchange} scanner failed: {e}")

    all_rows.sort(key=lambda x: x["score"], reverse=True)
    top = all_rows[:40]

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(top),
            "opportunities": top
        }, f, indent=2)

    print("\n=== TOP OPPORTUNITIES: MEXC + BINANCE ===\n")
    for i, coin in enumerate(top, 1):
        print(
            f"{i:02d}. {coin['exchange']} | {coin['symbol']} | "
            f"Price: {coin['price']} | "
            f"24h: {coin['change_24h_percent']}% | "
            f"Vol: ${coin['volume_usdt']:,.0f} | "
            f"Volatility: {coin['volatility_percent']}% | "
            f"Score: {coin['score']}"
        )

    print(f"\nSaved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
