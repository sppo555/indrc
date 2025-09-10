# GCP IP Address Collector

收集所有可存取 GCP 專案中的 IP 位址，並將結果輸出為單一 CSV 檔案。

## 功能
- 從所有可存取的專案收集以下來源的 IP：
  - 靜態位址（Addresses）
  - VM 實例的內外部位址（Instances: Internal/External）
  - 轉送規則位址（Forwarding Rules）
- 產生兩個檔案：
  - `gcp_all_ips_YYYYMMDD_HHMMSS.csv`：彙整所有專案的 IP 清單（含 `project_id` 欄位）。每次執行自動加上時間戳避免覆蓋。
  - `gcp_projects_status_YYYYMMDD_HHMMSS.csv`：各專案的 Compute Engine API 開啟狀態。每次執行自動加上時間戳避免覆蓋。

## 先決條件
- 已安裝 Google Cloud SDK（提供 `gcloud` 指令）
- Python 3.8+ 與相依套件（見 `requirements.txt`）
- 可用的服務帳戶金鑰 JSON 檔，並具備以下 IAM 權限：
  - Compute Viewer (`roles/compute.viewer`)
    - compute.addresses.get / list
    - compute.instances.get / list
    - compute.zones.list
    - compute.globalAddresses.get / list
  - Resource Manager Project Viewer (`roles/viewer`)
    - resourcemanager.projects.get / list
  - Service Account User (`roles/iam.serviceAccountUser`)

目前使用的服務帳戶：`gceprojectviewer@incdr-infra.iam.gserviceaccount.com`

## 安裝相依套件
```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 使用方式
支援兩種模式（以 `--mode` 指定）：

1) gcloud 模式（使用你目前 `gcloud` 登入的使用者）
```bash
./venv/bin/python list_ips.py --mode gcloud
```
說明：
- 會透過 `gcloud auth print-access-token` 取得權杖，無需再執行 `gcloud auth application-default login`。
- 列出專案與呼叫 API 都使用你目前的 gcloud 使用者環境。

執行時會顯示目前模式與帳號，例如：
```text
Mode: gcloud
Gcloud Account: alex@winsportss.com
Using gcloud user credentials (access token from gcloud auth).
```

2) service_account 模式（使用服務帳戶金鑰）
```bash
./venv/bin/python list_ips.py --mode service_account --credentials /path/to/key.json
```
或使用環境變數（擇一）讓程式自動偵測：
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
# 或
export GCP_SA_KEY_PATH=/path/to/key.json

./venv/bin/python list_ips.py --mode service_account
```

程式也會嘗試在常見位置尋找金鑰：
- 當前資料夾或上一層的 `incdr-infra.json`
- `~/Documents/incdr-infra.json`
- `~/incdr-infra.json`

service_account 模式執行時也會顯示使用的服務帳號，例如：
```text
Mode: service_account
Using credentials file: /path/to/key.json
Service Account: your-sa@project.iam.gserviceaccount.com
```

> 備註：`--auth` 參數已標示為 Deprecated，僅保留向下相容，請改用 `--mode`。

## 輸出說明
- `gcp_all_ips_YYYYMMDD_HHMMSS.csv`
  - 欄位：`project_id, ip_address, name, access_type, status, region, source, user, ip_version`
  - 內容：所有已啟用 Compute Engine API 的專案中蒐集到的唯一 IP 清單
- `gcp_projects_status_YYYYMMDD_HHMMSS.csv`
  - 欄位：`project_id, compute_api_status`
  - 內容：各專案 Compute Engine API 是否啟用（ENABLED / DISABLED）

## 疑難排解
- 看不到任何專案：請確認本機有安裝 `gcloud`，且服務帳戶具備 `roles/viewer` 可列出專案。
- 專案狀態顯示 DISABLED：該專案尚未啟用 Compute Engine API。
- 權限不足：請為服務帳戶補齊上方列出的 IAM 角色。

## 開發說明
主要邏輯見 `list_ips.py`：
- `get_all_projects()`：使用 `gcloud` 依服務帳戶列出可存取之專案
- `is_api_enabled()`：檢查專案是否已啟用 Compute Engine API
- `get_ips_for_project()`：彙整單一專案的各類 IP
- `main()`：彙總所有專案 IP 並輸出 CSV