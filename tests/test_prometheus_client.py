import pytest
from unittest.mock import patch, MagicMock
from prometheus_client import PrometheusClient, apply_node_filter
from tests.conftest import (
    prometheus_range_response,
    prometheus_labels_response,
    prometheus_metadata_response,
    make_flat_series,
)


@pytest.fixture
def client(prom_config):
    return PrometheusClient(prom_config)


# ── list_metric_names ─────────────────────────────────────────────────────────

def test_list_metric_names_success(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = prometheus_labels_response(["cpu_usage", "memory_usage"])
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client.session, "get", return_value=mock_resp):
        names = client.list_metric_names()

    assert names == ["cpu_usage", "memory_usage"]


def test_list_metric_names_sorted(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = prometheus_labels_response(["z_metric", "a_metric", "m_metric"])
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client.session, "get", return_value=mock_resp):
        names = client.list_metric_names()

    assert names == sorted(names)


def test_list_metric_names_prometheus_error(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "error", "error": "something went wrong"}
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client.session, "get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="something went wrong"):
            client.list_metric_names()


# ── query_range ───────────────────────────────────────────────────────────────

def test_query_range_success(client):
    series = make_flat_series("cpu_usage", value=50.0)
    mock_resp = MagicMock()
    mock_resp.json.return_value = prometheus_range_response([series])
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client.session, "get", return_value=mock_resp):
        result = client.query_range("cpu_usage", 1000.0, 2000.0)

    assert len(result) == 1
    assert result[0]["metric"]["__name__"] == "cpu_usage"


def test_query_range_empty(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = prometheus_range_response([])
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client.session, "get", return_value=mock_resp):
        result = client.query_range("nonexistent", 1000.0, 2000.0)

    assert result == []


def test_query_range_error_status(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "error", "error": "bad query"}
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client.session, "get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="bad query"):
            client.query_range("bad{query", 1000.0, 2000.0)


# ── check_connection ──────────────────────────────────────────────────────────

def test_check_connection_healthy(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch.object(client.session, "get", return_value=mock_resp):
        assert client.check_connection() is True


def test_check_connection_fallback_on_404(client):
    """/-/healthy 回傳 404（非 exception）時，應 fallback 試 /api/v1/query"""
    healthy_404 = MagicMock()
    healthy_404.status_code = 404

    query_ok = MagicMock()
    query_ok.status_code = 200

    responses = [healthy_404, query_ok]
    with patch.object(client.session, "get", side_effect=responses):
        assert client.check_connection() is True


def test_check_connection_fallback_on_exception(client):
    """/-/healthy 拋出例外時，應 fallback 試 /api/v1/query"""
    query_ok = MagicMock()
    query_ok.status_code = 200

    with patch.object(client.session, "get", side_effect=[Exception("refused"), query_ok]):
        assert client.check_connection() is True


def test_check_connection_both_fail(client):
    with patch.object(client.session, "get", side_effect=Exception("connection refused")):
        assert client.check_connection() is False


def test_check_connection_both_non200(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch.object(client.session, "get", return_value=mock_resp):
        assert client.check_connection() is False


# ── diagnose_connection ───────────────────────────────────────────────────────

def test_diagnose_connection_healthy_ok(client):
    ok = MagicMock()
    ok.status_code = 200

    with patch.object(client.session, "get", return_value=ok):
        diag = client.diagnose_connection()

    assert diag["connected"] is True
    assert diag["probes"]["/-/healthy"]["ok"] is True


def test_diagnose_connection_shows_fallback_success(client):
    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_200 = MagicMock()
    resp_200.status_code = 200

    with patch.object(client.session, "get", side_effect=[resp_404, resp_200]):
        diag = client.diagnose_connection()

    assert diag["connected"] is True
    assert diag["probes"]["/-/healthy"]["ok"] is False
    assert diag["probes"]["/api/v1/query"]["ok"] is True


def test_diagnose_connection_records_error_message(client):
    with patch.object(client.session, "get", side_effect=Exception("Name resolution failed")):
        diag = client.diagnose_connection()

    assert diag["connected"] is False
    for probe in diag["probes"].values():
        assert "Name resolution failed" in (probe["error"] or "")


def test_diagnose_connection_records_status_codes(client):
    resp_503 = MagicMock()
    resp_503.status_code = 503

    with patch.object(client.session, "get", return_value=resp_503):
        diag = client.diagnose_connection()

    for probe in diag["probes"].values():
        assert probe["status_code"] == 503
        assert probe["ok"] is False


# ── apply_node_filter ──────────────────────────────────────────────────────────

def test_apply_node_filter_bare_metric():
    result = apply_node_filter("cpu_usage", "instance", "worker-01")
    assert result == 'cpu_usage{instance="worker-01"}'


def test_apply_node_filter_with_existing_labels():
    result = apply_node_filter('cpu_usage{job="node"}', "instance", "worker-01")
    assert 'instance="worker-01"' in result
    assert 'job="node"' in result


def test_apply_node_filter_function_call():
    result = apply_node_filter("rate(cpu_usage[5m])", "instance", "worker-01")
    assert 'instance="worker-01"' in result
    assert "rate(" in result


def test_apply_node_filter_empty_value():
    """node 未設定時，query 不應被修改"""
    original = "cpu_usage"
    result = apply_node_filter(original, "instance", "")
    assert result == original
