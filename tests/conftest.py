"""
共用 fixtures：Prometheus 假資料、LLM 假回應。
"""
import json
import time
import pytest
from unittest.mock import MagicMock, patch
from config import AppConfig, LLMConfig, PrometheusConfig


# ── 基本設定 fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def llm_config():
    return LLMConfig(
        base_url="http://fake-llm.internal",
        model_id="test-model",
        api_key="none",
        timeout=10,
    )


@pytest.fixture
def prom_config():
    return PrometheusConfig(
        base_url="http://fake-prom.internal:9090",
        timeout=5,
        max_metrics_scan=10,
    )


@pytest.fixture
def app_config(llm_config, prom_config):
    return AppConfig(
        llm=llm_config,
        prometheus=prom_config,
        anomaly_sigma_threshold=2.0,
        max_anomalies_to_report=5,
    )


# ── 時間範圍 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def time_range():
    """固定時間範圍：1 小時"""
    end = 1741600000.0
    start = end - 3600
    return start, end


# ── Prometheus 假資料 ─────────────────────────────────────────────────────────

def make_flat_series(metric_name: str, value: float = 1.0, points: int = 60,
                     start: float = None, labels: dict = None) -> dict:
    """產生完全平坦的時序（無異常）"""
    base = start or 1741596400.0
    return {
        "metric": {"__name__": metric_name, **(labels or {})},
        "values": [[base + i * 60, str(value)] for i in range(points)],
    }


def make_spike_series(metric_name: str, baseline: float = 10.0,
                      spike_value: float = 100.0, spike_idx: int = 30,
                      points: int = 60, start: float = None,
                      labels: dict = None) -> dict:
    """產生在 spike_idx 位置有明顯峰值的時序（有異常）"""
    base = start or 1741596400.0
    values = [baseline] * points
    values[spike_idx] = spike_value
    return {
        "metric": {"__name__": metric_name, **(labels or {})},
        "values": [[base + i * 60, str(values[i])] for i in range(points)],
    }


def prometheus_range_response(results: list) -> dict:
    return {
        "status": "success",
        "data": {"resultType": "matrix", "result": results},
    }


def prometheus_labels_response(names: list) -> dict:
    return {"status": "success", "data": names}


def prometheus_metadata_response(meta: dict) -> dict:
    return {"status": "success", "data": meta}


# ── LLM 假回應 ────────────────────────────────────────────────────────────────

def llm_text_response(content: str) -> dict:
    """純文字回應（無 tool call）"""
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": content, "tool_calls": None},
        }]
    }


def llm_tool_call_response(tool_name: str, arguments: dict,
                            call_id: str = "call_001") -> dict:
    """帶 tool call 的回應"""
    return {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    },
                }],
            },
        }]
    }
