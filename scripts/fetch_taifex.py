#!/usr/bin/env python3
"""
台灣期貨交易所散戶籌碼數據抓取
改用 CSV 下載端點，更穩定可靠
"""
import json, re, sys, subprocess, time, csv, io
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))

# CSV 下載端點（比 Excel HTML 更穩定）
FUTURES_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
OPTIONS_URL = "https://www.taifex.com.tw/cht/3/callsAndPutsDateExcel"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def wget_fetch(url, retries=4, wait=12):
    for attempt in range(1, retries + 1):
        print(f"   嘗試 {attempt}/{retries}", flush=True)
        try:
            cmd = [
                "wget", "-q", "-O", "-",
                "--timeout=40", "--tries=1",
                f"--user-agent={UA}",
                "--header=Accept-Language: zh-TW,zh;q=0.9",
                "--header=Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                "--header=Referer: https://www.taifex.com.tw/cht/3/futContractsDate",
                "--header=Connection: keep-alive",
                url
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=50)
            raw = result.stdout
            if not raw:
                raise Exception("空白回應")
            # 嘗試各種編碼
            for enc in ("utf-8", "big5", "cp950"):
                try:
                    text = raw.decode(enc)
                    if len(text) > 500:
                        return text
                except Exception:
                    pass
            raise Exception(f"無法解碼 ({len(raw)} bytes)")
        except Exception as e:
            print(f"   ⚠️  {e}", flush=True)
            if attempt < retries:
                print(f"   等待 {wait}s...", flush=True)
                time.sleep(wait)
    raise Exception(f"連線失敗，已重試 {retries} 次")

def parse_int(s):
    s = str(s).strip().replace(",", "").replace("，", "").replace(" ", "")
    try:
        return int(s)
    except Exception:
        return 0

def extract_date(text):
    m = re.search(r"日期[,\s]*(\d{4}/\d{2}/\d{2})", text)
    if m: return m.group(1)
    m = re.search(r"(\d{4}/\d{2}/\d{2})", text)
    if m: return m.group(1)
    return datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d")

def parse_futures(text):
    """
    解析期交所三大法人期貨未平倉
    表格格式：每個契約 3 行（自營商/投信/外資）
    每行約 12 欄數字：交易(多口,多金,空口,空金,淨口,淨金) + 未平倉(同)
    我們需要「未平倉」的「淨額口數」= 第 11 個數字 (index 10)
    """
    date = extract_date(text)
    targets = {
        "臺股期貨": "tx",
        "小型臺指期貨": "mtx",
        "微型臺指期貨": "tmf",
    }
    result = {v: {"dealer": 0, "trust": 0, "foreign": 0, "total": 0} for v in targets.values()}

    # 取得所有行
    lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]

    current = None
    for line in lines:
        # 偵測契約名稱
        for name, key in targets.items():
            if name in line:
                current = key
                break

        if current is None:
            continue

        # 偵測身份別
        identity = None
        if "自營商" in line: identity = "dealer"
        elif "投信" in line: identity = "trust"
        elif "外資" in line: identity = "foreign"

        if identity is None:
            continue

        # 提取所有數字（含負號）
        nums = re.findall(r'-?\d[\d,，]*', line)
        nums = [parse_int(n) for n in nums]

        # 過濾掉年月日等無關數字（通常都很大或是年份格式）
        # 保留合理範圍的口數數字
        valid = [n for n in nums if -999999 <= n <= 999999]

        print(f"   {current} {identity}: 原始數字={nums[:15]}, 有效={valid[:12]}", flush=True)

        # 未平倉淨額口數：在 12 個數字中的 index 10
        if len(valid) >= 11:
            net_oi = valid[10]
            result[current][identity] = net_oi
            print(f"   → 未平倉淨額={net_oi}", flush=True)

            if identity == "foreign":
                r = result[current]
                r["total"] = r["dealer"] + r["trust"] + r["foreign"]
                current = None  # 本契約讀完

    return date, result

def parse_options(text):
    opt_date = extract_date(text)
    opt = {
        "foreign_call": 0, "foreign_put": 0,
        "dealer_call":  0, "dealer_put":  0,
        "trust_call":   0, "trust_put":   0,
        "opt_date": opt_date,
    }

    lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
    in_txo = False
    is_call = True

    for line in lines:
        if "臺指選擇權" in line or "台指選擇權" in line:
            in_txo = True
        if not in_txo:
            continue
        if "買權" in line: is_call = True
        elif "賣權" in line: is_call = False

        identity = None
        if "自營商" in line: identity = "dealer"
        elif "投信" in line: identity = "trust"
        elif "外資" in line: identity = "foreign"
        if identity is None: continue

        nums = re.findall(r'-?\d[\d,，]*', line)
        valid = [parse_int(n) for n in nums if -999999 <= parse_int(n) <= 999999]

        if len(valid) >= 7:
            oi = valid[6]
            key = f"{identity}_{'call' if is_call else 'put'}"
            opt[key] = oi

    return opt

def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, int(round(v))))

def calc_scores(tx, mtx, tmf, opt):
    def sf(net, scale):
        return clamp(50 + (-net / scale) * 15)
    tx_s  = sf(tx.get("total", 0),  30000)
    mtx_s = sf(mtx.get("total", 0), 15000)
    tmf_s = sf(tmf.get("total", 0), 80000)
    fc, fp = opt.get("foreign_call", 0), opt.get("foreign_put", 0)
    pc_s = clamp(50 - (fp/(fc+fp) - 0.5)*140) if fc+fp > 0 else 50
    overall = clamp(tx_s*0.25 + mtx_s*0.25 + tmf_s*0.10 + pc_s*0.40)
    return {"tx": tx_s, "mtx": mtx_s, "tmf": tmf_s, "pc": pc_s, "overall": overall}

def load_existing():
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")

    print("📡 抓取期交所期貨數據...", flush=True)
    try:
        fut_text = wget_fetch(FUTURES_URL)
        print(f"   回傳長度：{len(fut_text)} chars", flush=True)
        # 印出前500字供除錯
        preview = fut_text[:500].replace("\n", " | ")
        print(f"   預覽：{preview}", flush=True)

        date, contracts = parse_futures(fut_text)
        all_zero = all(v["total"] == 0 for v in contracts.values())
        if all_zero:
            raise Exception("所有期貨數據解析為 0")
        print(f"✅ 期貨日期：{date}", flush=True)
        for k, v in contracts.items():
            print(f"   {k}: {v}", flush=True)
    except Exception as e:
        print(f"❌ 失敗：{e}", file=sys.stderr, flush=True)
        existing = load_existing()
        if existing:
            print("⚠️  保留舊數據", flush=True)
            existing["fetched_at"] = now_str
            existing["fetch_error"] = str(e)
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            sys.exit(0)
        sys.exit(1)

    print("📡 抓取期交所選擇權數據...", flush=True)
    try:
        opt_text = wget_fetch(OPTIONS_URL)
        opt = parse_options(opt_text)
        print(f"✅ 選擇權日期：{opt['opt_date']}", flush=True)
        print(f"   外資 Call/Put：{opt['foreign_call']} / {opt['foreign_put']}", flush=True)
    except Exception as e:
        print(f"⚠️  選擇權失敗（用預設值）：{e}", flush=True)
        opt = {"foreign_call": 0, "foreign_put": 0, "dealer_call": 0,
               "dealer_put": 0, "trust_call": 0, "trust_put": 0, "opt_date": date}

    scores = calc_scores(contracts["tx"], contracts["mtx"], contracts["tmf"], opt)
    print(f"📊 評分：{scores}", flush=True)

    # 載入現有 crypto 數據保留
    existing = load_existing() or {}
    output = {
        "date": date,
        "fetched_at": now_str,
        "tx":  contracts["tx"],
        "mtx": contracts["mtx"],
        "tmf": contracts["tmf"],
        "options": opt,
        "scores": scores,
    }
    if "crypto" in existing:
        output["crypto"] = existing["crypto"]

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("💾 data.json 寫入完成！", flush=True)

if __name__ == "__main__":
    main()
