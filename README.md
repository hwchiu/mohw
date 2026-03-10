# Prometheus 智慧異常分析工具

> 在受限的內網環境中，利用 Local LLM 自動分析 Prometheus metrics，找出系統不穩定的根本原因。

---

## 背景與問題

當系統發生不穩定時，Prometheus 可能存有數百個 metrics。問題是：**你不知道該看哪些**。

傳統做法需要 SRE 依靠經驗逐一翻查 dashboard，費時且容易遺漏不熟悉的 metrics。

本工具的核心概念是：**讓 LLM 代替你去探索 metrics，自主找出異常，推導根本原因**。

```
你提供：Prometheus endpoint + 發生異常的時間範圍
工具回傳：根本原因分析、證據、問題傳播鏈、建議下一步
```

---

## 功能特色

- **零先備知識**：不需要事先知道要查哪些 metrics，LLM 自行探索
- **自動模式選擇**：啟動時探測 LLM 的能力，自動選擇最佳分析模式
- **離線友善**：只需要一個 OpenAI 相容的 local model endpoint（cline / Ollama / LM Studio / vLLM 均可）
- **Z-score 異常預篩**：程式先做統計分析，大幅降低 LLM 需要閱讀的資料量
- **三種分析模式**：依 model 能力自動降級，確保在弱模型上也能產出結果

---

## 系統需求

| 項目 | 需求 |
|------|------|
| Python | 3.11+ |
| Prometheus | 任意版本，HTTP API 可存取 |
| LLM | 支援 OpenAI 相容 API 的 local model |
| 網路 | 純內網環境即可，不需要對外連線 |

---

## 安裝

```bash
git clone https://github.com/hwchiu/mohw.git
cd mohw
pip install -r requirements.txt

# 建立 .env 設定檔
cp .env.example .env
# 編輯 .env，填入你的 LLM endpoint 與 Prometheus URL
```

---

## 快速開始

### 方式一：透過 `.env` 設定（推薦）

```bash
# .env
LLM_BASE_URL=http://test.com
LLM_MODEL_ID=my-model
PROMETHEUS_URL=http://prometheus.internal:9090
```

```bash
python __main__.py \
  --start "2026-03-10 14:00:00" \
  --end   "2026-03-10 15:00:00"
```

### 方式二：全部透過 CLI 參數

```bash
python __main__.py \
  --prometheus http://prometheus.internal:9090 \
  --start "2026-03-10 14:00:00" \
  --end   "2026-03-10 15:00:00" \
  --llm-url http://test.com \
  --model my-model
```

CLI 參數的優先度高於 `.env`，`.env` 高於程式內建預設值。

工具會自動：
1. 測試 Prometheus 連線
2. 探測 LLM 的 function calling 能力
3. 選擇最適合的分析模式
4. 輸出根本原因分析報告

---

## 完整參數說明

```
Options:
  -p, --prometheus  TEXT     Prometheus base URL（優先於 .env 的 PROMETHEUS_URL） [必填]
  -s, --start       TEXT     分析開始時間（ISO 格式或 unix timestamp）             [必填]
  -e, --end         TEXT     分析結束時間（ISO 格式或 unix timestamp）             [必填]
      --llm-url     TEXT     LLM API base URL（優先於 .env 的 LLM_BASE_URL）
  -m, --model       TEXT     Model ID（優先於 .env 的 LLM_MODEL_ID）
      --api-key     TEXT     LLM API key（優先於 .env 的 LLM_API_KEY，預設：none）
      --mode        TEXT     強制指定模式：a / b / c（預設自動偵測）
      --sigma       FLOAT    z-score 異常閾值（預設 2.5）
                             值越小 → 偵測越敏感 → 異常越多
      --max-metrics INT      最多掃描幾個 metrics（預設 500）
      --max-anomalies INT    最多回報幾個異常 metrics（預設 20）
```

### 時間格式範例

```bash
# ISO 格式（自動視為 UTC）
--start "2026-03-10 14:00:00" --end "2026-03-10 15:00:00"

# 帶時區
--start "2026-03-10T14:00:00+08:00" --end "2026-03-10T15:00:00+08:00"

# Unix timestamp
--start 1741608000 --end 1741611600
```

---

## 分析模式

工具啟動時會自動執行能力探針（約 10～30 秒），根據結果選擇最合適的模式。

### Mode A：Full Agentic（最佳）

```
需求：Model 支援多輪 function calling

LLM 自主決定：
  → 呼叫 detect_anomalies()    找出所有異常 metrics
  → 呼叫 query_metric()        深挖可疑 metrics
  → 呼叫 list_metrics()        搜尋相關 metrics
  → 呼叫 get_metric_info()     查閱 metric 語意
  → 反覆迭代，直到推導出結論
```

適合較大的模型（≥13B），能做多步推理。

### Mode B：Semi Auto

```
需求：Model 支援單輪 function calling

程式先執行 detect_anomalies() 掃描全部 metrics
↓
LLM 拿到異常清單後，決定要深挖哪幾個（單輪工具呼叫）
↓
LLM 根據取得的資料給出結論
```

適合中型模型，兼顧能力與穩定性。

### Mode C：Static Analysis（保底）

```
需求：只需要基本文字推理能力

程式自動完成：
  1. 掃描所有 metrics，標記異常（z-score）
  2. 擷取統計摘要（mean / std / peak / P95）
  3. 標記異常發生時間

全部整理成結構化文字 → 一次性交給 LLM 分析
```

適合小型模型或不支援 tool use 的 model，仍可產出有用的分析。

### 強制指定模式

如果你已經知道 model 的能力，可跳過探針直接指定：

```bash
# 強制使用靜態分析（最快啟動）
python __main__.py ... --mode c

# 強制使用 Full Agentic
python __main__.py ... --mode a
```

---

## 可用工具（LLM 可呼叫）

| 工具名稱 | 說明 |
|---------|------|
| `detect_anomalies` | 掃描全部 metrics，回傳 z-score 異常清單（依嚴重度排序） |
| `list_metrics` | 列出所有 metric 名稱，支援關鍵字篩選 |
| `query_metric` | 查詢特定 metric 的時序資料，回傳統計摘要或原始值 |
| `get_metric_info` | 取得 metric 的 help text 與資料類型（gauge/counter/...） |

---

## 異常偵測邏輯

本工具使用 **Z-score** 自動標記異常：

```
z = |value - mean| / std

若 z > sigma（預設 2.5）→ 視為異常
```

**調整建議：**

| `--sigma` 值 | 效果 | 適用場景 |
|-------------|------|---------|
| `1.5` | 非常敏感，異常較多 | 希望不遺漏任何線索 |
| `2.5`（預設） | 平衡 | 一般日常分析 |
| `3.5` | 保守，只回報明顯異常 | metrics 本身波動較大的環境 |

---

## LLM 串接設定

所有 LLM 相關設定建議寫在 `.env`：

```bash
# .env
LLM_BASE_URL=http://test.com
LLM_MODEL_ID=my-model
LLM_API_KEY=none
```

`.env` 對應 cline CLI 的設定方式：

```bash
# cline 初始設定指令
cline auth -k "none" --provider openai \
  --baseurl "http://test.com" \
  --modelid my-model
```

### 其他相容的 LLM 後端

| 後端 | `LLM_BASE_URL`（.env） | `LLM_MODEL_ID`（.env） |
|------|----------------------|----------------------|
| Ollama | `http://localhost:11434` | `qwen2.5:14b` |
| LM Studio | `http://localhost:1234` | `（你載入的模型名稱）` |
| vLLM | `http://localhost:8000` | `（你部署的模型名稱）` |

---

## Docker 使用

```bash
# 建立 image（tag 對應當前 git commit）
make build

# 執行分析
docker run --rm hwchiu/mohw:prometheus-analyzer-latest \
  --prometheus http://prometheus.internal:9090 \
  --start "2026-03-10 14:00:00" \
  --end   "2026-03-10 15:00:00"

# 一步完成 build + push 到 registry
make release
```

### Makefile 指令

| 指令 | 說明 |
|------|------|
| `make build` | 建立 docker image，tag 為當前 commit hash |
| `make push` | push 到 `hwchiu/mohw` container registry |
| `make release` | build + push 一步完成 |
| `make run ARGS="..."` | 本地執行（傳入 CLI 參數） |
| `make info` | 顯示當前 commit 與 image tag |
| `make clean` | 刪除本地 image |

---

## 執行測試

```bash
# 執行全部測試（不需要 Prometheus 或 LLM）
pytest tests/ -v

# 執行特定模組的測試
pytest tests/test_anomaly_detector.py -v
pytest tests/test_capability_probe.py -v
pytest tests/test_analyzer.py -v

# 顯示測試覆蓋率
pytest tests/ --tb=short -q
```

測試全部使用 mock，**不需要任何外部服務**即可執行。

---

## 輸出範例

```
╭─────────────────────────────────────────────────────╮
│              Prometheus 智慧異常分析                  │
│                                                     │
│  Prometheus:  http://prometheus.internal:9090       │
│  時間範圍:    2026-03-10 14:00:00 ~ 15:00:00 UTC    │
│  LLM:         http://test.com                       │
│  模式:        自動偵測                               │
╰─────────────────────────────────────────────────────╯

測試 Prometheus 連線...  ✓
正在探測 model 能力...
Model 支援多輪 tool calling → 使用 A 模式（Full Agentic）

────────────── 開始分析 ──────────────
▶ Mode A: Full Agentic 分析
  迭代 1/10...
  → 呼叫工具: detect_anomalies({})
  迭代 2/10...
  → 呼叫工具: query_metric({'metric_name': 'node_memory_MemAvailable_bytes'})
  迭代 3/10...

────────────── 分析結果 ──────────────
╭─────────────── 根本原因分析（Mode A）───────────────╮
│                                                     │
│  **Root Cause**                                     │
│  Memory leak in api-server pod leading to OOM kill  │
│                                                     │
│  **Evidence**                                       │
│  - node_memory_MemAvailable_bytes: 持續下降          │
│    從 14:00 的 8GB 降至 14:35 的 400MB              │
│  - node_vmstat_oom_kill: 14:36 出現 spike（+3）     │
│  - kube_pod_restarts_total{pod="api-server"}: +3    │
│                                                     │
│  **Propagation Chain**                              │
│  Memory 洩漏（14:00）→ OOM kill（14:36）            │
│  → api-server 重啟 → HTTP 5xx 上升（14:37）         │
│                                                     │
│  **Confidence**: High                               │
│                                                     │
│  **Suggested Next Steps**                           │
│  1. 檢查 api-server 的 heap dump 或記憶體 profile   │
│  2. 確認是否有 cache 未設上限                        │
│  3. 查看 14:00 前的 deployment 變更記錄             │
╰─────────────────────────────────────────────────────╯
```

---

## 專案結構

```
mohw/
├── .env.example          # 環境變數範本（複製為 .env 後填入實際值）
├── __main__.py           # CLI 入口
├── config.py             # 設定檔（LLM / Prometheus / App）
├── prometheus_client.py  # Prometheus HTTP API 封裝
├── anomaly_detector.py   # Z-score 異常偵測 + 報告格式化
├── capability_probe.py   # LLM 能力探針，自動選擇 A/B/C 模式
├── tools.py              # LLM 可呼叫的工具集（function calling schema）
├── analyzer.py           # 三種分析模式實作
├── tests/                # 單元測試（全部使用 mock）
├── Dockerfile            # Multi-stage build
├── Makefile              # Build / push 自動化
└── requirements.txt
```

---

## 授權

MIT
