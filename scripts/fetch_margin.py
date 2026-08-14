#!/usr/bin/env python3
"""
台股融資融券指標抓取（扣除 ETF）
資料來源：TWSE OpenAPI /exchangeReport/MI_MARGN
"""
import json
import urllib.request
import time
import sys
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
TWSE_BASE = "https://openapi.twse.com.tw/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; stock-dashboard/1.0)",
    "Accept": "application/json"
}

def is_etf(code: str) -> bool:
    """
    判斷是否為 ETF / ETN：
    涵蓋 00 開頭的 4~6 碼標的 (如 0050, 00878, 00632R, 00940 等)
    """
    c = str(code).strip()
    return c.startswith("00") or (c.startswith("0") and len(c) >= 4)

def api_get(path: str, retries: int = 4, wait: int = 10):
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

def parse_num(s) -> float:
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

    # 累計張數
    total_margin_sheets = no_etf_margin_sheets = 0.0
    total_short_sheets  = no_etf_short_sheets  = 0.0
    etf_count = stock_count = 0

    for row in data:
        code = str(row.get("股票代號", "")).strip()
        if not code:
            continue
        
        # 取得融資與融券餘額（單位：張）
        margin_today = parse_num(row.get("融資今日餘額", 0))
        short_today  = parse_num(row.get("融券今日餘額", 0))

        total_margin_sheets += margin_today
        total_short_sheets  += short_today

        if is_etf(code):
            etf_count += 1
        else:
            no_etf_margin_sheets += margin_today
            no_etf_short_sheets  += short_today
            stock_count += 1

    # 萬張計算
    total_margin_wan = round(total_margin_sheets / 10000, 2)
    no_etf_margin_wan = round(no_etf_margin_sheets / 10000, 2)

    print(f"   全市場融資：{total_margin_sheets:,.0f} 張 ({total_margin_wan} 萬張，含 ETF {etf_count} 檔)", flush=True)
    print(f"   扣ETF融資 ：{no_etf_margin_sheets:,.0f} 張 ({no_etf_margin_wan} 萬張，{stock_count} 檔個股)", flush=True)
    print(f"   全市場融券：{total_short_sheets:,.0f} 張", flush=True)
    print(f"   扣ETF融券 ：{no_etf_short_sheets:,.0f} 張", flush=True)

    # 正確券資比公式：(融券張數 / 融資張數) * 100%
    short_margin_ratio = round((no_etf_short_sheets / no_etf_margin_sheets) * 100, 2) if no_etf_margin_sheets > 0 else 0.0
    print(f"   扣ETF券資比：{short_margin_ratio}%", flush=True)

    return {
        "total_margin_sheets":   round(total_margin_sheets),
        "total_short_sheets":    round(total_short_sheets),
        "ex_etf_margin_sheets":  round(no_etf_margin_sheets),
        "ex_etf_short_sheets":   round(no_etf_short_sheets),
        "ex_etf_margin_wan":     no_etf_margin_wan,
        "short_margin_ratio":    short_margin_ratio,
        "stock_count":           stock_count,
        "etf_count":             etf_count,
    }

def calc_score(ex_etf_margin_wan: float) -> int:
    """
    評分模型（依據扣除 ETF 後之個股融資總萬張數）：
    以 600 萬張為中位基準 (50分)，每增減 100 萬張調整 10 分
    """
    baseline, scale = 600.0, 100.0
    raw = 50 + (ex_etf_margin_wan - baseline) / scale * 10
    return max(0, min(100, int(round(raw))))

def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")
    try:
        d = fetch_margin()
        d["score"] = calc_score(d["ex_etf_margin_wan"])
        d["fetched_at"] = now_str
        print(f"📊 融資指標評分：{d['score']}", flush=True)

        existing = {}
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
