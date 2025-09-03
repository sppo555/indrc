# 在 GCE VM 安裝 Grafana Alloy 並收集 NGINX JSON 日誌送到 Loki

本文件提供在 GCE VM 上安裝 Grafana Alloy 的步驟，並以 River 設定：
- 從 NGINX JSON 檔案讀取日誌
- 解析 JSON 欄位並轉成 labels
- 將資料送往 Kubernetes 叢集中的 Loki（或任何可存取的 Loki endpoint）

---

## 前置需求
- 一台 GCE VM（Debian/Ubuntu 範例）
- 具有對 Loki endpoint 的網路存取（建議透過 Ingress/LoadBalancer 對外提供，如 `https://loki.example.com`）
- NGINX 以 JSON 格式輸出到固定檔案（支援 glob），例如 `/opt/logs/nginx/*.log`

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
  --nginx-log /opt/logs/nginx/*.log

# 方式二：以環境變數傳入
sudo LOKI_URL=https://<你的Loki外部URL或LB> \
     NGINX_JSON_LOG=/opt/logs/nginx/*.log \
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
ALLOY_VERSION=1.4.1
curl -L -o /usr/local/bin/alloy \
  https://github.com/grafana/alloy/releases/download/v${ALLOY_VERSION}/alloy-linux-amd64
sudo chmod +x /usr/local/bin/alloy

# 3) 建立使用者與權限
sudo useradd --system --no-create-home --shell /usr/sbin/nologin alloy || true
sudo chown -R alloy:alloy /etc/alloy /var/lib/alloy

# 4) 設定環境變數（/etc/default/alloy）
sudo bash -c 'cat >/etc/default/alloy <<EOF
LOKI_URL=https://loki.example.com # 修改為你的 Loki 對外 URL
NGINX_JSON_LOG=/opt/logs/nginx/*.log # 可用 glob
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
  - 以 `loki.source.file` 讀取 NGINX JSON 檔案
  - `loki.process` 解析 JSON，將 `status`、`http_host`、`request_url_path` 等轉為 labels
  - `loki.write` 將日誌推送至 `LOKI_URL`

如需自訂欄位或檔案路徑，請調整 `config.river` 中的 `expressions` 與 `targets`。

---

## systemd 服務說明
- 單元檔：`/etc/systemd/system/alloy.service`
- 來源：`alloy/systemd/alloy.service`
- 可透過 `/etc/default/alloy` 設定 `LOKI_URL` 與 `NGINX_JSON_LOG`

常用指令：
```bash
sudo systemctl daemon-reload
sudo systemctl restart alloy
sudo systemctl status alloy --no-pager
journalctl -u alloy -f -n 200
```

---

## 常見調整
- 僅送出 4xx/5xx：在 `loki.process` 加上 `stage.match` 或在 Grafana Alerting 端篩選
- 加上主機與服務標籤：在 `loki.process` 的 `stage.labels` 指定固定值，例如 `{ instance = env.HOSTNAME, service="nginx" }`
- TLS/驗證：若 Loki 需要 basic auth 或 token，可在 `loki.write` 的 `endpoint` 區塊設定 `basic_auth` 或 `bearer_token`

---

## 檔案清單
- `config/alloy-config.river`：Alloy River 設定，讀取 NGINX JSON 檔案並送往 Loki
- `systemd/alloy.service`：systemd 服務單元檔


