import pytest
from unittest.mock import MagicMock, patch
from anomaly_detector import detect_anomalies, format_anomaly_summary, AnomalyReport
from tests.conftest import (
    make_flat_series,
    make_spike_series,
    prometheus_range_response,
    prometheus_labels_response,
    prometheus_metadata_response,
)


# ── detect_anomalies ──────────────────────────────────────────────────────────

def _make_prom_mock(metric_names: list, series_map: dict, metadata: dict = None):
    """建立一個模擬的 PrometheusClient。
    series_map: {metric_name: [series, ...]}
    """
    mock = MagicMock()
    mock.list_metric_names.return_value = metric_names
    mock.get_metadata.return_value = metadata or {}
    mock.query_range.side_effect = lambda name, *a, **kw: series_map.get(name, [])
    return mock


def test_detect_anomalies_finds_spike(app_config, time_range):
    start, end = time_range
    spike = make_spike_series("cpu_usage", baseline=10.0, spike_value=200.0, start=start)
    prom = _make_prom_mock(["cpu_usage"], {"cpu_usage": [spike]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert len(reports) == 1
    assert reports[0].metric_name == "cpu_usage"
    assert reports[0].severity > app_config.anomaly_sigma_threshold


def test_detect_anomalies_ignores_flat_series(app_config, time_range):
    start, end = time_range
    flat = make_flat_series("up", value=1.0, start=start)
    prom = _make_prom_mock(["up"], {"up": [flat]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports == []


def test_detect_anomalies_ignores_constant_zero(app_config, time_range):
    start, end = time_range
    flat = make_flat_series("no_data_metric", value=0.0, start=start)
    prom = _make_prom_mock(["no_data_metric"], {"no_data_metric": [flat]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports == []


def test_detect_anomalies_skips_scrape_metrics(app_config, time_range):
    start, end = time_range
    spike = make_spike_series("scrape_duration_seconds", baseline=0.01,
                               spike_value=100.0, start=start)
    prom = _make_prom_mock(["scrape_duration_seconds"], {"scrape_duration_seconds": [spike]})

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports == []


def test_detect_anomalies_sorted_by_severity(app_config, time_range):
    start, end = time_range
    low_spike = make_spike_series("metric_a", baseline=10.0, spike_value=30.0, start=start)
    high_spike = make_spike_series("metric_b", baseline=10.0, spike_value=500.0, start=start)
    prom = _make_prom_mock(
        ["metric_a", "metric_b"],
        {"metric_a": [low_spike], "metric_b": [high_spike]},
    )

    reports = detect_anomalies(prom, app_config, start, end)

    severities = [r.severity for r in reports]
    assert severities == sorted(severities, reverse=True)


def test_detect_anomalies_respects_max_limit(app_config, time_range):
    start, end = time_range
    app_config.max_anomalies_to_report = 2

    metrics = {f"metric_{i}": [make_spike_series(f"metric_{i}", spike_value=999.0, start=start)]
               for i in range(10)}
    prom = _make_prom_mock(list(metrics.keys()), metrics)

    reports = detect_anomalies(prom, app_config, start, end)

    assert len(reports) <= 2


def test_detect_anomalies_includes_metadata(app_config, time_range):
    start, end = time_range
    spike = make_spike_series("node_cpu_seconds_total", baseline=5.0,
                               spike_value=999.0, start=start)
    meta = {
        "node_cpu_seconds_total": [{"type": "counter", "help": "CPU seconds total"}]
    }
    prom = _make_prom_mock(["node_cpu_seconds_total"], {"node_cpu_seconds_total": [spike]},
                           metadata=meta)

    reports = detect_anomalies(prom, app_config, start, end)

    assert reports[0].metric_type == "counter"
    assert "CPU" in reports[0].metric_help


def test_detect_anomalies_handles_query_error(app_config, time_range):
    start, end = time_range
    prom = MagicMock()
    prom.list_metric_names.return_value = ["bad_metric"]
    prom.get_metadata.return_value = {}
    prom.query_range.side_effect = Exception("timeout")

    # 不應該 raise，只是跳過該 metric
    reports = detect_anomalies(prom, app_config, start, end)
    assert reports == []


def test_detect_anomalies_too_few_points(app_config, time_range):
    start, end = time_range
    # 只有 3 個點，應被過濾
    short_series = {
        "metric": {"__name__": "short_metric"},
        "values": [[start, "100"], [start + 60, "1"], [start + 120, "100"]],
    }
    prom = _make_prom_mock(["short_metric"], {"short_metric": [short_series]})

    reports = detect_anomalies(prom, app_config, start, end)
    assert reports == []


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
