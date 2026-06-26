#!/usr/bin/env python3
"""
台灣期貨交易所散戶籌碼數據抓取腳本
用 wget 加重試，解析 markdown-table 格式
"""
import json, re, sys, subprocess, time
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
FUTURES_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
OPTIONS_URL = "https://www.taifex.com.tw/cht/3/callsAndPutsDateExcel"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def fetch(url, retries=4, wait=10):
    """用 wget 抓取，失敗重試"""
    for attempt in range(1, retries + 1):
        print(f"   嘗試 {attempt}/{retries}: {url}")
        try:
            cmd = [
                "wget", "-q", "-O", "-",
                "--timeout=40",
                "--tries=1",
                f"--user-agent={UA}",
                "--header=Accept-Language: zh-TW,zh;q=0.9",
                "--header=Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                "--header=Referer: https://www.taifex.com.tw/cht/3/futContractsDate",
                url
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=50)
            raw = result.stdout
            for enc in ("utf-8", "big5", "cp950"):
                try:
                    text = raw.decode(enc)
                    if len(text) > 1000 and ("臺股期貨" in text or "臺指選擇權" in text):
                        return text
                except Exception:
                    pass
            print(f"   ⚠️  回傳內容不符預期（{len(raw)} bytes）")
        except subprocess.TimeoutExpired:
            print(f"   ⚠️  逾時")
        except Exception as e:
            print(f"   ⚠️  錯誤：{e}")

        if attempt < retries:
            print(f"   等待 {wait} 秒後重試...")
            time.sleep(wait)

    raise Exception(f"連線失敗，已重試 {retries} 次")

def parse_int(s):
    s = str(s).strip().replace(",", "").replace("，", "")
    try:
        return int(s)
    except Exception:
        return 0

def extract_date(text):
    m = re.search(r"日期\s*(\d{4}/\d{2}/\d{2})", text)
    return m.group(1) if m else datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d")

def get_nums(cells):
    nums = []
    for c in cells:
        c2 = c.replace(",", "").replace("，", "").strip()
        if re.match(r'^-?\d+$', c2):
            nums.append(int(c2))
    return nums

def table_rows(html):
    rows = []
    for line in html.split("\n"):
        line = line.strip()
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            rows.append(cells)
    return rows

def parse_futures(html):
    date = extract_date(html)
    targets = {"臺股期貨": "tx", "小型臺指期貨": "mtx", "微型臺指期貨": "tmf"}
    result = {v: {"dealer": 0, "trust": 0, "foreign": 0, "total": 0} for v in targets.values()}
    current = None

    for cells in table_rows(html):
        for name, key in targets.items():
            if any(name in c for c in cells):
                current = key
                break

        if current is None:
            continue

        identity = None
        for c in cells:
            if "自營商" in c: identity = "dealer"; break
            if "投信" in c:   identity = "trust";  break
            if "外資" in c:   identity = "foreign"; break

        if identity is None:
            continue

        nums = get_nums(cells)
        # 共 12 數字；未平倉多空淨額口數 = index 10
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
    in_txo = False
    is_call = True

    for cells in table_rows(html):
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
            if "投信" in c:   identity = "trust";  break
            if "外資" in c:   identity = "foreign"; break

        if identity is None:
            continue

        nums = get_nums(cells)
        if len(nums) >= 7:
            key = f"{identity}_{'call' if is_call else 'put'}"
            opt[key] = nums[6]

    return opt

def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))

def calc_scores(tx, mtx, tmf, opt):
    def sf(net, scale):
        return int(clamp(round(50 + (-net / scale) * 15)))

    tx_s  = sf(tx.get("total", 0),  30000)
    mtx_s = sf(mtx.get("total", 0), 15000)
    tmf_s = sf(tmf.get("total", 0), 80000)

    fc, fp = opt.get("foreign_call", 0), opt.get("foreign_put", 0)
    pc_s = int(clamp(round(50 - (fp/(fc+fp) - 0.5) * 140))) if fc+fp > 0 else 50
    overall = int(clamp(round(tx_s*0.25 + mtx_s*0.25 + tmf_s*0.10 + pc_s*0.40)))
    return {"tx": tx_s, "mtx": mtx_s, "tmf": tmf_s, "pc": pc_s, "overall": overall}

def load_existing():
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def main():
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M")

    # ── 期貨 ──
    print("📡 抓取期貨數據...")
    try:
        fut_html = fetch(FUTURES_URL)
        date, contracts = parse_futures(fut_html)
        if all(v["total"] == 0 for v in contracts.values()):
            raise Exception("所有期貨數據為 0，疑似解析失敗")
        print(f"✅ 日期：{date}")
        for k, v in contracts.items():
            print(f"   {k.upper()} {v}")
    except Exception as e:
        print(f"❌ 失敗：{e}", file=sys.stderr)
        existing = load_existing()
        if existing:
            print("⚠️  保留舊數據，更新抓取時間")
            existing["fetched_at"] = now_str
            existing["fetch_error"] = str(e)
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            sys.exit(0)
        sys.exit(1)

    # ── 選擇權 ──
    print("📡 抓取選擇權數據...")
    try:
        opt_html = fetch(OPTIONS_URL)
        opt = parse_options(opt_html)
        print(f"✅ 選擇權日期：{opt['opt_date']}")
        print(f"   外資 Call/Put：{opt['foreign_call']} / {opt['foreign_put']}")
    except Exception as e:
        print(f"⚠️  選擇權失敗（用預設值）：{e}")
        opt = {"foreign_call": 0, "foreign_put": 0, "dealer_call": 0,
               "dealer_put": 0, "trust_call": 0, "trust_put": 0, "opt_date": date}

    scores = calc_scores(contracts["tx"], contracts["mtx"], contracts["tmf"], opt)
    print(f"📊 TX={scores['tx']} MTX={scores['mtx']} TMF={scores['tmf']} PC={scores['pc']} 綜合={scores['overall']}")

    output = {
        "date": date,
        "fetched_at": now_str,
        "tx": contracts["tx"], "mtx": contracts["mtx"], "tmf": contracts["tmf"],
        "options": opt, "scores": scores,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("💾 data.json 寫入完成！")

if __name__ == "__main__":
    main()
