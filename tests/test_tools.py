import json
import pytest
from unittest.mock import MagicMock, patch
from tools import ToolExecutor
from tests.conftest import (
    make_flat_series,
    make_spike_series,
    prometheus_range_response,
    prometheus_labels_response,
    prometheus_metadata_response,
)


@pytest.fixture
def executor(app_config, time_range):
    start, end = time_range
    prom = MagicMock()
    return ToolExecutor(prom, app_config, start, end), prom


# ── list_metrics ──────────────────────────────────────────────────────────────

def test_list_metrics_all(executor):
    ex, prom = executor
    prom.list_metric_names.return_value = ["cpu_usage", "memory_usage", "disk_io"]

    result = json.loads(ex.execute("list_metrics", {}))

    assert result["count"] == 3
    assert "cpu_usage" in result["metrics"]


def test_list_metrics_with_filter(executor):
    ex, prom = executor
    prom.list_metric_names.return_value = ["cpu_usage", "cpu_throttled", "memory_usage"]

    result = json.loads(ex.execute("list_metrics", {"filter_keyword": "cpu"}))

    assert result["count"] == 2
    assert all("cpu" in m for m in result["metrics"])


def test_list_metrics_filter_case_insensitive(executor):
    ex, prom = executor
    prom.list_metric_names.return_value = ["CPU_USAGE", "memory_usage"]

    result = json.loads(ex.execute("list_metrics", {"filter_keyword": "cpu"}))

    assert result["count"] == 1


def test_list_metrics_no_match(executor):
    ex, prom = executor
    prom.list_metric_names.return_value = ["cpu_usage", "memory_usage"]

    result = json.loads(ex.execute("list_metrics", {"filter_keyword": "network"}))

    assert result["count"] == 0


# ── query_metric ──────────────────────────────────────────────────────────────

def test_query_metric_summary(executor, time_range):
    ex, prom = executor
    start, _ = time_range
    series = make_spike_series("cpu_usage", baseline=20.0, spike_value=95.0, start=start)
    prom.query_range.return_value = [series]

    result = json.loads(ex.execute("query_metric", {"metric_name": "cpu_usage"}))

    assert result["metric"] == "cpu_usage"
    assert len(result["results"]) == 1
    summary = result["results"][0]["summary"]
    assert summary["max"] == pytest.approx(95.0)
    assert summary["min"] == pytest.approx(20.0)


def test_query_metric_no_data(executor):
    ex, prom = executor
    prom.query_range.return_value = []

    result = json.loads(ex.execute("query_metric", {"metric_name": "nonexistent"}))

    assert result["result"] == "no data"


def test_query_metric_raw_timeseries(executor, time_range):
    ex, prom = executor
    start, _ = time_range
    series = make_flat_series("memory_usage", value=50.0, start=start)
    prom.query_range.return_value = [series]

    result = json.loads(ex.execute("query_metric",
                                    {"metric_name": "memory_usage", "summarize": False}))

    assert "timeseries" in result["results"][0]
    assert len(result["results"][0]["timeseries"]) > 0


# ── get_metric_info ───────────────────────────────────────────────────────────

def test_get_metric_info_with_metadata(executor):
    ex, prom = executor
    prom.get_metadata.return_value = {
        "cpu_usage": [{"type": "gauge", "help": "CPU utilization percentage"}]
    }

    result = json.loads(ex.execute("get_metric_info", {"metric_name": "cpu_usage"}))

    assert result["type"] == "gauge"
    assert "CPU" in result["help"]


def test_get_metric_info_no_metadata(executor):
    ex, prom = executor
    prom.get_metadata.return_value = {}

    result = json.loads(ex.execute("get_metric_info", {"metric_name": "unknown_metric"}))

    assert result["type"] == "unknown"


def test_get_metric_info_error_handled(executor):
    ex, prom = executor
    prom.get_metadata.side_effect = Exception("API error")

    result = json.loads(ex.execute("get_metric_info", {"metric_name": "cpu_usage"}))

    assert result["type"] == "unknown"


# ── detect_anomalies ──────────────────────────────────────────────────────────

def test_detect_anomalies_tool(executor, time_range, app_config):
    ex, prom = executor
    start, end = time_range

    spike = make_spike_series("cpu_usage", baseline=10.0, spike_value=300.0, start=start)
    prom.list_series_for_node.return_value = [{"__name__": "cpu_usage", "instance": "worker-01"}]
    prom.get_metadata.return_value = {}
    prom.query_range.return_value = [spike]
    # executor fixture uses app_config which has target_node=None; set it for this test
    ex.config.target_node = "worker-01"

    result = json.loads(ex.execute("detect_anomalies", {}))

    assert result["anomaly_count"] >= 1
    assert result["anomalies"][0]["metric"] == "cpu_usage"
    assert "severity_zscore" in result["anomalies"][0]
    assert "anomaly_time" in result["anomalies"][0]


def test_detect_anomalies_tool_top_n(executor, time_range):
    ex, prom = executor
    start, _ = time_range

    all_metrics = {f"m_{i}": [make_spike_series(f"m_{i}", spike_value=999.0, start=start)]
                   for i in range(10)}
    prom.list_series_for_node.return_value = [
        {"__name__": k, "instance": "worker-01"} for k in all_metrics
    ]
    prom.get_metadata.return_value = {}
    prom.query_range.side_effect = lambda q, *a, **kw: all_metrics.get(q.split("{")[0], [])
    ex.config.target_node = "worker-01"

    result = json.loads(ex.execute("detect_anomalies", {"top_n": 3}))

    assert result["anomaly_count"] <= 3


# ── unknown tool ──────────────────────────────────────────────────────────────

def test_unknown_tool_returns_error(executor):
    ex, _ = executor
    result = json.loads(ex.execute("nonexistent_tool", {}))
    assert "error" in result
