#!/usr/bin/env python3
"""
台灣期貨交易所散戶籌碼數據抓取腳本
每日盤後自動執行，從期交所取得三大法人未平倉數據，計算散戶多空比指標
"""
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TZ_TAIPEI = timezone(timedelta(hours=8))

FUTURES_URL  = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
OPTIONS_URL  = "https://www.taifex.com.tw/cht/3/callsAndPutsDateExcel"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; taifex-dashboard/1.0)",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://www.taifex.com.tw/",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    for enc in ("utf-8", "big5", "cp950"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def parse_int(s):
    if not s:
        return 0
    s = s.strip().replace(",", "").replace("，", "")
    try:
        return int(s)
    except Exception:
        return 0


def extract_date(html):
    m = re.search(r"日期\s*(\d{4}/\d{2}/\d{2})", html)
    return m.group(1) if m else datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d")


def parse_futures(html):
    """
    解析三大法人期貨未平倉表格
    目標契約：臺股期貨(TX)、小型臺指(MTX)、微型臺指(TMF)
    欄位：自營商、投信、外資 × 多空淨額（未平倉）
    """
    date = extract_date(html)

    # 移除 HTML tags
    text = re.sub(r"<[^>]+>", "\t", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    flat  = "\t".join(lines)
    cells = [c.strip() for c in flat.split("\t") if c.strip()]

    contracts = {
        "臺股期貨": "tx",
        "小型臺指期貨": "mtx",
        "微型臺指期貨": "tmf",
    }
    result = {v: {"dealer": 0, "trust": 0, "foreign": 0, "total": 0} for v in contracts.values()}

    i = 0
    current = None
    row_count = 0  # 0=自營商, 1=投信, 2=外資

    while i < len(cells):
        c = cells[i]
        # 偵測契約名稱
        for name, key in contracts.items():
            if name in c:
                current = key
                row_count = 0
                break

        if current and re.match(r"^-?\d[\d,，]*$", c):
            # 找到數字區段，嘗試解析未平倉多空淨額
            # 未平倉欄在交易欄之後，每組有 多方口數、多方金額、空方口數、空方金額、淨額口數、淨額金額
            # 我們需要「未平倉」部分的淨額口數（第12個數字段 in 18-col row）
            # 策略：收集連續數字，找到第12個（index 11）
            nums = []
            j = i
            while j < len(cells) and (re.match(r"^-?\d[\d,，]*$", cells[j]) or cells[j] in ("0",)):
                nums.append(parse_int(cells[j]))
                j += 1
            # 完整一行有 12 個數字（6交易 + 6未平倉）
            # 未平倉多空淨額口數在 index 10（第11個）
            if len(nums) >= 11:
                net_oi = nums[10]
                if row_count == 0:
                    result[current]["dealer"] = net_oi
                elif row_count == 1:
                    result[current]["trust"]  = net_oi
                elif row_count == 2:
                    result[current]["foreign"] = net_oi
                    result[current]["total"] = (
                        result[current]["dealer"] +
                        result[current]["trust"]  +
                        result[current]["foreign"]
                    )
                    current = None  # reset after 3 rows
                row_count += 1
                i = j
                continue
        i += 1

    return date, result


def parse_options(html):
    """
    解析選擇權三大法人未平倉（台指選擇權 TXO）
    目標：外資買權未平倉、外資賣權未平倉
    """
    opt_date = extract_date(html)

    text = re.sub(r"<[^>]+>", "\t", html)
    text = re.sub(r"&nbsp;", " ", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    flat  = "\t".join(lines)
    cells = [c.strip() for c in flat.split("\t") if c.strip()]

    opt = {
        "foreign_call": 0, "foreign_put": 0,
        "dealer_call":  0, "dealer_put":  0,
        "trust_call":   0, "trust_put":   0,
        "opt_date": opt_date,
    }

    in_txo      = False
    call_block  = False
    put_block   = False
    dealer_done = False
    trust_done  = False
    foreign_done= False

    i = 0
    while i < len(cells):
        c = cells[i]

        if "臺指選擇權" in c or "台指選擇權" in c:
            in_txo = True
        if not in_txo:
            i += 1; continue

        if "買權" in c:
            call_block = True; put_block = False
            dealer_done = trust_done = foreign_done = False
        elif "賣權" in c:
            put_block = True; call_block = False
            dealer_done = trust_done = foreign_done = False

        if "自營商" in c and not dealer_done:
            # 找到下一組數字，未平倉買方口數在第7個（index 6）
            nums = []
            j = i + 1
            while j < len(cells) and len(nums) < 12:
                if re.match(r"^-?\d[\d,，]*$", cells[j]):
                    nums.append(parse_int(cells[j]))
                j += 1
            if len(nums) >= 7:
                oi = nums[6]
                if call_block:
                    opt["dealer_call"] = oi; dealer_done = True
                elif put_block:
                    opt["dealer_put"]  = oi; dealer_done = True

        elif "投信" in c and not trust_done:
            nums = []
            j = i + 1
            while j < len(cells) and len(nums) < 12:
                if re.match(r"^-?\d[\d,，]*$", cells[j]):
                    nums.append(parse_int(cells[j]))
                j += 1
            if len(nums) >= 7:
                oi = nums[6]
                if call_block:
                    opt["trust_call"] = oi; trust_done = True
                elif put_block:
                    opt["trust_put"]  = oi; trust_done = True

        elif "外資" in c and not foreign_done:
            nums = []
            j = i + 1
            while j < len(cells) and len(nums) < 12:
                if re.match(r"^-?\d[\d,，]*$", cells[j]):
                    nums.append(parse_int(cells[j]))
                j += 1
            if len(nums) >= 7:
                oi = nums[6]
                if call_block:
                    opt["foreign_call"] = oi; foreign_done = True
                elif put_block:
                    opt["foreign_put"]  = oi; foreign_done = True

        i += 1

    return opt


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def calc_scores(tx, mtx, tmf, opt):
    """
    散戶多空比評分（玩股網作法）
    散戶推估 = -(三大法人合計淨口數)
    分數基準50，法人淨空 → 散戶偏多 → 分數 > 50
    """
    def score_futures(net_inst, scale):
        # net_inst: 法人合計淨口數（負=法人空）
        retail_net = -net_inst  # 散戶偏多為正
        raw = 50 + (retail_net / scale) * 15
        return int(clamp(round(raw)))

    tx_s  = score_futures(tx.get("total", 0),  30000)
    mtx_s = score_futures(mtx.get("total", 0), 15000)
    tmf_s = score_futures(tmf.get("total", 0), 80000)

    fc, fp = opt.get("foreign_call", 0), opt.get("foreign_put", 0)
    total_opt = fc + fp
    if total_opt > 0:
        put_ratio = fp / total_opt
        if put_ratio > 0.60:
            pc_s = int(clamp(round(50 - (put_ratio - 0.5) * 140)))
        elif put_ratio < 0.40:
            pc_s = int(clamp(round(50 + (0.5 - put_ratio) * 140)))
        else:
            pc_s = 50
    else:
        pc_s = 50

    overall = int(clamp(round(tx_s * 0.25 + mtx_s * 0.25 + tmf_s * 0.10 + pc_s * 0.40)))

    return {"tx": tx_s, "mtx": mtx_s, "tmf": tmf_s, "pc": pc_s, "overall": overall}


def main():
    print("📡 抓取期交所期貨數據...")
    try:
        fut_html = fetch(FUTURES_URL)
    except Exception as e:
        print(f"❌ 期貨數據抓取失敗：{e}", file=sys.stderr)
        sys.exit(1)

    date, contracts = parse_futures(fut_html)
    print(f"✅ 期貨數據日期：{date}")
    print(f"   TX  合計：{contracts['tx']}")
    print(f"   MTX 合計：{contracts['mtx']}")
    print(f"   TMF 合計：{contracts['tmf']}")

    print("📡 抓取期交所選擇權數據...")
    try:
        opt_html = fetch(OPTIONS_URL)
    except Exception as e:
        print(f"⚠️  選擇權數據抓取失敗（使用預設值）：{e}", file=sys.stderr)
        opt = {"foreign_call": 0, "foreign_put": 0, "dealer_call": 0,
               "dealer_put": 0, "trust_call": 0, "trust_put": 0,
               "opt_date": date}
    else:
        opt = parse_options(opt_html)
        print(f"✅ 選擇權數據日期：{opt['opt_date']}")
        print(f"   外資 Call/Put：{opt['foreign_call']} / {opt['foreign_put']}")

    scores = calc_scores(contracts["tx"], contracts["mtx"], contracts["tmf"], opt)
    print(f"📊 評分：TX={scores['tx']} MTX={scores['mtx']} TMF={scores['tmf']} PC={scores['pc']} 綜合={scores['overall']}")

    output = {
        "date": date,
        "fetched_at": datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d %H:%M"),
        "tx":  contracts["tx"],
        "mtx": contracts["mtx"],
        "tmf": contracts["tmf"],
        "options": opt,
        "scores": scores,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("💾 data.json 已寫入")


if __name__ == "__main__":
    main()
