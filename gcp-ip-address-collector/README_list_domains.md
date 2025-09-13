# Cloud Domains 列表工具（list_domains.py）使用說明

本檔提供 `list_domains.py` 的使用教學。此工具可以列出指定專案下的 Google Cloud Domains 註冊資訊，並輸出為 CSV。

僅參考了 `list_ips.py` 的「認證模式（--mode）」行為，其他邏輯未引用。

---

## 功能簡介
- 列出專案的 Cloud Domains 註冊（registrations）。
- 支援兩種認證模式：
  - `service_account`：使用服務帳戶金鑰 JSON
  - `gcloud`：使用目前 gcloud 使用者的 access token
- 產生 CSV 檔，檔名格式：`gcp_domains_<project_id>_<YYYYMMDD_HHMMSS>.csv`

---

## 先決條件
- 已安裝 Python 3.9+（建議 3.10 以上）
- 已安裝相依套件（見下方安裝步驟）
- 目標專案已啟用 Cloud Domains API：`domains.googleapis.com`
- 具備足夠的 IAM 權限（只讀）：
  - 建議角色：`roles/domains.viewer`（Cloud Domains Viewer）
  - 若使用服務帳戶，請將該服務帳戶授權到目標專案
  - 若使用 gcloud 模式，請以具備對目標專案查看權限的使用者登入

> 若你之前僅有 Compute Viewer 等角色，請另外為該服務帳戶或使用者補上 Cloud Domains 相關查看權限。

---

## 安裝步驟
在目錄 `indrc/gcp-ip-address-collector/` 下執行：

```bash
# 建議使用虛擬環境
python3 -m venv venv
source venv/bin/activate

# 安裝依賴
pip install -r requirements.txt
```

> 備註：`requirements.txt` 由 `list_ips.py` 專案共用，已包含本工具需要的核心套件（google-api-python-client、pandas 等）。

---

## 使用方式

### 1) 使用服務帳戶金鑰 JSON（指定路徑）
```bash
python3 list_domains.py --mode service_account --credentials /path/to/key.json -p india-game-pro
```

### 2) 使用服務帳戶金鑰 JSON（自動尋找）
不加 `--credentials` 時，程式會自動從以下來源尋找金鑰：
- 環境變數：`GOOGLE_APPLICATION_CREDENTIALS` 或 `GCP_SA_KEY_PATH`
- 常見路徑：
  - `~/Documents/incdr-infra.json`
  - `~/incdr-infra.json`

範例：
```bash
export GOOGLE_APPLICATION_CREDENTIALS=~/Documents/incdr-infra.json
python3 list_domains.py --mode service_account -p india-game-pro
```

### 3) 使用 gcloud 使用者認證
```bash
# 先確保已登入並選對帳號
gcloud auth login

# 列出 india-game-pro 專案的 Cloud Domains
python3 list_domains.py --mode gcloud -p india-game-pro
```

---

## 參數說明
- `-p, --project`：目標專案 ID（必填）。例如：`india-game-pro`
- `-c, --credentials`：服務帳戶金鑰 JSON 的路徑（`--mode service_account` 可選）。
- `--mode`：認證模式，`service_account`（預設）或 `gcloud`。

---

## 範例輸出
執行後，會在目前目錄產生 CSV 檔，例如：
```
gcp_domains_india-game-pro_20250912_184500.csv
```
欄位包含：
- `project_id`
- `domain_name`
- `state`
- `expire_time`
- `contact_privacy`
- `registration_name`
- `dns_settings`（JSON 字串）

終端機也會列出簡要摘要，例如：
```
- example.com | state=ACTIVE | expire=2026-01-01T00:00:00Z
```

---

## 常見問題
- 問：遇到「Permission denied」或「Not authorized」？
  - 答：請確認：
    - 已啟用 `domains.googleapis.com`
    - 服務帳戶或 gcloud 使用者擁有 `roles/domains.viewer`（或更高權限）
- 問：顯示「No Cloud Domains registrations found.」？
  - 答：目標專案目前沒有任何 Cloud Domains 註冊，或權限不足導致清單為空。
- 問：使用 `--mode gcloud` 仍失敗？
  - 答：執行 `gcloud auth list` 確認當前使用者；或使用 `--mode service_account` 以服務帳戶測試。

---

## 檔案位置
- 指令檔案：`indrc/gcp-ip-address-collector/list_domains.py`
- 本說明：`indrc/gcp-ip-address-collector/README_list_domains.md`
