# Loki 查詢與導出 CSV 使用說明

本文件說明如何用 LogQL 與 Loki HTTP API，產生與 Grafana 面板「完全一致語意」的查詢結果，並導出成 CSV。亦包含區間查詢（query_range）導出逐分鐘資料，以及將逐分鐘資料彙總為 45 分鐘總和的做法。

- 目錄位置：`indrc/loki/`
- 需要工具：`curl`、`jq`（建議安裝）、`python3`

---

## 1) 快速開始：產出與 Grafana 表格一致的 CSV（instant 查詢）

以你的面板為例：在「2025-09-21 21:30:59 CST」這一個時間點，回看 45 分鐘窗口計算以下 LogQL：

```
topk(85, sum by (remote_addr) (count_over_time({host="www.starstar123.com"} | json [45m])))
```

注意：Grafana 顯示為 CST（UTC+8），Loki API 預設使用 UTC，所以需將時間轉換為 `2025-09-21T13:30:59Z`。

- 變數設定
```bash
HOST="www.starstar123.com"
TOPK=85
WINDOW="45m"
TIME_CST="2025-09-21 21:30:59.000"         # 只用來顯示在 CSV
TIME_UTC="2025-09-21T13:30:59Z"            # 提供給 Loki API
LOKI_URL="http://34.100.235.214:3100"
OUT="loki_instant_top${TOPK}_remote_addr_20250921_213059_CST_sorted.csv"
```

- 直接以 `jq` 產生「已排序」且欄位與面板一致的 CSV（Time, remote_addr, Value #A）
```bash
curl -G -s "$LOKI_URL/loki/api/v1/query" \
  --data-urlencode "query=topk(${TOPK}, sum by (remote_addr) (count_over_time({host=\"$HOST\"} | json [$WINDOW])) )" \
  --data-urlencode "time=$TIME_UTC" \
| jq -r --arg t "$TIME_CST" '
  # 取出每筆 (IP, 值)，並以值做降序排序
  [.data.result[]? | {remote: .metric.remote_addr, val: (.value[1]|tonumber)}]
  | sort_by(-.val, .remote)
  # 輸出 CSV，欄位與面板表格一致
  | (["Time","remote_addr","Value #A"]
     + (map([ $t, .remote, .val ]) | add))
  | @csv
' > "$OUT"

echo "Wrote $OUT"
head -n 20 "$OUT"
```

輸出示例（前幾行）：
```csv
Time,remote_addr,Value #A
2025-09-21 21:30:59.000,13.232.135.44,236149
2025-09-21 21:30:59.000,13.200.229.14,228785
...
```

---

## 2) 區間查詢：取得逐分鐘序列（query_range）

若你要取得「每分鐘」的計數（方便後續自訂彙總）：

- 變數設定
```bash
HOST="www.starstar123.com"
START="2025-09-21T20:45:00Z"
END="2025-09-21T21:30:59Z"
STEP="60s"             # 每分鐘一筆
TOPK=100
LOKI_URL="http://34.100.235.214:3100"
OUT_RNG="loki_topk_remote_addr_20250921_204500_213059.csv"
```

- 產出逐分鐘 CSV（remote_addr, timestamp, value）
```bash
curl -G -s "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode "query=topk(${TOPK}, sum by (remote_addr) (count_over_time({host=\"$HOST\"} | json [1m])))" \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode "step=$STEP" \
| jq -r '[ "remote_addr","timestamp","value" ],
         (.data.result[]? as $r | $r.values[]? |
           [ $r.metric.remote_addr, (.[0]|tostring), (.[1]|tostring) ])
         | @csv' > "$OUT_RNG"

echo "Wrote $OUT_RNG"
head -n 10 "$OUT_RNG"
```

> 提醒：`query_range` 的 `step` 是「評估點間隔」；每個評估點都會向後取 `[...]` 視窗。

---

## 3) 從逐分鐘 CSV 彙總為 45 分鐘總和

若你已用第 2 步產生每分鐘 CSV，想要在本機做彙總（接近面板語意）：

```bash
IN="loki_topk_remote_addr_20250921_204500_213059.csv"
OUT_AGG="loki_topk_remote_addr_20250921_204500_213059_agg45m.csv"
python3 - "$IN" "$OUT_AGG" <<'PY'
import sys,csv
inp, outp = sys.argv[1], sys.argv[2]
agg = {}
with open(inp, newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        ip = row.get('remote_addr','')
        v = row.get('value','0')
        try:
            n = int(float(v))
        except Exception:
            continue
        if ip:
            agg[ip] = agg.get(ip, 0) + n
rows = sorted(agg.items(), key=lambda x: (-x[1], x[0]))
with open(outp, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['remote_addr','total_45m'])
    for ip, tot in rows:
        w.writerow([ip, tot])
print(outp)
PY

head -n 20 "$OUT_AGG"
```

> 與面板的「exact」一致性仍取決於：視窗邊界是否與 Grafana 評估時間完全一致（秒級/分鐘取整、是否含結束點那 59 秒等）。若要 100% 對齊，建議使用第 1 步的 instant 查詢。

---

## 4) LogQL 語法備忘

- **標籤選擇器**：`{host="example.com", job="nginx"}`
- **管線解析器**：`| json`、`| logfmt`（解析後欄位可用於聚合與篩選）
- **區間向量**：`[...]`，例如 `[45m]` 表示「每個評估點往回 45 分鐘」
- **計數**：`count_over_time(<log-selector> [range])` — 視窗內日誌行數
- **速率**：`rate(count_over_time(... [5m]))` — 每秒速率（適合看趨勢）
- **聚合**：`sum|count|max|min by (label1, label2) ( <向量> )`
- **排名**：`topk(10, <向量>)`、`bottomk(10, <向量>)`

---

## 5) 時間與時區

- Grafana 預設使用瀏覽器時區（例如 CST/UTC+8）。
- Loki API（`time`/`start`/`end`）建議提供 UTC（尾巴 `Z`）。
- 例：`2025-09-21 21:30:59 CST` = `2025-09-21T13:30:59Z`。

---

## 6) 常見問題（FAQ）

- **結果與面板不同？**
  - 確認是否用的是 instant 查詢（`/query`）而非區間查詢（`/query_range`）。
  - 確認 `time` 是否為面板顯示時間點的 UTC 轉換。
  - 確認 `[...]` 視窗長度一致（例如 `[45m]`）。
  - 若用逐分鐘 CSV 再彙總，可能因邊界包含規則造成輕微差異。

- **`query_range` 忘了 `step`**
  - 請務必加入 `--data-urlencode 'step=60s'` 或其他間隔。

- **`remote_addr` 來源**
  - 若來源已把 `remote_addr` 作為標籤，直接可用；若沒有，請透過 `| json` 解析後再在 `sum by (remote_addr)` 使用。

---

## 7) 檔名與輸出位置

- 本說明中的輸出檔案預設都寫在目前工作目錄（建議在 `indrc/loki/` 下執行）。
- 你可以依需要調整 `OUT`/`OUT_RNG`/`OUT_AGG` 變數。

---

## 8) 範例再現（本次你使用的資料）

- 與面板一致（instant）：
  - 檔名：`loki_instant_top85_remote_addr_20250921_213059_CST_sorted.csv`
  - 時間：`2025-09-21 21:30:59 CST`（= `2025-09-21T13:30:59Z`）
  - 語法：`topk(85, sum by (remote_addr) (count_over_time({host="www.starstar123.com"} | json [45m])))`

- 每分鐘序列（range）：
  - 檔名：`loki_topk_remote_addr_20250921_204500_213059.csv`
  - 窗口：`2025-09-21T20:45:00Z` 到 `2025-09-21T21:30:59Z`
  - step：`60s`

- 從每分鐘序列彙總：
  - 檔名：`loki_topk_remote_addr_20250921_204500_213059_agg45m.csv`

---

若你希望我把上述命令包成一鍵腳本（例如傳入 host、topk、時間、窗口就自動輸出 CSV），告訴我參數命名偏好，我可以幫你新增腳本到 `indrc/loki/scripts/`。
