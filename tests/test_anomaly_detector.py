import pytest
from unittest.mock import MagicMock, patch
from anomaly_detector import detect_anomalies, format_anomaly_summary, AnomalyReport, _analyze_series
from tests.conftest import (
    make_flat_series,
    make_spike_series,
    prometheus_range_response,
    prometheus_labels_response,
    prometheus_metadata_response,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_node_prom_mock(series_list: list, series_map: dict, metadata: dict = None):
    """
    Build a PrometheusClient mock for node mode.
    series_list: raw /api/v1/series response items (list of label dicts).
    series_map: {metric_name: [series, ...]}
    """
    mock = MagicMock()
    mock.list_series_for_node.return_value = series_list
    mock.get_metadata.return_value = metadata or {}
    mock.query_range.side_effect = lambda q, *a, **kw: series_map.get(q.split("{")[0], [])
    return mock


# ── _analyze_series (unit) ────────────────────────────────────────────────────

def test_analyze_series_detects_spike(time_range):
    start, _ = time_range
    series = make_spike_series("cpu_usage", baseline=10.0, spike_value=200.0, start=start)
    report = _analyze_series(series, "cpu_usage", sigma=2.5, metadata={})
    assert report is not None
    assert report.severity > 2.5


def test_analyze_series_flat_returns_none(time_range):
    start, _ = time_range
    flat = make_flat_series("up", value=1.0, start=start)
    assert _analyze_series(flat, "up", sigma=2.5, metadata={}) is None


def test_analyze_series_too_few_points():
    short_series = {
        "metric": {"__name__": "short"},
        "values": [[1000, "10"], [1060, "10"], [1120, "200"]],
    }
    assert _analyze_series(short_series, "short", sigma=2.5, metadata={}) is None


def test_analyze_series_attaches_metadata(time_range):
    start, _ = time_range
    spike = make_spike_series("node_cpu", baseline=5.0, spike_value=999.0, start=start)
    meta = {"node_cpu": [{"type": "gauge", "help": "CPU usage"}]}
    report = _analyze_series(spike, "node_cpu", sigma=2.5, metadata=meta)
    assert report.metric_type == "gauge"
    assert "CPU" in report.metric_help


# ── detect_anomalies – non-node (sequential) mode ────────────────────────────

def test_detect_anomalies_finds_spike(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    spike = make_spike_series("cpu_usage", baseline=10.0, spike_value=200.0, start=start)
    series_list = [{"__name__": "cpu_usage", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"cpu_usage": [spike]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert len(reports) == 1
    assert reports[0].metric_name == "cpu_usage"
    assert reports[0].severity > app_config.anomaly_sigma_threshold


def test_detect_anomalies_ignores_flat_series(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    flat = make_flat_series("up", value=1.0, start=start)
    series_list = [{"__name__": "up", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"up": [flat]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports == []


def test_detect_anomalies_ignores_constant_zero(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    flat = make_flat_series("no_data_metric", value=0.0, start=start)
    series_list = [{"__name__": "no_data_metric", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"no_data_metric": [flat]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports == []


def test_detect_anomalies_skips_scrape_metrics(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    spike = make_spike_series("scrape_duration_seconds", baseline=0.01,
                               spike_value=100.0, start=start)
    series_list = [{"__name__": "scrape_duration_seconds", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"scrape_duration_seconds": [spike]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports == []


def test_detect_anomalies_sorted_by_severity(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    low_spike = make_spike_series("metric_a", baseline=10.0, spike_value=30.0, start=start)
    high_spike = make_spike_series("metric_b", baseline=10.0, spike_value=500.0, start=start)
    series_list = [
        {"__name__": "metric_a", "instance": "worker-01"},
        {"__name__": "metric_b", "instance": "worker-01"},
    ]
    prom = _make_node_prom_mock(series_list, {"metric_a": [low_spike], "metric_b": [high_spike]})

    reports = detect_anomalies(prom, app_config, start, end)

    severities = [r.severity for r in reports]
    assert severities == sorted(severities, reverse=True)


def test_detect_anomalies_respects_max_limit(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    app_config.max_anomalies_to_report = 2

    spikes = {f"metric_{i}": [make_spike_series(f"metric_{i}", spike_value=999.0, start=start)]
              for i in range(10)}
    series_list = [{"__name__": k, "instance": "worker-01"} for k in spikes]
    prom = _make_node_prom_mock(series_list, spikes)

    reports = detect_anomalies(prom, app_config, start, end)

    assert len(reports) <= 2


def test_detect_anomalies_includes_metadata(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    spike = make_spike_series("node_cpu_seconds_total", baseline=5.0,
                               spike_value=999.0, start=start)
    meta = {
        "node_cpu_seconds_total": [{"type": "counter", "help": "CPU seconds total"}]
    }
    series_list = [{"__name__": "node_cpu_seconds_total", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"node_cpu_seconds_total": [spike]}, metadata=meta)

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports[0].metric_type == "counter"
    assert "CPU" in reports[0].metric_help


def test_detect_anomalies_handles_query_error(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    prom = MagicMock()
    prom.list_series_for_node.return_value = [{"__name__": "bad_metric", "instance": "worker-01"}]
    prom.get_metadata.return_value = {}
    prom.query_range.side_effect = Exception("timeout")

    reports = detect_anomalies(prom, app_config, start, end)
    assert reports == []


def test_detect_anomalies_too_few_points(app_config, time_range):
    start, end = time_range
    app_config.target_node = "worker-01"
    short_series = {
        "metric": {"__name__": "short_metric"},
        "values": [[start, "100"], [start + 60, "1"], [start + 120, "100"]],
    }
    series_list = [{"__name__": "short_metric", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"short_metric": [short_series]})

    reports = detect_anomalies(prom, app_config, start, end)
    assert reports == []


# ── detect_anomalies – node mode (parallel) ───────────────────────────────────

def test_detect_anomalies_node_mode_uses_series_discovery(app_config, time_range):
    """必須呼叫 list_series_for_node，不應呼叫 list_metric_names"""
    start, end = time_range
    app_config.target_node = "worker-01"
    app_config.scan_workers = 2

    spike = make_spike_series("node_cpu", baseline=5.0, spike_value=999.0, start=start)
    series_list = [{"__name__": "node_cpu", "instance": "worker-01"}]
    prom = _make_node_prom_mock(series_list, {"node_cpu": [spike]})

    reports = detect_anomalies(prom, app_config, start, end)

    prom.list_series_for_node.assert_called_once_with("instance", "worker-01", start, end)
    prom.list_metric_names.assert_not_called()
    assert len(reports) == 1
    assert reports[0].metric_name == "node_cpu"


def test_detect_anomalies_node_mode_deduplicates_metric_names(app_config, time_range):
    """series 探索可能回傳同一 metric 多次（不同 label 組合），應去重"""
    start, end = time_range
    app_config.target_node = "worker-01"
    app_config.scan_workers = 2

    series_list = [
        {"__name__": "node_cpu", "instance": "worker-01", "cpu": "0"},
        {"__name__": "node_cpu", "instance": "worker-01", "cpu": "1"},
        {"__name__": "node_memory", "instance": "worker-01"},
    ]
    prom = _make_node_prom_mock(series_list, {})

    detect_anomalies(prom, app_config, start, end)

    call_queries = [c[0][0] for c in prom.query_range.call_args_list]
    assert len(call_queries) == 2  # node_cpu, node_memory


def test_detect_anomalies_no_series_returns_empty(app_config, time_range):
    """該節點在時間範圍內無任何 series 時，應回傳空列表"""
    start, end = time_range
    app_config.target_node = "worker-01"

    prom = _make_node_prom_mock([], {})

    reports = detect_anomalies(prom, app_config, start, end)
    assert reports == []


def test_detect_anomalies_node_mode_parallel_finds_anomalies(app_config, time_range):
    """並行模式應正確找出多個異常"""
    start, end = time_range
    app_config.target_node = "worker-01"
    app_config.scan_workers = 4

    spikes = {
        f"metric_{i}": [make_spike_series(f"metric_{i}", spike_value=999.0, start=start)]
        for i in range(5)
    }
    series_list = [{"__name__": k, "instance": "worker-01"} for k in spikes]
    prom = _make_node_prom_mock(series_list, spikes)

    reports = detect_anomalies(prom, app_config, start, end)

    assert len(reports) == 5


# ── format_anomaly_summary ────────────────────────────────────────────────────

def test_format_anomaly_summary_contains_metric_name(time_range):
    start, end = time_range
    reports = [
        AnomalyReport(
            metric_name="cpu_usage",
            labels={"instance": "server01"},
            severity=5.2,
            anomaly_time=start + 1800,
            baseline_mean=10.0,
            baseline_std=1.0,
            peak_value=60.0,
            metric_type="gauge",
            metric_help="CPU utilization",
        )
    ]
    summary = format_anomaly_summary(reports, start, end)

    assert "cpu_usage" in summary
    assert "server01" in summary
    assert "5.2" in summary
    assert "60.0" in summary


def test_format_anomaly_summary_empty(time_range):
    start, end = time_range
    summary = format_anomaly_summary([], start, end)
    assert "0 個" in summary
