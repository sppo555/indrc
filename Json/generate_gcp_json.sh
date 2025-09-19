#!/bin/bash

# GCP Service Account JSON Generator
# 此腳本會讀取模板文件並生成新的GCP服務帳戶JSON文件

# 設定變數
# export SERVICE_ACCOUNT=""
# export PRIVATE_KEY_ID=""
# export CLIENT_ID=""
# export PROJECT_ID=""
# export PEM_FILE="./private_key.pem"
# export TEMPLATE_FILE="./GCP_TEMPLATE_FILE.json"
# 模板文件和輸出文件路徑
OUTPUT_FILE="./${SERVICE_ACCOUNT}_${PRIVATE_KEY_ID}.json"

echo "開始生成GCP服務帳戶JSON文件..."

# 檢查模板文件是否存在
if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "錯誤：找不到模板文件 $TEMPLATE_FILE"
    exit 1
fi

# 檢查PEM文件是否存在
if [ ! -f "$PEM_FILE" ]; then
    echo "錯誤：找不到私鑰文件 $PEM_FILE"
    exit 1
fi

# 讀取並轉換私鑰格式（將換行符替換為\n）
echo "正在處理私鑰文件..."
PRIVATE_KEY=$(cat "$PEM_FILE" | tr '\n' '\n' | sed 's/$/\\n/' | tr -d '\n' | sed 's/\\n$//')

# 創建臨時文件來處理替換
cp "$TEMPLATE_FILE" "$OUTPUT_FILE"

# 使用sed進行變數替換
echo "正在替換模板變數..."
sed -i '' "s/\\\$project_id/$PROJECT_ID/g" "$OUTPUT_FILE"
sed -i '' "s/\${private_key_id}/$PRIVATE_KEY_ID/g" "$OUTPUT_FILE"
sed -i '' "s/\${service_account}/$SERVICE_ACCOUNT/g" "$OUTPUT_FILE"
sed -i '' "s/__Your_Service_Account_ID__/$CLIENT_ID/g" "$OUTPUT_FILE"

# 替換私鑰（需要特殊處理因為包含特殊字符）
python3 -c "
import json
import sys

# 讀取文件
with open('$OUTPUT_FILE', 'r') as f:
    content = f.read()

# 讀取私鑰並轉換格式
with open('$PEM_FILE', 'r') as f:
    pem_content = f.read().strip()

# 將私鑰轉換為JSON格式（替換換行符為\\n）
pem_json = pem_content.replace('\n', '\\\\n')

# 替換私鑰
content = content.replace('\${Your_Private_Key}', pem_json)
content = content.replace('\${project_id}', '$PROJECT_ID')

# 寫回文件
with open('$OUTPUT_FILE', 'w') as f:
    f.write(content)
"

echo "JSON文件已生成: $OUTPUT_FILE"
echo ""
echo "生成的文件內容："
echo "=================="
cat "$OUTPUT_FILE"
echo ""
echo "=================="
echo "完成！"
echo ""
echo "驗證指令："
echo "gcloud auth activate-service-account ${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com --key-file=${OUTPUT_FILE}"
