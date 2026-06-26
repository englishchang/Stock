#!/usr/bin/env python3
"""
台灣期貨交易所散戶籌碼數據抓取
解析 markdown table 格式（| 分隔）
"""
import json, re, sys, subprocess, time
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))
FUTURES_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
OPTIONS_URL = "https://www.taifex.com.tw/cht/3/callsAndPutsDateExcel"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def wget_fetch(url, retries=4, wait=12):
    for attempt in range(1, retries + 1):
        print(f"   嘗試 {attempt}/{retries}", flush=True)
        try:
            cmd = ["wget", "-q", "-O", "-", "--timeout=40", "--tries=1",
                   f"--user-agent={UA}",
                   "--header=Accept-Language: zh-TW,zh;q=0.9",
                   "--header=Referer: https://www.taifex.com.tw/cht/3/futContractsDate",
                   url]
            result = subprocess.run(cmd, capture_output=True, timeout=50)
            raw = result.stdout
            if not raw:
                raise Exception("空白回應")
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

def parse_num(s):
    s = str(s).strip().replace(",", "").replace("，", "").replace(" ", "")
    # 處理負號（全形或括號）
    s = s.replace("－", "-").replace("−", "-")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return int(s)
    except Exception:
        return None

def extract_date(text):
    m = re.search(r"日期\s*(\d{4}/\d{2}/\d{2})", text)
    if m: return m.group(1)
    m = re.search(r"(\d{4}/\d{2}/\d{2})", text)
    return m.group(1) if m else datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d")

def parse_md_rows(text):
    """解析 markdown table，回傳 list of list of str"""
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # 去掉頭尾空字串
        cells = [c for c in cells if c != ""]
        if cells:
            rows.append(cells)
    return rows

def parse_futures(text):
    date = extract_date(text)
    # 目標契約對照
    targets = {
        "臺股期貨": "tx",
        "小型臺指期貨": "mtx",
        "微型臺指期貨": "tmf",
    }
    result = {v: {"dealer": 0, "trust": 0, "foreign": 0, "total": 0} for v in targets.values()}

    rows = parse_md_rows(text)
    current = None

    for cells in rows:
        # 合併所有格文字偵測契約名稱
        row_text = " ".join(cells)
        for name, key in targets.items():
            if name in row_text:
                current = key
                break

        if current is None:
            continue

        # 偵測身份別
        identity = None
        for c in cells:
            if "自營商" in c: identity = "dealer"; break
            if "投信"  in c: identity = "trust";  break
            if "外資"  in c: identity = "foreign"; break

        if identity is None:
            continue

        # 提取所有數字欄（可能含負號）
        nums = []
        for c in cells:
            v = parse_num(c)
            if v is not None:
                nums.append(v)

        # 表格結構：多方口數,多方金額,空方口數,空方金額,淨額口數,淨額金額（交易） + 同6欄（未平倉）= 共12個數字
        # 未平倉多空淨額口數 = index 10（第11個數字）
        if len(nums) >= 11:
            net_oi = nums[10]
            result[current][identity] = net_oi
            print(f"   ✓ {current} {identity}: 未平倉淨額={net_oi}", flush=True)
            if identity == "foreign":
                r = result[current]
                r["total"] = r["dealer"] + r["trust"] + r["foreign"]
                current = None

    return date, result


def parse_options(text):
    opt_date = extract_date(text)
    opt = {
        "foreign_call": 0, "foreign_put": 0,
        "dealer_call":  0, "dealer_put":  0,
        "trust_call":   0, "trust_put":   0,
        "opt_date": opt_date,
    }
    rows = parse_md_rows(text)
    in_txo = False
    is_call = True

    for cells in rows:
        row_text = " ".join(cells)
        if "臺指選擇權" in row_text or "台指選擇權" in row_text:
            in_txo = True
        if not in_txo:
            continue
        if "買權" in row_text: is_call = True
        elif "賣權" in row_text: is_call = False

        identity = None
        for c in cells:
            if "自營商" in c: identity = "dealer"; break
            if "投信"  in c: identity = "trust";  break
            if "外資"  in c: identity = "foreign"; break
        if identity is None:
            continue

        nums = [parse_num(c) for c in cells if parse_num(c) is not None]
        # 未平倉買方口數 = index 6（第7個數字）
        if len(nums) >= 7:
            key = f"{identity}_{'call' if is_call else 'put'}"
            opt[key] = nums[6]

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
        date, contracts = parse_futures(fut_text)
        if all(v["total"] == 0 for v in contracts.values()):
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

    print("📡 抓取選擇權數據...", flush=True)
    try:
        opt_text = wget_fetch(OPTIONS_URL)
        opt = parse_options(opt_text)
        print(f"✅ 選擇權日期：{opt['opt_date']}", flush=True)
        print(f"   外資 Call/Put：{opt['foreign_call']} / {opt['foreign_put']}", flush=True)
    except Exception as e:
        print(f"⚠️  選擇權失敗：{e}", flush=True)
        opt = {"foreign_call": 0, "foreign_put": 0, "dealer_call": 0,
               "dealer_put": 0, "trust_call": 0, "trust_put": 0, "opt_date": date}

    scores = calc_scores(contracts["tx"], contracts["mtx"], contracts["tmf"], opt)
    print(f"📊 評分：{scores}", flush=True)

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
