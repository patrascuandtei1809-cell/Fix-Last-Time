import json, time, hmac, hashlib, requests
from pathlib import Path

BASE = "https://api.mexc.com"
CREDS_FILE = Path("data/.mexc_creds.json")
OUT_FILE = Path("data/mexc_balance.json")

def sign(secret, params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, sig

def get_account():
    creds = json.loads(CREDS_FILE.read_text())
    params = {"timestamp": int(time.time() * 1000)}
    query, sig = sign(creds["api_secret"], params)
    url = f"{BASE}/api/v3/account?{query}&signature={sig}"
    r = requests.get(url, headers={"X-MEXC-APIKEY": creds["api_key"]}, timeout=20)
    r.raise_for_status()
    return r.json()

def main():
    acc = get_account()
    balances = []
    total_usdt_known = 0.0

    for b in acc.get("balances", []):
        asset = b.get("asset")
        free = float(b.get("free", 0) or 0)
        locked = float(b.get("locked", 0) or 0)
        total = free + locked
        if total <= 0:
            continue

        item = {
            "asset": asset,
            "free": free,
            "locked": locked,
            "total": total
        }
        balances.append(item)

        if asset == "USDT":
            total_usdt_known += total

    out = {
        "updated_at": int(time.time()),
        "account_type": acc.get("accountType"),
        "can_trade": acc.get("canTrade"),
        "balances": balances,
        "usdt_available": next((x["free"] for x in balances if x["asset"] == "USDT"), 0.0),
        "usdt_total_known": total_usdt_known
    }

    OUT_FILE.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
