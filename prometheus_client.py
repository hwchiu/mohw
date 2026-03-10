import requests
from typing import Optional
from config import PrometheusConfig


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
        """測試 Prometheus 是否可連線"""
        try:
            resp = self.session.get(
                f"{self.config.base_url.rstrip('/')}/-/healthy",
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            try:
                # 有些環境沒有 /-/healthy，改試 /api/v1/query
                resp = self.session.get(
                    self.config.query_endpoint,
                    params={"query": "1"},
                    timeout=5,
                )
                return resp.status_code == 200
            except Exception:
                return False
