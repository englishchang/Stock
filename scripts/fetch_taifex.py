#!/usr/bin/env python3
"""
台灣期貨交易所散戶籌碼數據抓取
使用官方 OpenAPI：https://openapi.taifex.com.tw/v1
直接回傳 JSON，不需解析 HTML，GitHub Actions 環境完全暢通
"""
import json, urllib.request, sys, time
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
BASE = "https://openapi.taifex.com.tw/v1"

CONTRACT_MAP = {"TX": "tx", "MTX": "mtx", "TMF": "tmf"}

IDENTITY_MAP = {
    "自營商": "dealer", "投信": "trust", "外資": "foreign",
    "Dealer": "dealer", "Trust": "trust",
    "Foreign Institutional Investors": "foreign",
    "Foreign Institutional Investors and Foreign Individuals": "foreign",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; taifex-dashboard/1.0)",
           "Accept": "application/json"}


def api_get(path, retries=4, wait=10):
    url = f"{BASE}{path}"
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


def parse_int(s):
    try:
        return int(str(s).replace(",", "").replace("，", "").strip())
    except Exception:
        return 0


def fmt_date(d):
    """YYYYMMDD → YYYY/MM/DD"""
    if d and len(str(d)) == 8:
        d = str(d)
        return f"{d[:4]}/{d[4:6]}/{d[6:]}"
    return str(d)


def fetch_futures():
    print("📡 TAIFEX OpenAPI 期貨數據...", flush=True)
    data = api_get("/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    if not data:
        raise Exception("API 回傳空資料")

    dates = sorted(set(str(r.get("Date","")) for r in data if r.get("Date")), reverse=True)
    print("前3筆原始:", json.dumps(data[:3], ensure_ascii=False), flush=True)
    latest = dates[0]
    print(f"   最新日期：{fmt_date(latest)}", flush=True)

    result = {v: {"dealer": 0, "trust": 0, "foreign": 0, "total": 0}
              for v in CONTRACT_MAP.values()}

    for row in data:
        if str(row.get("Date","")) != latest:
            continue
        code = str(row.get("ContractCode","")).strip()
        if code not in CONTRACT_MAP:
            continue
        key = CONTRACT_MAP[code]
        item = str(row.get("Item","")).strip()
        identity = next((v for k,v in IDENTITY_MAP.items() if k in item), None)
        if not identity:
            continue
        net_oi = parse_int(row.get("OpenInterest(Net)", 0))
        result[key][identity] = net_oi
        print(f"   ✓ {code} {identity}: {net_oi:,}", flush=True)

    for k in result:
        r = result[k]
        r["total"] = r["dealer"] + r["trust"] + r["foreign"]

    return fmt_date(latest), result


def fetch_options():
    print("📡 TAIFEX OpenAPI 選擇權數據...", flush=True)
    opt = {"foreign_call":0,"foreign_put":0,"dealer_call":0,
           "dealer_put":0,"trust_call":0,"trust_put":0,"opt_date":""}
    try:
        data = api_get("/MarketDataOfMajorInstitutionalTradersDetailsOfCallsAndPutsBytheDate")
        if not data:
            return opt
        dates = sorted(set(str(r.get("Date","")) for r in data if r.get("Date")), reverse=True)
        latest = dates[0]
        for row in data:
            if str(row.get("Date","")) != latest:
                continue
            if "TXO" not in str(row.get("ContractCode","")):
                continue
            item = str(row.get("Item","")).strip()
            cp   = str(row.get("CallPut","")).strip()
            identity = next((v for k,v in IDENTITY_MAP.items() if k in item), None)
            if not identity:
                continue
            is_call = "買權" in cp or cp.upper() in ("CALL","C")
            oi = parse_int(row.get("OpenInterest(Long)", 0))
            opt[f"{identity}_{'call' if is_call else 'put'}"] = oi
        opt["opt_date"] = fmt_date(latest)
        print(f"   外資 Call/Put：{opt['foreign_call']:,} / {opt['foreign_put']:,}", flush=True)
    except Exception as e:
        print(f"   ⚠️  選擇權失敗：{e}", flush=True)
    return opt


def fetch_taiex():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&range=2d"
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode())
        meta = d["chart"]["result"][0]["meta"]
        close = meta.get("regularMarketPrice", 0)
        prev  = meta.get("chartPreviousClose", close)
        chg   = round(close - prev, 2)
        pct   = round(chg / prev * 100, 2) if prev else 0
        import time as t
        ts = meta.get("regularMarketTime", int(t.time()))
        dt = datetime.fromtimestamp(ts, tz=TZ_TAIPEI)
        return {"close": round(close,2), "change": chg, "change_pct": pct,
                "date": dt.strftime("%Y/%m/%d")}
    except Exception as e:
        print(f"   ⚠️  加權指數失敗：{e}", flush=True)
        return None


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, int(round(v))))


def calc_scores(tx, mtx, tmf, opt):
    def sf(net, scale):
        return clamp(50 + (-net / scale) * 15)
    tx_s  = sf(tx.get("total",0),  30000)
    mtx_s = sf(mtx.get("total",0), 15000)
    tmf_s = sf(tmf.get("total",0), 80000)
    fc, fp = opt.get("foreign_call",0), opt.get("foreign_put",0)
    pc_s = clamp(50 - (fp/(fc+fp)-0.5)*140) if fc+fp > 0 else 50
    overall = clamp(tx_s*0.25 + mtx_s*0.25 + tmf_s*0.10 + pc_s*0.40)
    return {"tx":tx_s,"mtx":mtx_s,"tmf":tmf_s,"pc":pc_s,"overall":overall}


def load_existing():
    try:
        with open("data.json","r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")

    try:
        date, contracts = fetch_futures()
        if all(v["total"]==0 for v in contracts.values()):
            raise Exception("所有期貨數據為 0")
        print(f"✅ 日期：{date}", flush=True)
        for k,v in contracts.items():
            print(f"   {k}: {v}", flush=True)
    except Exception as e:
        print(f"❌ 失敗：{e}", file=sys.stderr, flush=True)
        existing = load_existing()
        if existing:
            existing["fetched_at"] = now_str
            existing["fetch_error"] = str(e)
            with open("data.json","w",encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            sys.exit(0)
        sys.exit(1)

    opt = fetch_options()
    opt["opt_date"] = opt.get("opt_date") or date

    print("📡 抓取加權指數...", flush=True)
    taiex = fetch_taiex()
    if taiex:
        print(f"✅ {taiex['close']:,.2f}（{taiex['change']:+.2f}）", flush=True)

    scores = calc_scores(contracts["tx"], contracts["mtx"], contracts["tmf"], opt)
    print(f"📊 {scores}", flush=True)

    existing = load_existing() or {}
    output = {
        "date": date, "fetched_at": now_str,
        "tx": contracts["tx"], "mtx": contracts["mtx"], "tmf": contracts["tmf"],
        "options": opt, "scores": scores,
    }
    if taiex:
        output["taiex"] = taiex
    if "crypto" in existing:
        output["crypto"] = existing["crypto"]

    with open("data.json","w",encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("💾 完成！", flush=True)


if __name__ == "__main__":
    main()
