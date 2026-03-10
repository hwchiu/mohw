import re
import requests
from typing import Optional
from config import PrometheusConfig


def apply_node_filter(query: str, label: str, value: str) -> str:
    """
    將節點 label filter 注入 PromQL 表達式。

    支援三種格式：
      bare metric:   cpu_usage              → cpu_usage{instance="node1"}
      with labels:   cpu_usage{job="foo"}   → cpu_usage{instance="node1",job="foo"}
      function call: rate(cpu_usage[5m])    → rate(cpu_usage{instance="node1"}[5m])
    """
    if not value:
        return query

    filter_str = f'{label}="{value}"'

    # 已有 label selector：在第一個 { 後插入
    if "{" in query:
        return re.sub(r"\{", "{" + filter_str + ",", query, count=1)

    # function 呼叫（例如 rate(metric[5m])）：找第一個裸 metric name 並注入
    func_match = re.match(r"^([\w_]+\()([a-zA-Z_:][a-zA-Z0-9_:]*)(.*)", query)
    if func_match:
        prefix, metric, suffix = func_match.groups()
        return f"{prefix}{metric}{{{filter_str}}}{suffix}"

    # 純 metric name
    return f"{query}{{{filter_str}}}"


class PrometheusClient:
    """封裝 Prometheus HTTP API 查詢"""

    def __init__(self, config: PrometheusConfig):
        self.config = config
        self.session = requests.Session()
        self.session.timeout = config.timeout

    def list_metric_names(self) -> list[str]:
        """列出所有 metric 名稱"""
        resp = self.session.get(self.config.labels_endpoint)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus error: {data.get('error')}")
        return sorted(data["data"])

    def get_metadata(self, metric_name: Optional[str] = None) -> dict:
        """取得 metric 的 help text 與 type"""
        params = {}
        if metric_name:
            params["metric"] = metric_name
        resp = self.session.get(self.config.metadata_endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {})

    def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: int = 60,
    ) -> list[dict]:
        """
        執行 PromQL range query。
        回傳 list of { metric: {labels}, values: [[ts, val], ...] }
        """
        params = {
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        }
        resp = self.session.get(self.config.query_range_endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus error: {data.get('error')}")
        result = data.get("data", {}).get("result", [])
        return result

    def query_instant(self, query: str, timestamp: Optional[float] = None) -> list[dict]:
        """執行 PromQL instant query"""
        params = {"query": query}
        if timestamp:
            params["time"] = timestamp
        resp = self.session.get(self.config.query_endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus error: {data.get('error')}")
        return data.get("data", {}).get("result", [])

    def check_connection(self) -> bool:
        """測試 Prometheus 是否可連線（任一端點成功即視為連線正常）"""
        detail = self.diagnose_connection()
        return detail["connected"]

    def diagnose_connection(self) -> dict:
        """
        詳細診斷 Prometheus 連線狀況。
        回傳 dict 包含每個端點的嘗試結果，方便排查問題。
        """
        base = self.config.base_url.rstrip("/")
        probes = [
            ("/-/healthy",    f"{base}/-/healthy",          {}),
            ("/api/v1/query", self.config.query_endpoint,   {"query": "1"}),
        ]
        results = {}

        for name, url, params in probes:
            try:
                resp = self.session.get(url, params=params, timeout=5)
                results[name] = {
                    "url": url,
                    "status_code": resp.status_code,
                    "ok": resp.status_code == 200,
                    "error": None,
                }
            except Exception as e:
                results[name] = {
                    "url": url,
                    "status_code": None,
                    "ok": False,
                    "error": str(e),
                }

        connected = any(r["ok"] for r in results.values())
        return {"connected": connected, "probes": results}
