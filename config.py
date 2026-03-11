import os
from dataclasses import dataclass, field
from typing import Optional, Union
from dotenv import load_dotenv

load_dotenv()


def _parse_ssl_verify() -> Union[bool, str]:
    """
    解析 PROMETHEUS_SSL_VERIFY 環境變數：
      未設定 / "true"  → True（載入系統憑證，預設）
      "false"          → False（跳過驗證，僅限測試環境）
      其他字串         → 視為自訂 CA bundle 檔案路徑
    """
    val = os.getenv("PROMETHEUS_SSL_VERIFY", "true").strip()
    if val.lower() == "false":
        return False
    if val.lower() in ("true", "1", "yes", ""):
        return True
    return val  # 自訂 CA bundle 路徑


@dataclass
class LLMConfig:
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "http://localhost"))
    model_id: str = field(default_factory=lambda: os.getenv("LLM_MODEL_ID", ""))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "none"))
    timeout: int = int(os.getenv("LLM_TIMEOUT", "120"))
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

    @property
    def chat_endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/v1/chat/completions"


@dataclass
class PrometheusConfig:
    base_url: str = field(default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    timeout: int = 30
    max_metrics_scan: int = 500  # 無節點模式下最多掃描幾個 metrics
    # SSL 驗證：True = 載入系統憑證、False = 跳過驗證、str = 自訂 CA bundle 路徑
    ssl_verify: Union[bool, str] = field(default_factory=_parse_ssl_verify)

    @property
    def series_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/series"

    @property
    def query_range_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/query_range"

    @property
    def query_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/query"

    @property
    def labels_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/label/__name__/values"

    @property
    def metadata_endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/metadata"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    anomaly_sigma_threshold: float = float(os.getenv("ANOMALY_SIGMA", "2.5"))
    max_anomalies_to_report: int = int(os.getenv("MAX_ANOMALIES", "20"))
    mode_override: Optional[str] = field(default_factory=lambda: os.getenv("ANALYSIS_MODE"))
    # 節點篩選：指定後所有 PromQL query 都只查詢該節點的 metrics
    target_node: Optional[str] = field(default_factory=lambda: os.getenv("TARGET_NODE"))
    # Prometheus 中代表節點的 label 名稱（node_exporter 常用 instance，k8s 常用 node）
    node_label: str = field(default_factory=lambda: os.getenv("NODE_LABEL", "instance"))
    # 並行掃描的 worker 數（節點模式下生效）
    scan_workers: int = int(os.getenv("SCAN_WORKERS", "10"))
