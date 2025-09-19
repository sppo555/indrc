# generate-nginx-from-template.sh 使用說明

這份 README 說明如何使用 `generate-nginx-from-template.sh` 腳本，根據一份 Nginx 模板與站點清單，自動產出多個對應的 `nginx.conf` 檔案。

## 功能概述
- 將模板中的網域佔位符 `__DOMAIN_PLACEHOLDER__` 替換為清單中的域名（如 `www.example.com`）。
- 將模板中的發佈目錄佔位符 `__PROD_PLACEHOLDER__` 替換為清單中的數字（例如 `101`），配合模板中的 `prod__PROD_PLACEHOLDER__` 形成 `prod101`、`prod102`。
- 每個輸出檔案以「域名」命名，例如：`www.example.com.conf`。

## 前置條件
- macOS 或 Linux 環境，已安裝 `bash`、`sed`、`awk`、`grep`。
- 你的模板檔需包含以下佔位符：
  - 網域：`__DOMAIN_PLACEHOLDER__`
  - 目錄：`/data/h5-download/prod__PROD_PLACEHOLDER__`

範例模板（`indrc/nginx-template.conf`）：
```
server {
    listen                         80;
    listen  443 ssl;
    server_name                    __DOMAIN_PLACEHOLDER__;
    charset utf-8;
    access_log                     /opt/logs/nginx/__DOMAIN_PLACEHOLDER__.access.log;
    error_log                      /opt/logs/nginx/__DOMAIN_PLACEHOLDER__.error.log;

    ssl_certificate /opt/ssl/__DOMAIN_PLACEHOLDER__.pem;
    ssl_certificate_key /opt/ssl/__DOMAIN_PLACEHOLDER__.key;
    ssl_session_cache shared:SSL:1m;
    ssl_session_timeout  10m;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        root                       /data/h5-download/prod__PROD_PLACEHOLDER__;
        try_files                  $uri $uri/ /index.html;
    }
}
```

## 站點清單格式（TXT）
- 每行兩個欄位，使用空白或 Tab 分隔：
  - 第 1 欄：數字（例如 `101`、`102`）
  - 第 2 欄：域名（例如 `www.example.com`）

範例（`sites.txt`）：
```
101 www.fd5tytrf.com
102 www.uyhk76t.com
131 www.4redgfcv.com
152 www.hfgyrew4.com
```

## 腳本位置
- `/root/create_nginx_conf/generate-nginx-from-template.sh`

若無執行權限，先賦予：
```bash
chmod +x /root/create_nginx_conf/generate-nginx-from-template.sh
```

## 使用方式一：直接帶三個參數
```bash
/root/create_nginx_conf/generate-nginx-from-template.sh \
  /root/create_nginx_conf/nginx-template.conf \
  /root/create_nginx_conf/sites.txt \
  /root/create_nginx_conf/nginx-conf-output
```
- 第 1 參數：模板檔路徑
- 第 2 參數：站點清單路徑
- 第 3 參數：輸出資料夾路徑（不存在會自動建立）

範例：
```bash
sh /root/create_nginx_conf/generate-nginx-from-template.sh \
  /root/create_nginx_conf/nginx-template.conf \
  /root/create_nginx_conf/sites.txt \
  /root/create_nginx_conf/nginx-conf-output
```

## 使用方式二：互動式輸入（不帶參數）
```bash
/root/create_nginx_conf/generate-nginx-from-template.sh
```
執行後依提示輸入：
- Template file path:
- Sites list file path:
- Output directory:

## 輸出結果
- 產出在指定的輸出資料夾，每個檔名為域名，例如：
  - `/root/create_nginx_conf/nginx-conf-output/www.fd5tytrf.com.conf`
  - `/root/create_nginx_conf/nginx-conf-output/www.uyhk76t.com.conf`
- 內容中：
  - `__DOMAIN_PLACEHOLDER__` 會被換成該行域名
  - `__PROD_PLACEHOLDER__` 會被換成該行數字，並與模板中的 `prod__PROD_PLACEHOLDER__` 組合成 `prod<number>`

## 錯誤處理與注意事項
- 腳本會檢查模板檔與清單檔是否存在；不存在會回報錯誤。
- 清單檔支援空行與以 `#` 起頭的註解行（會被忽略）。
- 若某行缺欄位、或第 1 欄不是純數字，該行會被略過並在終端輸出警告。
- macOS 的 `sed -i` 與 GNU `sed -i` 行為不同；本腳本以建立暫存檔再輸出方式避免相容性問題。

## 範例驗證
產出後可檢查：
```bash
ls -1 /root/create_nginx_conf/nginx-conf-output
sed -n '1,60p' /root/create_nginx_conf/nginx-conf-output/www.fd5tytrf.com.conf
```
確認 `server_name`、log 路徑、憑證名與 `root /data/h5-download/prod<number>` 是否正確。

---