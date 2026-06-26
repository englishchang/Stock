# 台灣期貨散戶籌碼儀表板

每日盤後自動從[臺灣期貨交易所](https://www.taifex.com.tw)抓取三大法人未平倉數據，計算散戶多空比指標，部署至 GitHub Pages。

## 線上查看

👉 **https://你的帳號.github.io/taifex-dashboard/**

## 指標說明

| 指標 | 說明 |
|------|------|
| 散戶多空比 | 全市場未平倉 − 三大法人 = 散戶推估，法人淨空 → 散戶被動偏多 |
| 評分 0–100 | 50=中性；≥65 極端偏多（反指標警示）；≤35 偏空 |
| 綜合評分 | 台指期 25% + 小台 25% + 微台 10% + 選擇權 40% |

## 自動更新排程

GitHub Actions 每週一至週五 **台北時間 16:30** 自動執行：
1. Python 腳本抓取期交所數據 → 計算評分 → 更新 `data.json`
2. 重新部署 GitHub Pages

---

## 快速部署（5 分鐘）

### 1. Fork 此 repo

點擊右上角 **Fork** 按鈕。

### 2. 啟用 GitHub Pages

Settings → Pages → Source 選 **GitHub Actions** → Save

### 3. 開啟 Actions 寫入權限

Settings → Actions → General → Workflow permissions
→ 選 **Read and write permissions** → Save

### 4. 手動觸發第一次

Actions → 每日更新期交所數據 → **Run workflow**

約 1 分鐘後網站上線：`https://你的帳號.github.io/taifex-dashboard/`

---

## 本地測試

```bash
python scripts/fetch_taifex.py   # 抓數據
cat data.json                     # 查看結果
python -m http.server 8080        # 啟動本地預覽
```

## 檔案結構

```
taifex-dashboard/
├── index.html                  # 儀表板（靜態，讀取 data.json）
├── data.json                   # 期交所數據（Actions 自動更新）
├── scripts/fetch_taifex.py     # 抓取 & 計算腳本
└── .github/workflows/update.yml
```

## 免責聲明

本儀表板僅供參考，不構成任何投資建議。
