import re
import ssl
import requests
from typing import Optional, Union
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


def _resolve_ssl_verify(ssl_verify: Union[bool, str]) -> Union[bool, str]:
    """
    將 ssl_verify 設定轉換成 requests session 能接受的 verify 值。

    - False         → False（跳過驗證）
    - str（路徑）   → 使用指定 CA bundle 檔案
    - True          → 嘗試依序載入：系統 CA bundle → certifi
    """
    if ssl_verify is False:
        return False
    if isinstance(ssl_verify, str):
        return ssl_verify  # 自訂 CA bundle 路徑

    # ssl_verify is True：優先使用系統憑證
    paths = ssl.get_default_verify_paths()
    if paths.cafile:
        return paths.cafile
    if paths.capath:
        return paths.capath

    # 退回 certifi（requests 內建的 CA bundle）
    try:
        import certifi
        return certifi.where()
    except ImportError:
        pass

    return True  # 讓 requests 自行處理


class PrometheusClient:
    """封裝 Prometheus HTTP API 查詢"""

    def __init__(self, config: PrometheusConfig):
        self.config = config
        self.session = requests.Session()
        self.session.timeout = config.timeout
        self.session.verify = _resolve_ssl_verify(config.ssl_verify)

    def list_metric_names(self) -> list[str]:
        """列出全域所有 metric 名稱（未過濾節點）"""
        resp = self.session.get(self.config.labels_endpoint)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus error: {data.get('error')}")
        return sorted(data["data"])

    def list_series_for_node(
        self,
        node_label: str,
        node_value: str,
        start: float,
        end: float,
    ) -> list[dict]:
        """
        用 /api/v1/series 查詢特定節點在時間範圍內存在的所有 series。
        比 list_metric_names() + 逐一 query 快得多（只需一次 HTTP 請求）。

        回傳 list of label dicts，每個 dict 包含 __name__ 和其他 labels。
        """
        params = {
            "match[]": f'{{{node_label}="{node_value}"}}',
            "start": start,
            "end": end,
        }
        resp = self.session.get(self.config.series_endpoint, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus error: {data.get('error')}")
        return data.get("data", [])

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
