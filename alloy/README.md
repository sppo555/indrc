# 在 GCE VM 安裝 Grafana Alloy 並收集 NGINX 日誌送到 Loki

本文件提供在 GCE VM 上安裝 Grafana Alloy 的步驟，並以 River 設定：
- 同時處理 NGINX JSON 格式的 access log 和純文本格式的 error log
- 解析 JSON 欄位和 error log 文本，轉換為結構化 labels
- 將資料送往 Kubernetes 叢集中的 Loki（或任何可存取的 Loki endpoint）

---

## 前置需求
- 一台 GCE VM（支援 Ubuntu 22/24 LTS、CentOS 7、Debian 12/13、CentOS-stream-10）
- 具有對 Loki endpoint 的網路存取（建議透過 Ingress/LoadBalancer 對外提供，如 `https://loki.example.com`）
- NGINX 配置：
  - Access log 以 JSON 格式輸出到固定檔案，例如 `/opt/logs/nginx/access.log`
  - Error log 以標準格式輸出，例如 `/opt/logs/nginx/error.log`

> 提醒：若你的 Loki 僅在叢集內部（ClusterIP `http://loki.loki.svc:3100`），GCE VM 可能無法直接存取。請：
> - 以 Ingress/LoadBalancer 對外暴露 Loki，設定 `LOKI_URL` 為外部可達 URL；或
> - 打通 VPC / 專線 / VPN 讓 VM 可直接存取內部服務。

---

## 一鍵安裝（推薦）
在 GCE VM 上執行以下腳本（需 root/sudo）：

```bash
# 將腳本放到 VM 後
chmod +x ./install_alloy.sh

# 方式一：以旗標傳入參數
sudo ./install_alloy.sh \
  --loki-url https://<你的Loki外部URL或LB> \
  --access-log "/opt/logs/nginx/*access*.log" \
  --error-log "/opt/logs/nginx/*error*.log"

# 方式二：以環境變數傳入
sudo LOKI_URL=https://<你的Loki外部URL或LB> \
     NGINX_ACCESS_LOG="/opt/logs/nginx/*access*.log" \
     NGINX_ERROR_LOG="/opt/logs/nginx/*error*.log" \
     ./install_alloy.sh

# 驗證服務
systemctl status alloy --no-pager
journalctl -u alloy -f -n 200
```

腳本位置：`alloy/scripts/install_alloy.sh`

---

## 進階安裝（手動，可選）
在 GCE VM 上執行：

```bash
# 1) 準備目錄
sudo mkdir -p /etc/alloy /var/lib/alloy

# 2) 下載 Alloy 二進位檔（Linux x86_64）
ALLOY_VERSION=1.10.2
# 下載 ZIP 格式的檔案並解壓
TEMP_DIR=$(mktemp -d)
curl -L -o "${TEMP_DIR}/alloy.zip" \
  https://github.com/grafana/alloy/releases/download/v${ALLOY_VERSION}/alloy-linux-amd64.zip
unzip -q "${TEMP_DIR}/alloy.zip" -d "${TEMP_DIR}"
# 解壓後的檔案名稱為 alloy-linux-amd64
mv "${TEMP_DIR}/alloy-linux-amd64" /usr/local/bin/alloy
sudo chmod +x /usr/local/bin/alloy

# 3) 建立使用者與權限
sudo useradd --system --no-create-home --shell /usr/sbin/nologin alloy || true
sudo chown -R alloy:alloy /etc/alloy /var/lib/alloy

# 4) 設定環境變數（/etc/default/alloy）
sudo bash -c 'cat >/etc/default/alloy <<EOF
LOKI_URL=https://loki.example.com # 修改為你的 Loki 對外 URL
NGINX_ACCESS_LOG=/opt/logs/nginx/*access*.log # access log 路徑，可用 glob
NGINX_ERROR_LOG=/opt/logs/nginx/*error*.log # error log 路徑，可用 glob
EOF'

# 5) 放置設定與服務檔
# 將本專案檔案拷貝到相對位置：
# - alloy/config/alloy-config.river -> /etc/alloy/config.river
# - alloy/systemd/alloy.service     -> /etc/systemd/system/alloy.service

# 6) 啟用並啟動服務
sudo systemctl daemon-reload
sudo systemctl enable alloy
sudo systemctl start alloy

# 查看狀態
sudo systemctl status alloy --no-pager
journalctl -u alloy -f -n 200
```

---

## 設定檔說明
- 路徑：`/etc/alloy/config.river`
- 來源：`alloy/config/alloy-config.river`
- 功能：
  - 分別以 `loki.source.file` 讀取 NGINX JSON access log 和 text error log
  - 提供兩個獨立的處理器：
    - `loki.process "nginx_json"`: 解析 JSON 格式的 access log，將 `status`、`http_host`、`request_time` 等轉為 labels
    - `loki.process "nginx_error"`: 僅提取時間戳來排序，保留完整錯誤訊息不進行额外解析
  - `loki.write` 將兩類日誌推送至 `LOKI_URL`

如需自訂欄位或檔案路徑，請調整 `config.river` 中的 `expressions` 與 `targets`。

---

## systemd 服務說明
- 單元檔：`/etc/systemd/system/alloy.service`
- 來源：`alloy/systemd/alloy.service`
- 可透過 `/etc/default/alloy` 設定 `LOKI_URL`、`NGINX_ACCESS_LOG` 與 `NGINX_ERROR_LOG`

常用指令：
```bash
sudo systemctl daemon-reload
sudo systemctl restart alloy
sudo systemctl status alloy --no-pager
journalctl -u alloy -f -n 200
```

---

## 常見調整
- 僅送出 4xx/5xx：在 `loki.process "nginx_json"` 加上 `stage.match` 或在 Grafana Alerting 端篩選
- 錯誤日誌篩選：在 Grafana 查詢中使用 LogQL 全文搜索篩選，如 `{source="nginx_error"} |= "[error]"`
- 加上主機與服務標籤：已預設在 `stage.static_labels` 加上 `host` 和 `source` 標籤
- TLS/驗證：若 Loki 需要 basic auth 或 token，可在 `loki.write` 的 `endpoint` 區塊設定 `basic_auth` 或 `bearer_token`

---

## 檔案清單
- `config/alloy-config.river`：Alloy River 設定，讀取 NGINX JSON 檔案並送往 Loki
- `systemd/alloy.service`：systemd 服務單元檔


