# 在 Kubernetes（GKE）上安裝 Loki（單 Pod）與 Grafana Alerting，使用 GCS 儲存與 Workload Identity

本指南將完成以下項目：
- 以 Helm 安裝 Loki 單一執行檔（single-binary、單 Pod）模式，並使用 GCS 作為儲存，透過 GKE Workload Identity 存取。
- 以 Helm 安裝 Grafana，預先佈署 Loki DataSource。
- 佈署 Grafana Alerting：Telegram Contact Point 與 Notification Policy。
- 佈署 Loki 查詢的示範 Alert 規則，偵測 Nginx 非 200 狀態碼並送警告至 Telegram。
- 與 Alloy 日誌收集器整合，收集 Nginx JSON 格式日誌。

注意：此專案與 Alloy 日誌收集器配合使用，收集 Nginx JSON 格式的日誌並發送至 Loki。

授權說明：本部署使用 GCP Identity（GKE Workload Identity）進行授權，Grafana/Loki 無需保存任何服務金鑰檔。

---

## 先決條件（Prerequisites）
- 已啟用 Workload Identity 的 GKE 叢集
- 已設定好的 gcloud 與 kubectl（可操作該叢集）
- Helm v3+
- 一個 GCS Bucket（例如 `gs://YOUR_LOKI_BUCKET`）
- 具備存取該 Bucket 權限的 GCP Service Account（GSA），測試可給 Storage Object Admin
- 需要兩個 Kubernetes 命名空間：`loki`（部署 Loki）、`monitoring`（部署 Grafana）
- Telegram 機器人 Token 與目標 Chat ID

---

## 1) 準備 GCP 資源

使用下列實際參數（GSA 已存在）：

```bash
PROJECT_ID=incdr-infra
GSA_EMAIL=loki-gcs@incdr-infra.iam.gserviceaccount.com
GCS_BUCKET=loki-storage-log

# 授予 Bucket 存取權（正式環境請最小化權限）
gsutil iam ch serviceAccount:${GSA_EMAIL}:objectAdmin gs://${GCS_BUCKET}
# 視需要可額外授與更高權限（請審慎評估）
# gsutil iam ch serviceAccount:${GSA_EMAIL}:admin gs://${GCS_BUCKET}
```

---

## 2) 建立命名空間
```bash
kubectl create namespace loki || true
kubectl create namespace monitoring || true
```

---

## 3) 安裝 Loki（單 Pod）並設定 GCS + Workload Identity

請先更新 `helm-values/loki-values.yaml` 中的 GCS Bucket 與 GSA Email：
- GCS Bucket：`loki-storage-log`
- GSA Email：`loki-gcs@incdr-infra.iam.gserviceaccount.com`

加入 Grafana 的 Helm repo 並安裝 Loki：
```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# 使用修正後的配置安裝 Loki - 不再需要 --set loki.useTestSchema=true 參數
helm upgrade --install loki grafana/loki \
  --namespace loki \
  -f helm-values/loki-values.yaml

# 舊版本說明：若使用未經修正的配置檔，則需要加上 --set loki.useTestSchema=true 參數
# helm upgrade --install loki grafana/loki \
#   --namespace loki \
#   -f helm-values/loki-values.yaml \
#   --set loki.useTestSchema=true
```

設定 Workload Identity 綁定（將 KSA 對應至 GSA）。請確保 KSA 名稱與 `loki-values.yaml` 中 `serviceAccount.name` 一致（預設 `loki`）：
```bash
KSA_NAME=loki
GSA_EMAIL=loki-gcs@incdr-infra.iam.gserviceaccount.com
PROJECT_ID=incdr-infra

kubectl annotate serviceaccount ${KSA_NAME} \
  --namespace loki \
  iam.gke.io/gcp-service-account=${GSA_EMAIL} --overwrite

# 允許該 KSA 冒用（impersonate）GSA
gcloud iam service-accounts add-iam-policy-binding \
  ${GSA_EMAIL} \
  --project ${PROJECT_ID} \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT_ID}.svc.id.goog[loki/${KSA_NAME}]"
```

驗證 Loki 是否啟動：
```bash
kubectl -n loki get pods -l app.kubernetes.io/name=loki
```

---

## 4) 安裝 Grafana 並佈署 DataSource 與 Alerting 設定

請檢查並視需要更新 `helm-values/grafana-values.yaml`：
- Loki URL（跨命名空間請使用 `http://loki.loki.svc:3100`）
- 管理者密碼（預設 `admin123`，正式環境請修改）

建立 Telegram Secret（請替換為你的值）：
```bash
kubectl -n monitoring apply -f k8s/secrets/telegram-secret.yaml
```

先佈署 Contact Points 與 Notification Policies 的 ConfigMap（Grafana 會以 volume 方式掛載）：
```bash
kubectl -n monitoring apply -f k8s/provisioning/grafana-alerting-contactpoints.yaml
```

安裝 Grafana：
```bash
helm upgrade --install grafana grafana/grafana \
  --namespace monitoring \
  -f helm-values/grafana-values.yaml
```

等待 Grafana 部署完成：
```bash
kubectl -n monitoring rollout status deploy/grafana
```

取得 Grafana 管理者密碼：
```bash
kubectl -n monitoring get secret grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

本機連線（Port-forward）：
```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
```
瀏覽 http://localhost:3000

---

## 5) 套用 Loki 的示範 Grafana Alert 規則

此專案使用 Grafana Helm Chart 內建的 sidecar，從帶有 `grafana_alert="1"` 標籤的 ConfigMap 載入 Alert 規則。
若你變更了 DataSource UID，請同步更新 `k8s/provisioning/grafana-alert-rule-loki.yaml` 中的 `datasourceUid`（預設為 `loki`）。

```bash
kubectl -n monitoring apply -f k8s/provisioning/grafana-alert-rule-loki.yaml
```

這會建立一條規則，當偵測到 Nginx 日誌中在 5 分鐘內出現超過 10 次非 200 狀態碼的請求時觸發警報，並透過先前佈署的 Telegram Contact Point 與 Notification Policy 發送告警。

### Nginx JSON 日誌格式

本配置預期處理的 Nginx JSON 日誌格式如下：

```json
{
  "access_time": "2025-08-29T12:30:59+05:30",
  "remote_addr": "35.191.16.240", 
  "x_forward_for": "35.240.213.159,34.160.149.251", 
  "method": "POST", 
  "request_url_path": "/game/vndr/cb/jili/bet", 
  "request_url": "/game/vndr/cb/jili/bet?id=1890144949453160300", 
  "status": 500, 
  "request_time": 0.001, 
  "request_length": "592", 
  "upstream_host": "-", 
  "upstream_response_length": "186", 
  "upstream_response_time": "0.001", 
  "upstream_status": "500", 
  "http_referer": "-", 
  "remote_user": "-", 
  "http_user_agent": "Go-http-client/2.0", 
  "appkey": "-", 
  "upstream_addr": "10.71.11.13:8080", 
  "http_host": "in01prod.cc", 
  "pro": "http", 
  "request_id": "4e4bed3d4c8533cb0cf002f5473a85cf", 
  "bytes_sent": 353
}
```

警報規則會監控並偵測不等於 200 的 `status` 欄位，在 5 分鐘內發現超過 10 次時才觸發警報，避免因偶發性錯誤造成過多告警通知。

---

## 專案檔案說明

### Helm Values 檔案
- `helm-values/loki-values.yaml` — Loki（single-binary 單 Pod）+ GCS + Workload Identity + 內部 Ingress 的 Helm values
  - 重要配置項目：
    - `deploymentMode: SingleBinary` — 使用單 Pod 模式部署
    - **明確將其他部署模式設置為 0**，避免 Helm Chart 驗證問題：
      ```yaml
      read:
        replicas: 0
      write:
        replicas: 0
      backend:
        replicas: 0
      gateway:
        enabled: false
      ```
    - `serviceAccount` — 設定 GCP Workload Identity 認證
    - `loki.storage` — 設定 GCS 儲存類型和 bucket 名稱
    - `loki.schemaConfig` — 設定資料結構，使用 TSDB 存儲引擎，更適合高效率存取：
      ```yaml
      schemaConfig:
        configs:
          - from: "2024-01-01"
            store: tsdb
            object_store: gcs
            schema: v13
            index:
              prefix: loki_index_
              period: 24h
      ```
    - `loki.structuredConfig` — 完整的 Loki 執行配置，包含壓縮器、限制和儲存等詳細設定
    - `ingress` — GKE 內部 Ingress 設定：
      ```yaml
      ingress:
        enabled: true
        ingressClassName: "gce-internal"  # 使用 GKE 內部負載平衡器
        annotations:
          networking.gke.io/allowed-backend-cidrs: "10.71.11.0/24,10.11.11.0/24"  # IP 白名單
          networking.gke.io/v1beta1.FrontendConfig: "loki-frontend-config"
      ```
    - `frontendConfig` — GKE 特有的負載平衡器前端配置：
      ```yaml
      frontendConfig:
        enabled: true
        name: loki-frontend-config
        spec:
          redirectToHttps:
            enabled: false  # 允許 HTTP 訪問，不強制重定向至 HTTPS
      ```
- `helm-values/grafana-values.yaml` — Grafana 的 Helm values，預先佈署 Loki DataSource，並啟用 Alert sidecar 與掛載 provisioning

### Kubernetes 檔案
- `k8s/secrets/telegram-secret.yaml` — 放置 Telegram Bot Token 與 Chat ID 的 Secret（以環境變數方式提供給 Grafana）
- `k8s/provisioning/grafana-alerting-contactpoints.yaml` — 佈署 Telegram Contact Point 與 Notification Policy 的 ConfigMap（由 Grafana 掛載使用）
- `k8s/provisioning/grafana-alert-rule-loki.yaml` — Loki Alert 規則示例（由 Grafana sidecar 掛載使用）

---

## 6) 設定 GKE Workload Identity

Workload Identity 是 GKE 上推薦的授權方式，讓 Kubernetes 服務帳號（KSA）能使用 Google Cloud 服務帳號（GSA）的權限，而無需下載金鑰檔案。

### Node Pool 啟用 Workload Identity

首先，確保 Node Pool 已啟用 Workload Identity：

```bash
gcloud container node-pools update ${NODE_POOL_NAME} \
  --cluster=${CLUSTER_NAME} \
  --location=${LOCATION} \
  --workload-metadata=GKE_METADATA \
  --project=${PROJECT_ID}
```

在本專案中，我們使用了以下參數：
- NODE_POOL_NAME: infra-pool
- CLUSTER_NAME: infra-gke
- LOCATION: asia-south1

### 服務帳號綁定步驟

1. 確保 KSA 有正確的 annotation（這已包含在 Helm 配置中）：
```yaml
serviceAccount:
  annotations:
    iam.gke.io/gcp-service-account: loki-gcs@incdr-infra.iam.gserviceaccount.com
```

2. 建立 IAM 綁定（已在前面的步驟完成）：
```bash
gcloud iam service-accounts add-iam-policy-binding \
  ${GSA_EMAIL} \
  --project ${PROJECT_ID} \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT_ID}.svc.id.goog[loki/${KSA_NAME}]"
```

### 權限驗證

部署 Loki 後，檢查 Pod 的日誌確認是否能成功訪問 GCS：
```bash
kubectl -n loki logs loki-0 -c loki | grep -i error
```

若未見到 GCS 相關錯誤，表示 Workload Identity 設定成功。

---

## 7) 與 Alloy 日誌收集器的整合

本專案搭配 Alloy 日誌收集器，用於收集 Nginx JSON 格式的日誌。Alloy 配置應確保可以將收集到的日誌結構化並發送至 Loki。

主要整合步驟：

1. Alloy 配置與 Nginx 連接，收集 JSON 格式日誌
2. Alloy 與 Loki 整合，確保合適的標籤和元數據映射
3. 日誌結構的正確解析與儲存

請參考 `/alloy` 目錄下的相關配置與文件，了解 Alloy 的詳細設定。

---

## 8) Loki 配置重點說明

### 為何 `schema_config` 必須位於正確的層級

Grafana Loki Helm Chart 與其他 Helm Chart 不同，它要求某些配置項目必須位於特定層級：

- `loki.schemaConfig` — 必須在這個層級定義，而非在 `loki.config` 或 `loki.structuredConfig` 內部
- `loki.storage` — 存儲類型與 GCS bucket 需要在這個層級定義，為 Helm Chart 驗證所需

裡面的細節配置可以放在 `loki.structuredConfig` 中，但主要部分必須在高層級。這樣才能避免使用 `--set loki.useTestSchema=true` 參數。

### 重要的部署模式設置

Loki Helm Chart 的驗證機制非常嚴格，在設置 `deploymentMode: SingleBinary` 時，必須確保不存在其他部署模式的副本數配置，即使是設置為 0。因此我們需要明確禁用所有其他部件：

```yaml
read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0
gateway:
  enabled: false
```

### 使用 TSDB 存儲引擎

最新的 Loki 配置使用 TSDB 存儲引擎而不是舊的 boltdb-shipper，提供更好的效能與擴展性：

```yaml
schemaConfig:
  configs:
    - from: "2024-01-01"
      store: tsdb
      object_store: gcs
      schema: v13
```

TSDB 存儲引擎相對於 boltdb-shipper 提供：
- 更快的查詢效能
- 更高的壓縮率
- 更好的擴展性
- 更穩定的大規模部署表現

### 單 Pod vs 分散式模式

本配置使用單 Pod 模式 (`deploymentMode: SingleBinary`)，適合以下場景：

- 小到中型的日誌量
- 測試或 POC 環境
- 管理簡單化優先

若日誌量增長與需求變更，建議轉為分散式模式，可讓各元件獨立擴展：

```yaml
deploymentMode: Distributed

# 啟用每個元件，與適當的副本數
gateway:
  enabled: true
write:
  replicas: 2
read:
  replicas: 2
backend:
  replicas: 2
```

## 9) 使用 GKE 內部 LoadBalancer 訪問 Loki

為了讓 GKE 集群內的其他服務或同一 VPC 網路中的資源能夠訪問 Loki，我們配置了 GKE 內部 LoadBalancer，提供內部 IP 地址而非公開暴露服務。

### 部署內部 LoadBalancer

使用 `type: LoadBalancer` 和相關註釋配置內部 LoadBalancer 更加直接且易於管理：

```bash
# 安裝或更新 Loki
helm upgrade --install loki grafana/loki \
  --namespace loki \
  -f helm-values/loki-values.yaml
```

`loki-values.yaml` 中的 LoadBalancer 設定已包含：
- 服務類型設置為 LoadBalancer: `type: LoadBalancer`
- 指定為內部負載平衡器: `cloud.google.com/load-balancer-type: "Internal"`
- 暴露標準 Loki HTTP 埠口: 3100

>注意：內部 LoadBalancer 比 Ingress 更簡單，無需額外配置 FrontendConfig 和路徑設定。它直接暴露服務到自動分配的內部 IP 上。

### 取得內部 IP 地址

套用後，等待 GKE 佈給內部負載平衡器（通常需要 1-3 分鐘）：

```bash
kubectl -n loki get service loki
```

輸出將顯示分配的內部 IP 地址：

```
NAME   TYPE           CLUSTER-IP     EXTERNAL-IP     PORT(S)                         AGE
loki   LoadBalancer   10.11.87.94    10.11.11.215    3100:31778/TCP,9095:31536/TCP   83m
```

### 設定固定內部 IP 地址

若需要使用固定的內部 IP 地址，需要先在 GCP 中預留該 IP：

```bash
# 1. 先在 GCP 中預留静態內部 IP
gcloud compute addresses create loki-internal-ip \
  --region=REGION \
  --subnet=SUBNET_NAME \
  --addresses=<預定使用的IP地址>
```

然後在 `loki-values.yaml` 中添加相應註釋：

```yaml
singleBinary:
  service:
    annotations:
      cloud.google.com/load-balancer-type: "Internal"
      cloud.google.com/address-name: "loki-internal-ip"  # 預留的內部 IP 名稱
```

### 使用內部 IP 存取 Loki

現在可以通過以下 URL 從 VPC 內部存取 Loki：

```
http://10.11.11.215:3100
```

若需要從集群內的 Pod 訪問，也可以繼續使用 Kubernetes 服務名稱：

```
http://loki.loki.svc:3100
```

### Alloy 配置指南

如需配置 Alloy 使用內部 Ingress 的 Loki 端點，請在 Alloy 配置中指定：

```river
loki.write "loki" {
  endpoint = "http://INTERNAL_IP"
  // ...
}
```

## 10) 疑難排解

### 常見失敗模式與解決方式

1. **部署模式配置衝突**
   - 錯誤訊息：`You have more than zero replicas configured for both the single binary and simple scalable targets`
   - 原因：同時存在多種部署模式的配置，即使副本數為 0
   - 解決方式：
     - 確保只設置一種部署模式（如 `deploymentMode: SingleBinary`）
     - 不要同時設定 `simpleScalable` 的 `replicas` 屬性
     - 使用上面所示的 `read/write/backend/gateway` 禁用其他部署元件

2. **Schema 配置錯誤**
   - 高層級 `loki.schemaConfig` 缺失或結構不正確
   - 錯誤訊息：`schema_config: no schema found before 2020-10-24`
   - 解決方式：確保 `loki.schemaConfig` 正確定義，或臨時使用 `--set loki.useTestSchema=true`

3. **GCS 存取問題**
   - Workload Identity 未正確設置
   - 錯誤訊息：`permission denied` 或 `storage: bucket doesn't exist` 或 `googleapi: Error 403: Provided scope(s) are not authorized, forbidden`
   - 解決方式：
     - 確認 GSA 與 KSA 的 binding 已建立
     - 確認 GSA 有適當的 GCS 權限
     - 確認 GCS bucket 存在且命名正確
     - 確認 Node Pool 已啟用 Workload Identity：`--workload-metadata=GKE_METADATA`
     - 檢查 IAM 綁定是否正確：`gcloud iam service-accounts get-iam-policy ${GSA_EMAIL}`

4. **TSDB 配置與 WAL 問題**
   - 錯誤訊息：`tsdb is not a supported store`
   - 解決方式：確保 Loki 版本支援 TSDB（需要 2.8.0+），從舊版的 boltdb-shipper 升級

5. **Grafana 警報規則未載入**
   - 錯誤：規則在 Grafana UI 不可見
   - 解決方式：
     - 確認 ConfigMap 有 `grafana_alert: "1"` 標籤
     - 確認 Grafana values 已啟用 `sidecar.alerts.enabled: true`
     - 檢查 Grafana Pod 的日誌是否有載入相關訊息

## 11) 備註
- 正式環境請強化 RBAC 與 GCS 權限的最小化設定。
- 若規模成長，建議評估 Loki 分散式（distributed）模式與更細緻的保留策略。
- 若使用 Alloy 以外的收集器，需要確保其可以正確處理 Nginx JSON 格式。
- 若 sidecar 沒有載入規則，請確認 `grafana_alert: "1"` 標籤與 `sidecar.alerts.enabled: true` 已在 Grafana values 啟用。
