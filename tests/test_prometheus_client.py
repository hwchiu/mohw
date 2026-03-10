import pytest
from unittest.mock import patch, MagicMock
from prometheus_client import PrometheusClient
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


def test_check_connection_fallback(client):
    """/-/healthy 拋出例外時改試 /api/v1/query"""
    query_ok = MagicMock()
    query_ok.status_code = 200

    responses = [Exception("not found"), query_ok]
    with patch.object(client.session, "get", side_effect=responses):
        assert client.check_connection() is True


def test_check_connection_failure(client):
    with patch.object(client.session, "get", side_effect=Exception("connection refused")):
        assert client.check_connection() is False
