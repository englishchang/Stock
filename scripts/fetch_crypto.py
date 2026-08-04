#!/usr/bin/env python3
"""
加密貨幣散戶情緒指標抓取
資料來源：
  - Alternative.me Fear & Greed Index（免費，無需 API Key）
  - CoinGecko API（免費，無需 API Key，GitHub Actions 可用）
BTC + DOGE，每 4 小時更新
"""
import json, urllib.request, time, sys
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; stock-dashboard/1.0)",
    "Accept": "application/json",
}


def api_get(url, retries=4, wait=8):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"   retry {i+1}/{retries}: {e}", flush=True)
            if i < retries - 1:
                time.sleep(wait)
            else:
                raise


def fetch_fear_greed():
    """Alternative.me 恐懼貪婪指數（0=極恐, 100=極貪）"""
    d = api_get("https://api.alternative.me/fng/?limit=1&format=json")
    v = d["data"][0]
    return {"value": int(v["value"]), "label": v["value_classification"]}


def fetch_btc_dominance():
    """BTC 全球市佔率（低=山寨季=散戶狂熱=危險）"""
    d = api_get("https://api.coingecko.com/api/v3/global")
    return round(d["data"]["market_cap_percentage"].get("btc", 52.0), 2)


def fetch_price_simple():
    """BTC + DOGE 即時價格與 24h 漲跌（批次）"""
    url = ("https://api.coingecko.com/api/v3/simple/price"
           "?ids=bitcoin,dogecoin&vs_currencies=usd&include_24hr_change=true")
    return api_get(url)


def fetch_coin_detail(coin_id):
    """個別幣的 7 日漲跌"""
    url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}"
           f"?localization=false&tickers=false&market_data=true"
           f"&community_data=false&developer_data=false&sparkline=false")
    d = api_get(url)
    md = d.get("market_data", {})
    return {"change_7d": round(md.get("price_change_percentage_7d") or 0, 2)}


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, int(round(v))))


def calc_score(fg_val, change_7d, btc_dom):
    """
    散戶情緒評分 0-100（越高 = 散戶越樂觀 = 反指標風險越高）
    - 恐懼貪婪（50%）：貪婪 → 高分 → 危險
    - 7日動能（20%）：大漲後散戶追多 → 高分
    - BTC 市佔（30%）：低市佔 = 山寨季 = 散戶狂熱 → 高分
    """
    fg_score = fg_val  # 直接對應

    if   change_7d >= 30: mom = 90
    elif change_7d >= 20: mom = 78
    elif change_7d >= 10: mom = 65
    elif change_7d >=  3: mom = 55
    elif change_7d >= -3: mom = 50
    elif change_7d >=-10: mom = 40
    elif change_7d >=-20: mom = 32
    else:                 mom = 22

    if   btc_dom >= 60: dom = 38
    elif btc_dom >= 55: dom = 45
    elif btc_dom >= 50: dom = 52
    elif btc_dom >= 45: dom = 62
    else:               dom = 72

    overall = clamp(fg_score * 0.50 + mom * 0.20 + dom * 0.30)
    return {"fear_greed": fg_score, "momentum": mom, "btc_dom": dom, "overall": overall}


def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")
    print(f"📡 抓取加密貨幣數據... {now_str}", flush=True)

    # Fear & Greed
    print("  [Fear & Greed Index]", flush=True)
    try:
        fg = fetch_fear_greed()
        print(f"    {fg['value']} / {fg['label']}", flush=True)
    except Exception as e:
        print(f"    ❌ {e}", flush=True)
        fg = {"value": 50, "label": "Neutral"}
    time.sleep(2)

    # BTC 市佔率
    print("  [BTC 全球市佔率]", flush=True)
    try:
        btc_dom = fetch_btc_dominance()
        print(f"    {btc_dom}%", flush=True)
    except Exception as e:
        print(f"    ❌ {e}", flush=True)
        btc_dom = 52.0
    time.sleep(2)

    # 價格批次
    print("  [BTC + DOGE 價格]", flush=True)
    try:
        prices = fetch_price_simple()
        btc_price  = prices.get("bitcoin",  {}).get("usd", 0)
        doge_price = prices.get("dogecoin", {}).get("usd", 0)
        btc_24h    = prices.get("bitcoin",  {}).get("usd_24h_change", 0) or 0
        doge_24h   = prices.get("dogecoin", {}).get("usd_24h_change", 0) or 0
        print(f"    BTC  ${btc_price:,.0f}  24h:{btc_24h:+.1f}%", flush=True)
        print(f"    DOGE ${doge_price:.5f}  24h:{doge_24h:+.1f}%", flush=True)
    except Exception as e:
        print(f"    ❌ {e}", flush=True)
        btc_price = doge_price = btc_24h = doge_24h = 0
    time.sleep(3)

    # 7 日漲跌
    print("  [BTC 7日數據]", flush=True)
    try:
        btc_7d = fetch_coin_detail("bitcoin")["change_7d"]
        print(f"    {btc_7d:+.1f}%", flush=True)
    except Exception as e:
        print(f"    ❌ {e}", flush=True)
        btc_7d = 0.0
    time.sleep(3)

    print("  [DOGE 7日數據]", flush=True)
    try:
        doge_7d = fetch_coin_detail("dogecoin")["change_7d"]
        print(f"    {doge_7d:+.1f}%", flush=True)
    except Exception as e:
        print(f"    ❌ {e}", flush=True)
        doge_7d = 0.0

    # 評分
    btc_scores  = calc_score(fg["value"], btc_7d,  btc_dom)
    doge_scores = calc_score(fg["value"], doge_7d, btc_dom)
    print(f"  📊 BTC  評分：{btc_scores}", flush=True)
    print(f"  📊 DOGE 評分：{doge_scores}", flush=True)

    # 合併寫入
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    existing["crypto"] = {
        "fetched_at":    now_str,
        "fear_greed":    fg,
        "btc_dominance": btc_dom,
        "data": {
            "BTCUSDT": {
                "symbol":        "BTCUSDT",
                "price":         btc_price,
                "change_24h":    round(btc_24h,  2),
                "change_7d":     round(btc_7d,   2),
                "fear_greed":    fg,
                "btc_dominance": btc_dom,
                "scores":        btc_scores,
            },
            "DOGEUSDT": {
                "symbol":        "DOGEUSDT",
                "price":         doge_price,
                "change_24h":    round(doge_24h, 2),
                "change_7d":     round(doge_7d,  2),
                "fear_greed":    fg,
                "btc_dominance": btc_dom,
                "scores":        doge_scores,
            },
        },
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print("💾 crypto 數據合併寫入完成！", flush=True)


if __name__ == "__main__":
    main()
