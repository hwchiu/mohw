import os
from config import AppConfig, LLMConfig, PrometheusConfig, _parse_ssl_verify


def test_llm_config_reads_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://test.com")
    monkeypatch.setenv("LLM_MODEL_ID", "my-model")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    # 重新實例化才會讀到 monkeypatched env
    c = LLMConfig(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost"),
        model_id=os.getenv("LLM_MODEL_ID", ""),
        api_key=os.getenv("LLM_API_KEY", "none"),
    )
    assert c.base_url == "http://test.com"
    assert c.model_id == "my-model"
    assert c.api_key == "secret"


def test_llm_config_chat_endpoint_no_trailing_slash():
    c = LLMConfig(base_url="http://test.com")
    assert c.chat_endpoint == "http://test.com/v1/chat/completions"


def test_llm_config_chat_endpoint_trailing_slash():
    c = LLMConfig(base_url="http://test.com/")
    assert c.chat_endpoint == "http://test.com/v1/chat/completions"


def test_prometheus_config_endpoints():
    c = PrometheusConfig(base_url="http://prom.internal:9090")
    assert c.query_range_endpoint == "http://prom.internal:9090/api/v1/query_range"
    assert c.query_endpoint == "http://prom.internal:9090/api/v1/query"
    assert c.labels_endpoint == "http://prom.internal:9090/api/v1/label/__name__/values"
    assert c.metadata_endpoint == "http://prom.internal:9090/api/v1/metadata"


def test_prometheus_config_trailing_slash():
    c = PrometheusConfig(base_url="http://prom.internal:9090/")
    assert not c.query_range_endpoint.endswith("//api/v1/query_range")


def test_app_config_defaults():
    c = AppConfig()
    assert c.anomaly_sigma_threshold == 2.5
    assert c.max_anomalies_to_report == 20
    assert c.mode_override is None


# ── _parse_ssl_verify ─────────────────────────────────────────────────────────

def test_parse_ssl_verify_default(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_SSL_VERIFY", raising=False)
    assert _parse_ssl_verify() is True


def test_parse_ssl_verify_true_string(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_SSL_VERIFY", "true")
    assert _parse_ssl_verify() is True


def test_parse_ssl_verify_false_string(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_SSL_VERIFY", "false")
    assert _parse_ssl_verify() is False


def test_parse_ssl_verify_custom_path(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_SSL_VERIFY", "/etc/ssl/certs/ca.crt")
    result = _parse_ssl_verify()
    assert result == "/etc/ssl/certs/ca.crt"


def test_prometheus_config_ssl_verify_default():
    c = PrometheusConfig(base_url="https://prom.internal:9090")
    assert c.ssl_verify is True


def test_prometheus_config_ssl_verify_false():
    c = PrometheusConfig(base_url="https://prom.internal:9090", ssl_verify=False)
    assert c.ssl_verify is False


def test_prometheus_config_ssl_verify_custom_path():
    c = PrometheusConfig(base_url="https://prom.internal:9090", ssl_verify="/path/to/ca.crt")
    assert c.ssl_verify == "/path/to/ca.crt"
