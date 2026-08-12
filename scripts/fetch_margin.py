#!/usr/bin/env python3
"""
台股融資融券指標抓取（扣除 ETF）
資料來源：TWSE OpenAPI /exchangeReport/MI_MARGN
"""
import json, urllib.request, time, sys
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
TWSE_BASE = "https://openapi.twse.com.tw/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-dashboard/1.0)", "Accept": "application/json"}

def is_etf(code):
    code = str(code).strip()
    return code.startswith("0") and len(code) == 4

def api_get(path, retries=4, wait=10):
    url = f"{TWSE_BASE}{path}"
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"   retry {i+1}/{retries}: {e}", flush=True)
            if i < retries - 1:
                time.sleep(wait)
            else:
                raise

def parse_num(s):
    try:
        return float(str(s).replace(",", "").replace("，", "").strip())
    except Exception:
        return 0.0

def fetch_margin():
    print("📡 TWSE OpenAPI 融資融券數據...", flush=True)
    data = api_get("/exchangeReport/MI_MARGN")
    if not data:
        raise Exception("API 回傳空資料")
    print(f"   總筆數：{len(data)}", flush=True)

    total_margin = no_etf_margin = 0.0
    total_short  = no_etf_short  = 0.0
    etf_count = stock_count = 0

    for row in data:
        code = str(row.get("股票代號", "")).strip()
        if not code:
            continue
        margin_today = parse_num(row.get("融資今日餘額", 0))
        short_today  = parse_num(row.get("融券今日餘額", 0))
        total_margin += margin_today
        total_short  += short_today
        if is_etf(code):
            etf_count += 1
        else:
            no_etf_margin += margin_today
            no_etf_short  += short_today
            stock_count   += 1

    # 千元 → 億元
    total_margin_b  = round(total_margin   / 100000, 2)
    no_etf_margin_b = round(no_etf_margin  / 100000, 2)

    print(f"   全市場融資：{total_margin_b} 億元（ETF {etf_count} 檔）", flush=True)
    print(f"   扣ETF融資：{no_etf_margin_b} 億元（{stock_count} 檔個股）", flush=True)
    print(f"   全市場融券：{total_short:,.0f} 張", flush=True)
    print(f"   扣ETF融券：{no_etf_short:,.0f} 張", flush=True)

    short_margin_ratio = round(no_etf_short / (no_etf_margin_b * 10000) * 100, 4) \
        if no_etf_margin_b > 0 else 0

    return {
        "total_margin_bn":    total_margin_b,
        "total_short_k":      round(total_short),
        "ex_etf_margin_bn":   no_etf_margin_b,
        "ex_etf_short_k":     round(no_etf_short),
        "short_margin_ratio": short_margin_ratio,
        "stock_count":        stock_count,
        "etf_count":          etf_count,
    }

def calc_score(ex_etf_margin_bn):
    """融資餘額越高 → 散戶槓桿越重 → 反指標風險越高 → 評分越高（0-100）"""
    baseline, scale = 2500.0, 500.0
    raw = 50 + (ex_etf_margin_bn - baseline) / scale * 10
    return max(0, min(100, int(round(raw))))

def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")
    try:
        d = fetch_margin()
        d["score"] = calc_score(d["ex_etf_margin_bn"])
        d["fetched_at"] = now_str
        print(f"📊 融資槓桿評分：{d['score']}", flush=True)

        try:
            with open("data.json", "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

        existing["margin"] = d
        with open("data.json", "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print("💾 margin 數據合併寫入完成", flush=True)
    except Exception as e:
        print(f"❌ 失敗：{e}", file=sys.stderr, flush=True)

if __name__ == "__main__":
    main()
