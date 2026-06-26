#!/usr/bin/env python3
"""
Binance Futures 加密貨幣散戶指標抓取
資料來源：Binance Public API（免費，無需 Key）
BTC / DOGE 每4小時更新一次
"""
import json, urllib.request, urllib.error, time, sys
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
BASE = "https://fapi.binance.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

def api_get(path, params="", retries=3):
    url = f"{BASE}{path}?{params}" if params else f"{BASE}{path}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt < retries - 1:
                print(f"    重試 {attempt+1}/{retries}: {e}")
                time.sleep(5)
            else:
                raise e

def get_price(symbol):
    d = api_get("/fapi/v1/ticker/price", f"symbol={symbol}")
    return float(d["price"])

def get_funding_rate(symbol):
    """資金費率 - 正=多頭支付空頭(偏多)，負=空頭支付多頭(偏空)"""
    d = api_get("/fapi/v1/premiumIndex", f"symbol={symbol}")
    return float(d["lastFundingRate"])

def get_ls_ratio(symbol, period="4h"):
    """全市場帳戶多空比（散戶情緒）"""
    data = api_get("/futures/data/globalLongShortAccountRatio",
                   f"symbol={symbol}&period={period}&limit=1")
    d = data[0]
    return {
        "long_pct":  round(float(d["longAccount"]) * 100, 2),
        "short_pct": round(float(d["shortAccount"]) * 100, 2),
        "ls_ratio":  round(float(d["longShortRatio"]), 4),
    }

def get_top_ls_ratio(symbol, period="4h"):
    """大戶帳戶多空比（前20%資金大戶）"""
    data = api_get("/futures/data/topLongShortAccountRatio",
                   f"symbol={symbol}&period={period}&limit=1")
    d = data[0]
    return {
        "long_pct":  round(float(d["longAccount"]) * 100, 2),
        "short_pct": round(float(d["shortAccount"]) * 100, 2),
        "ls_ratio":  round(float(d["longShortRatio"]), 4),
    }

def get_taker_ratio(symbol, period="4h"):
    """主動買賣比（Taker）"""
    data = api_get("/futures/data/takerlongshortRatio",
                   f"symbol={symbol}&period={period}&limit=1")
    d = data[0]
    ratio = float(d["buySellRatio"])
    buy  = float(d["buyVol"])
    sell = float(d["sellVol"])
    total = buy + sell
    return {
        "buy_sell_ratio": round(ratio, 4),
        "buy_pct":  round(buy  / total * 100, 2) if total > 0 else 50.0,
        "sell_pct": round(sell / total * 100, 2) if total > 0 else 50.0,
    }

def get_open_interest(symbol):
    """當前未平倉量"""
    d = api_get("/fapi/v1/openInterest", f"symbol={symbol}")
    return float(d["openInterest"])

def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))

def calc_score(ls, top_ls, taker, funding):
    """
    加密貨幣散戶情緒評分 0-100
    ─ 散戶多空比（反指標）：散戶多頭% 越高 → 評分越低（反轉風險）
    ─ 大戶多空比（順指標）：大戶多頭% 越高 → 評分越高
    ─ Taker 買/賣比（動能）：買方主動越多 → 評分越高
    ─ 資金費率：正費率偏多，負費率偏空
    權重：散戶35% + 大戶30% + Taker20% + 資金費率15%
    """
    # 散戶反指標：多頭% 60→50分，70→35分，50→65分
    retail_score = clamp(round(50 + (50 - ls["long_pct"]) * 1.5))

    # 大戶順指標：多頭% 55→58分，65→73分，45→43分
    top_score = clamp(round(50 + (top_ls["long_pct"] - 50) * 1.5))

    # Taker 動能：買方% 55→60分，65→80分
    taker_score = clamp(round(50 + (taker["buy_pct"] - 50) * 2.0))

    # 資金費率：正偏多，0.01%≈+10分
    fund_score = clamp(round(50 + funding * 5000))

    overall = clamp(round(
        retail_score * 0.35 +
        top_score    * 0.30 +
        taker_score  * 0.20 +
        fund_score   * 0.15
    ))
    return {
        "retail":      retail_score,
        "top_trader":  top_score,
        "taker":       taker_score,
        "funding":     fund_score,
        "overall":     overall,
    }

def fetch_one(symbol):
    print(f"  [{symbol}]", flush=True)
    price   = get_price(symbol)
    print(f"    價格：{price:,.4f}", flush=True)
    ls      = get_ls_ratio(symbol)
    print(f"    散戶多空：{ls}", flush=True)
    top_ls  = get_top_ls_ratio(symbol)
    print(f"    大戶多空：{top_ls}", flush=True)
    taker   = get_taker_ratio(symbol)
    print(f"    Taker：{taker}", flush=True)
    oi      = get_open_interest(symbol)
    funding = get_funding_rate(symbol)
    print(f"    未平倉：{oi:,.0f}  資金費率：{funding:.6f}", flush=True)
    scores  = calc_score(ls, top_ls, taker, funding)
    print(f"    評分：{scores}", flush=True)
    return {
        "symbol":         symbol,
        "price":          price,
        "ls_ratio":       ls,
        "top_ls_ratio":   top_ls,
        "taker_ratio":    taker,
        "open_interest":  oi,
        "funding_rate":   round(funding * 100, 6),  # 轉為 %
        "scores":         scores,
    }

def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")
    print(f"📡 抓取 Binance 加密貨幣數據... {now_str}", flush=True)

    crypto_data = {}
    for sym in ["BTCUSDT", "DOGEUSDT"]:
        try:
            crypto_data[sym] = fetch_one(sym)
            time.sleep(2)
        except Exception as e:
            print(f"  ❌ {sym} 失敗：{e}", flush=True)
            crypto_data[sym] = None

    # 載入現有 data.json 並合併
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = {}

    existing["crypto"] = {
        "fetched_at": now_str,
        "period":     "4h",
        "data":       crypto_data,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"💾 crypto 數據已合併寫入 data.json", flush=True)

if __name__ == "__main__":
    main()
