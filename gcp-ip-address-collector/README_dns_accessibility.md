# DNS 可訪問性檢測說明

此說明檔針對 `check_dns_accessibility.py` 腳本，協助你檢測由 `list_india_domains.py` 匯出的 DNS 記錄是否能在 80 與 443 端口建立連線，並同步擷取 SSL 憑證資訊。

## 功能概要

- **80/443 端口檢測**：透過 TCP 連線判斷端口可訪問性與耗時。
- **SSL 憑證解析**：即使遠端伺服器使用自簽或過期憑證，也會抓取憑證到期日、頒發者與主體資訊。
- **多執行緒加速**：以 `ThreadPoolExecutor` 並行處理大量記錄，縮短執行時間。
- **彈性逾時設定**：可依實際網路狀況調整 `--timeout`，防止慢速主機被誤判為不可用。

## 前置需求

- Python 3.9 以上版本。
- 推薦啟用虛擬環境並安裝 `requirements.txt`（實際使用多為標準庫，無額外依賴）。
- 已透過 `list_india_domains.py` 生成含有 `record_type`、`record_value` 等欄位的 CSV。

```bash
cd /Users/alex/Documents/indrc/gcp-ip-address-collector
source venv/bin/activate
```

## 基本用法

```bash
python3 check_dns_accessibility.py --input-file india_game_pro_domains_dns_detailed_20250924_145734.csv
```

## 參數說明

- **`--input-file`, `-i`**（必填）
  指定欲檢測的輸入 CSV 路徑。

- **`--output-file`, `-o`**（選填）
  自訂輸出 CSV 路徑。未指定時會在輸入檔名後附加 `_accessibility`。

- **`--timeout`**（選填，預設 `5` 秒）
  控制 TCP/SSL 連線的逾時秒數。例如跨國環境可設為 `8`~`10` 秒：
  ```bash
  python3 check_dns_accessibility.py \
    --input-file india_game_pro_domains_dns_detailed_20250924_145734.csv \
    --timeout 8
  ```

- **`--threads`, `-t`**（選填，預設 `10`）
  調整併發處理的執行緒數量。主機限制或網路受限時可適度降低。

## 範例：完整命令

```bash
python3 check_dns_accessibility.py \
  --input-file india_game_pro_domains_dns_detailed_20250925_154258.csv \
  --timeout 5 \
  --threads 20 \
  --output-file india_game_pro_domains_dns_detailed_20250925_154258_accessibility_v3.csv
```

## 檢測細節

- **A 記錄**：若 `record_value` 是合法 IPv4/IPv6，腳本直接使用該 IP 建立連線。即使當前 DNS 查詢結果為 `NXDOMAIN`，仍會以 CSV 中保存的 IP 進行檢測。
- **CNAME 記錄**：對 `record_name` 域名建立連線，依賴系統 DNS 解析。
- **HTTPS 憑證**：使用 `ssl_sock.getpeercert(binary_form=True)` 取得 DER 憑證並轉成 PEM，再解析 `subject`、`issuer`、`notAfter` 等欄位。若憑證缺失或無法解析，會於 `cert_error` 記錄原因。
- **自簽判定**：優先沿用輸出欄位中的 `self_signed`，若欄位缺失則依據 OpenSSL 驗證錯誤碼 (`cert_verify_code` 18/19/20/21) 或錯誤訊息包含 `self signed` 判定。搭配 `cert_trust_status`、`cert_verify_error` 可協助分析信任問題。

## 輸出欄位

輸出 CSV 保留原始欄位並新增下列內容：

- `port_80_accessible`：80 端口是否成功連線 (`True`/`False`)
- `port_80_response_time`：開啟 TCP 所耗秒數（秒，保留 3 位小數）
- `port_80_error`：80 端口的錯誤訊息（若成功則為空）
- `port_443_accessible`：443 端口是否成功連線
- `port_443_response_time`：443 端口連線耗時
- `port_443_error`：443 端口錯誤訊息
- `ssl_certificate`：是否取得憑證 (`True`/`False`)
- `cert_expiry_date`：證書到期時間（ISO 格式，UTC）
- `days_until_expiry`：距離到期剩餘天數（小數，單位：天）
- `cert_issuer`：證書頒發者辨識名（DN）
- `cert_subject`：證書主體辨識名（DN）
- `cert_error`：憑證相關錯誤訊息
- `self_signed`：是否為自簽憑證 (`True`/`False`)
- `cert_trust_status`：OpenSSL 驗證結果（例如 `trusted`、`untrusted`）
- `cert_verify_code`：OpenSSL 驗證錯誤碼（整數）
- `cert_verify_error`：OpenSSL 驗證錯誤訊息

## 介面檢視：`dns_dashboard.html`

1. 在瀏覽器開啟 `dns_dashboard.html`（直接雙擊或透過開發伺服器皆可）。
2. 點擊右上角的 **載入 CSV**，選擇最新的檢測結果（例如 `india_game_pro_domains_dns_detailed_20250925_154258_accessibility_v3.csv`）。
3. 可使用頁面提供的篩選器與排序，快速查找 `自簽`、`憑證已過期`、`80/443` 逾時等情況。

## 常見疑問

- **為何 DNS 查不到仍顯示可連線？**
  `A` 記錄檢測使用 CSV 中保存的 IP，若登入時 DNS 存在、但稍後被刪除仍會成功連線。可搭配即時 DNS 查詢結果來判斷異常。

- **如何驗證結果？**
  建議使用 `head`/`tail` 或匯入 Excel/Sheets 快速檢視關鍵欄位。亦可搭配 `openssl s_client -connect host:443 -servername domain` 手動比對憑證資訊。

- **Dashboard 為何顯示自簽結果與預期不同？**
  確認已載入最新輸出的 CSV 檔案。`dns_dashboard.html` 會直接採用 CSV 中的 `self_signed` 與 `cert_verify_code` 欄位；若仍有疑慮，可檢查原始 CSV 或手動使用 `openssl` 驗證。

- **超時應設多少？**
  若目標伺服器位於海外或存在延遲，可將 `--timeout` 調整至 `8`~`12` 秒，以降低「timed out」誤判。

## 後續延伸

- 新增即時 DNS 解析（例如藉由 `socket.getaddrinfo()`）以標註現況是否存在 DNS 記錄。
- 導出報表或整合到監控系統，定期追蹤憑證到期狀態。
- 視需要加入代理設定或自訂請求標頭（目前工具僅用裸 TCP）。