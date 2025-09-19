# GCP 服務帳戶 JSON 生成器

這個工具用於將 GCP 服務帳戶參數和 PEM 私鑰文件轉換成標準的 GCP 服務帳戶 JSON 格式文件。

## 檔案結構

```
Json/
├── README.md                     # 本說明文件
├── gcp.json                     # 模板文件
├── generate_gcp_json.sh         # 生成腳本
├── service-account.json         # 生成的服務帳戶文件
└── gcp_generated.json          # 生成的原始文件
```

## 功能說明

- 自動讀取 PEM 格式私鑰文件
- 將私鑰轉換為 JSON 格式（替換換行符為 `\n`）
- 使用模板文件生成完整的 GCP 服務帳戶 JSON
- 自動驗證生成的 JSON 文件

## 使用方法

### 1. 生成 PEM 金鑰對

使用 OpenSSL 生成私鑰和公鑰：

```bash
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout ./private_key.pem \
    -out ./public_key.pem \
    -subj "/CN=unused"
```

這會生成兩個文件：
- `private_key.pem` - 私鑰（用於 JSON 生成）
- `public_key.pem` - 公鑰（需上傳到 GCP）

### 2. 上傳公鑰到 GCP 並取得 PRIVATE_KEY_ID

1. **登入 GCP Console**
2. **導航到 IAM & Admin > Service Accounts**
3. **選擇或創建服務帳戶**
4. **進入 "Keys" 標籤**
5. **點擊 "ADD KEY" > "Upload existing key"**
6. **上傳剛生成的 `public_key.pem` 文件**
7. **上傳成功後，GCP 會顯示 `PRIVATE_KEY_ID`**
8. **複製此 ID 用於腳本設定**

⚠️ **重要**：`PRIVATE_KEY_ID` 必須先將 `public_key.pem` 上傳到 GCP 服務帳戶後才能取得，這是 GCP 自動生成的唯一識別碼。

### 3. 準備文件

確保以下文件存在：
- 模板文件：`gcp.json`
- PEM 私鑰文件：`/tmp/pem/private_key.pem`（或調整腳本中的路徑）

### 4. 設定參數

在 `generate_gcp_json.sh` 中修改以下參數：

```bash
export SERVICE_ACCOUNT="grafana-viewer"
export PRIVATE_KEY_ID="96faf0e23be802cd35a38c2fea91edf6c59ece55"  # 從 GCP Console 取得
export CLIENT_ID="112337270083219293609"                        # 服務帳戶的 Client ID
export PEM_FILE="/tmp/pem/private_key.pem"                     # 私鑰文件路徑
export PROJECT_ID="incdr-infra"                                # GCP 專案 ID
```

### 5. 執行腳本

```bash
# 賦予執行權限
chmod +x generate_gcp_json.sh

# 執行生成腳本
./generate_gcp_json.sh
```

### 6. 驗證結果

使用 gcloud 驗證生成的服務帳戶文件：

```bash
gcloud auth activate-service-account \
    grafana-viewer@incdr-infra.iam.gserviceaccount.com \
    --key-file=service-account.json
```

## 模板格式

`gcp.json` 模板文件使用以下變數：

- `$project_id` - GCP 專案 ID
- `${private_key_id}` - 私鑰 ID
- `${Your_Private_Key}` - PEM 私鑰內容
- `${service_account}` - 服務帳戶名稱
- `__Your_Service_Account_ID__` - 客戶端 ID

## 輸出文件

腳本會生成兩個文件：
- `gcp_generated.json` - 原始生成文件
- `service-account.json` - 可直接使用的服務帳戶文件

## 依賴要求

- **Python 3** - 用於處理 JSON 格式轉換
- **gcloud CLI** - 用於驗證服務帳戶（可選）
- **Bash** - 執行腳本環境

## 注意事項

1. **安全性**：生成的 JSON 文件包含敏感信息，請妥善保管
2. **權限**：確保 PEM 文件路徑正確且可讀取
3. **格式**：私鑰會自動轉換為 JSON 所需的轉義格式

## 故障排除

### 常見錯誤

1. **找不到 PEM 文件**
   ```
   錯誤：找不到私鑰文件 /tmp/pem/private_key.pem
   ```
   解決方案：檢查 PEM 文件路徑是否正確

2. **gcloud 驗證失敗**
   - 檢查服務帳戶是否存在
   - 確認項目 ID 正確
   - 驗證私鑰 ID 和客戶端 ID

3. **權限錯誤**
   ```bash
   chmod +x generate_gcp_json.sh
   ```

## 更新歷史

- **v1.0** - 初始版本，支援基本的 JSON 生成和驗證功能

---

**作者**: Alex  
**創建日期**: 2025-09-17  
**最後更新**: 2025-09-17
