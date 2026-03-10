from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMConfig:
    base_url: str = "http://ai-coding-agent.qq.com/cline"
    model_id: str = "coder-flash"
    api_key: str = "none"
    timeout: int = 120
    max_tokens: int = 4096

    @property
    def chat_endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/v1/chat/completions"


@dataclass
class PrometheusConfig:
    base_url: str = "http://localhost:9090"
    timeout: int = 30
    max_metrics_scan: int = 500  # 最多掃描幾個 metrics 找異常

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
    anomaly_sigma_threshold: float = 2.5  # z-score 超過此值視為異常
    max_anomalies_to_report: int = 20     # 最多回報幾個異常 metrics
    mode_override: Optional[str] = None  # None=auto, "a"/"b"/"c"=強制模式
