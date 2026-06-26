#!/usr/bin/env python3
"""
台灣期貨交易所散戶籌碼數據抓取腳本
使用 curl 繞過 403，解析 markdown-table 格式回應
"""
import json, re, sys, subprocess
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
FUTURES_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
OPTIONS_URL = "https://www.taifex.com.tw/cht/3/callsAndPutsDateExcel"

def fetch(url):
    cmd = [
        "curl", "-s", "-L", "--max-time", "30",
        "--compressed",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "-H", "Connection: keep-alive",
        "-H", "Referer: https://www.taifex.com.tw/cht/3/futContractsDate",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=40)
    raw = result.stdout
    for enc in ("utf-8", "big5", "cp950"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")

def parse_int(s):
    s = str(s).strip().replace(",", "").replace("，", "")
    try:
        return int(s)
    except Exception:
        return 0

def extract_date(text):
    m = re.search(r"日期\s*(\d{4}/\d{2}/\d{2})", text)
    return m.group(1) if m else datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d")

def get_nums_from_cells(cells):
    nums = []
    for c in cells:
        c2 = c.replace(",", "").replace("，", "").strip()
        if re.match(r'^-?\d+$', c2):
            nums.append(int(c2))
    return nums

def parse_futures(html):
    date = extract_date(html)
    targets = {"臺股期貨": "tx", "小型臺指期貨": "mtx", "微型臺指期貨": "tmf"}
    result = {v: {"dealer": 0, "trust": 0, "foreign": 0, "total": 0} for v in targets.values()}

    # 把 HTML table 轉成 row list
    rows = []
    for line in html.split("\n"):
        line = line.strip()
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            rows.append(cells)

    current = None
    for cells in rows:
        # 偵測契約
        for name, key in targets.items():
            if any(name in c for c in cells):
                current = key
                break

        if current is None:
            continue

        # 偵測身份別
        identity = None
        for c in cells:
            if "自營商" in c: identity = "dealer"; break
            if "投信" in c:  identity = "trust";  break
            if "外資" in c:  identity = "foreign"; break

        if identity is None:
            continue

        nums = get_nums_from_cells(cells)
        # 每行有 12 個數字：交易(多口,多金,空口,空金,淨口,淨金) + 未平倉(同結構)
        # 未平倉多空淨額口數 = index 10
        if len(nums) >= 11:
            result[current][identity] = nums[10]
            if identity == "foreign":
                r = result[current]
                r["total"] = r["dealer"] + r["trust"] + r["foreign"]
                current = None

    return date, result

def parse_options(html):
    opt_date = extract_date(html)
    opt = {
        "foreign_call": 0, "foreign_put": 0,
        "dealer_call":  0, "dealer_put":  0,
        "trust_call":   0, "trust_put":   0,
        "opt_date": opt_date,
    }

    rows = []
    for line in html.split("\n"):
        line = line.strip()
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            rows.append(cells)

    in_txo = False
    is_call = True

    for cells in rows:
        if any("臺指選擇權" in c or "台指選擇權" in c for c in cells):
            in_txo = True
        if not in_txo:
            continue

        for c in cells:
            if "買權" in c: is_call = True; break
            if "賣權" in c: is_call = False; break

        identity = None
        for c in cells:
            if "自營商" in c: identity = "dealer"; break
            if "投信" in c:  identity = "trust";  break
            if "外資" in c:  identity = "foreign"; break

        if identity is None:
            continue

        nums = get_nums_from_cells(cells)
        # 未平倉買方口數 = index 6
        if len(nums) >= 7:
            key = f"{identity}_{'call' if is_call else 'put'}"
            opt[key] = nums[6]

    return opt

def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))

def calc_scores(tx, mtx, tmf, opt):
    def score_fut(net, scale):
        return int(clamp(round(50 + (-net / scale) * 15)))

    tx_s  = score_fut(tx.get("total", 0),  30000)
    mtx_s = score_fut(mtx.get("total", 0), 15000)
    tmf_s = score_fut(tmf.get("total", 0), 80000)

    fc, fp = opt.get("foreign_call", 0), opt.get("foreign_put", 0)
    if fc + fp > 0:
        pc_s = int(clamp(round(50 - (fp / (fc + fp) - 0.5) * 140)))
    else:
        pc_s = 50

    overall = int(clamp(round(tx_s*0.25 + mtx_s*0.25 + tmf_s*0.10 + pc_s*0.40)))
    return {"tx": tx_s, "mtx": mtx_s, "tmf": tmf_s, "pc": pc_s, "overall": overall}

def load_existing():
    """載入現有 data.json 作為 fallback"""
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")

    print("📡 抓取期交所期貨數據...")
    try:
        fut_html = fetch(FUTURES_URL)
        if len(fut_html) < 1000:
            raise Exception(f"回傳內容過短 ({len(fut_html)} bytes)，可能被封鎖")
        date, contracts = parse_futures(fut_html)
        all_zero = all(v["total"] == 0 for v in contracts.values())
        if all_zero:
            raise Exception("所有期貨數據解析結果為 0，解析失敗")
        print(f"✅ 期貨日期：{date}")
        for k, v in contracts.items():
            print(f"   {k.upper()} {v}")
    except Exception as e:
        print(f"❌ 期貨抓取/解析失敗：{e}", file=sys.stderr)
        existing = load_existing()
        if existing:
            print("⚠️  使用現有 data.json（保留舊數據，更新抓取時間）")
            existing["fetched_at"] = now_str
            existing["fetch_error"] = str(e)
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print("💾 data.json 已更新（保留舊數據）")
            sys.exit(0)
        else:
            sys.exit(1)

    print("📡 抓取期交所選擇權數據...")
    try:
        opt_html = fetch(OPTIONS_URL)
        opt = parse_options(opt_html)
        print(f"✅ 選擇權日期：{opt['opt_date']}")
        print(f"   外資 Call/Put：{opt['foreign_call']} / {opt['foreign_put']}")
    except Exception as e:
        print(f"⚠️  選擇權失敗（使用預設）：{e}")
        opt = {"foreign_call": 0, "foreign_put": 0, "dealer_call": 0,
               "dealer_put": 0, "trust_call": 0, "trust_put": 0, "opt_date": date}

    scores = calc_scores(contracts["tx"], contracts["mtx"], contracts["tmf"], opt)
    print(f"📊 評分：TX={scores['tx']} MTX={scores['mtx']} TMF={scores['tmf']} PC={scores['pc']} 綜合={scores['overall']}")

    output = {
        "date": date,
        "fetched_at": now_str,
        "tx":  contracts["tx"],
        "mtx": contracts["mtx"],
        "tmf": contracts["tmf"],
        "options": opt,
        "scores": scores,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("💾 data.json 寫入完成！")

if __name__ == "__main__":
    main()
