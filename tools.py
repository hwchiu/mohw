"""
提供給 LLM 的 Prometheus 工具集（OpenAI function calling 格式）
"""

import json
import numpy as np
from datetime import datetime, timezone
from config import AppConfig
from prometheus_client import PrometheusClient, apply_node_filter


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ──────────────────────────────────────────
# 工具定義（schema，傳給 LLM）
# ──────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_metrics",
            "description": (
                "List all available Prometheus metric names. "
                "Use this to discover what metrics exist before deciding which to investigate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_keyword": {
                        "type": "string",
                        "description": "Optional keyword to filter metric names (case-insensitive substring match)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_metric",
            "description": (
                "Query a Prometheus metric over the analysis time range. "
                "Returns statistical summary and notable anomaly timestamps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_name": {
                        "type": "string",
                        "description": "Metric name or PromQL expression to query",
                    },
                    "summarize": {
                        "type": "boolean",
                        "description": "If true, return only statistical summary. If false, return raw time series.",
                        "default": True,
                    },
                },
                "required": ["metric_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metric_info",
            "description": "Get help text, type, and label names for a metric.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_name": {
                        "type": "string",
                        "description": "Metric name to look up",
                    }
                },
                "required": ["metric_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomalies",
            "description": (
                "Automatically scan all metrics in the time range and return those with "
                "statistically significant anomalies (z-score based). "
                "Use this first to get an overview of what went wrong."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {
                        "type": "integer",
                        "description": "Number of top anomalies to return (default 15)",
                        "default": 15,
                    }
                },
                "required": [],
            },
        },
    },
]


# ──────────────────────────────────────────
# 工具執行器
# ──────────────────────────────────────────

class ToolExecutor:
    def __init__(self, prom: PrometheusClient, config: AppConfig, start: float, end: float):
        self.prom = prom
        self.config = config
        self.start = start
        self.end = end
        self._metric_names_cache: list[str] | None = None

    def _get_metric_names(self) -> list[str]:
        if self._metric_names_cache is None:
            self._metric_names_cache = self.prom.list_metric_names()
        return self._metric_names_cache

    def execute(self, tool_name: str, arguments: dict) -> str:
        """執行工具並回傳 JSON 字串結果"""
        try:
            if tool_name == "list_metrics":
                return self._list_metrics(**arguments)
            elif tool_name == "query_metric":
                return self._query_metric(**arguments)
            elif tool_name == "get_metric_info":
                return self._get_metric_info(**arguments)
            elif tool_name == "detect_anomalies":
                return self._detect_anomalies(**arguments)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _list_metrics(self, filter_keyword: str = "") -> str:
        names = self._get_metric_names()
        if filter_keyword:
            names = [n for n in names if filter_keyword.lower() in n.lower()]
        return json.dumps({"count": len(names), "metrics": names[:200]})

    def _query_metric(self, metric_name: str, summarize: bool = True) -> str:
        query = apply_node_filter(
            metric_name, self.config.node_label, self.config.target_node or ""
        )
        results = self.prom.query_range(query, self.start, self.end, step=60)
        if not results:
            return json.dumps({"metric": metric_name, "result": "no data"})

        output = []
        for series in results[:5]:  # 最多 5 個 label 組合
            values = [float(v[1]) for v in series["values"] if v[1] != "NaN"]
            if not values:
                continue
            arr = np.array(values)
            labels = {k: v for k, v in series["metric"].items() if k != "__name__"}

            if summarize:
                mean = float(arr.mean())
                std = float(arr.std())
                zscores = np.abs((arr - mean) / std) if std > 0 else np.zeros_like(arr)
                anomaly_indices = np.where(zscores > self.config.anomaly_sigma_threshold)[0]
                anomaly_times = [
                    _fmt(float(series["values"][i][0]))
                    for i in anomaly_indices[:5]
                ]
                output.append({
                    "labels": labels,
                    "summary": {
                        "min": round(float(arr.min()), 4),
                        "max": round(float(arr.max()), 4),
                        "mean": round(mean, 4),
                        "std": round(std, 4),
                        "p95": round(float(np.percentile(arr, 95)), 4),
                    },
                    "anomaly_times": anomaly_times,
                })
            else:
                # 回傳最多 60 個點（降採樣）
                step = max(1, len(values) // 60)
                sampled = [
                    {"time": _fmt(float(series["values"][i][0])), "value": round(float(values[i]), 4)}
                    for i in range(0, len(values), step)
                ]
                output.append({"labels": labels, "timeseries": sampled})

        return json.dumps({"metric": metric_name, "results": output})

    def _get_metric_info(self, metric_name: str) -> str:
        try:
            meta = self.prom.get_metadata(metric_name)
        except Exception:
            meta = {}
        info = meta.get(metric_name, [])
        if info:
            return json.dumps({
                "metric": metric_name,
                "type": info[0].get("type", "unknown"),
                "help": info[0].get("help", ""),
            })
        return json.dumps({"metric": metric_name, "type": "unknown", "help": "no metadata"})

    def _detect_anomalies(self, top_n: int = 15) -> str:
        from anomaly_detector import detect_anomalies

        reports = detect_anomalies(
            self.prom,
            self.config,
            self.start,
            self.end,
        )[:top_n]

        items = []
        for r in reports:
            label_str = {k: v for k, v in r.labels.items() if k != "__name__"}
            items.append({
                "metric": r.metric_name,
                "labels": label_str,
                "severity_zscore": r.severity,
                "type": r.metric_type,
                "help": r.metric_help,
                "baseline": {"mean": r.baseline_mean, "std": r.baseline_std},
                "peak_value": r.peak_value,
                "anomaly_time": _fmt(r.anomaly_time),
            })
        return json.dumps({"anomaly_count": len(items), "anomalies": items})
