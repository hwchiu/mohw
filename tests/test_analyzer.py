import pytest
from unittest.mock import patch, MagicMock, call
from analyzer import run_mode_a, run_mode_b, run_mode_c
from tests.conftest import (
    llm_text_response,
    llm_tool_call_response,
    make_spike_series,
)

FINAL_ANALYSIS = (
    "**Root Cause**: Memory leak in api-server\n"
    "**Evidence**: node_memory_MemAvailable dropped at 14:30\n"
    "**Propagation Chain**: Memory leak → OOM kill → Pod restart\n"
    "**Confidence**: High\n"
    "**Suggested Next Steps**: Check heap dump"
)


def _prom_mock(time_range):
    start, end = time_range
    prom = MagicMock()
    spike = make_spike_series("cpu_usage", baseline=20.0, spike_value=90.0, start=start)
    prom.list_metric_names.return_value = ["cpu_usage"]
    prom.get_metadata.return_value = {}
    prom.query_range.return_value = [spike]
    return prom


def _mock_post_seq(responses: list):
    call_iter = iter(responses)

    def side_effect(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = next(call_iter)
        return resp

    return side_effect


# ── Mode A ────────────────────────────────────────────────────────────────────

def test_mode_a_completes_without_tool_calls(app_config, time_range):
    """Model 直接回答，不呼叫任何工具"""
    prom = _prom_mock(time_range)

    with patch("requests.post", side_effect=_mock_post_seq([
        llm_text_response(FINAL_ANALYSIS)
    ])):
        result = run_mode_a(prom, app_config, *time_range)

    assert "Root Cause" in result


def test_mode_a_executes_tool_calls_then_concludes(app_config, time_range):
    """Model 先呼叫 detect_anomalies，再給出結論"""
    prom = _prom_mock(time_range)

    responses = [
        llm_tool_call_response("detect_anomalies", {}, "call_1"),
        llm_text_response(FINAL_ANALYSIS),
    ]

    with patch("requests.post", side_effect=_mock_post_seq(responses)):
        result = run_mode_a(prom, app_config, *time_range)

    assert "Root Cause" in result


def test_mode_a_handles_multiple_tool_rounds(app_config, time_range):
    """Model 呼叫多輪工具"""
    prom = _prom_mock(time_range)

    responses = [
        llm_tool_call_response("detect_anomalies", {}, "call_1"),
        llm_tool_call_response("query_metric", {"metric_name": "cpu_usage"}, "call_2"),
        llm_text_response(FINAL_ANALYSIS),
    ]

    with patch("requests.post", side_effect=_mock_post_seq(responses)):
        result = run_mode_a(prom, app_config, *time_range, max_iterations=5)

    assert "Root Cause" in result


def test_mode_a_forces_conclusion_after_max_iterations(app_config, time_range):
    """超過 max_iterations 後強制取得結論"""
    prom = _prom_mock(time_range)

    # 前 N 次都是 tool call，最後一次是文字
    tool_responses = [
        llm_tool_call_response("list_metrics", {}, f"call_{i}")
        for i in range(3)
    ]
    tool_responses.append(llm_text_response(FINAL_ANALYSIS))

    with patch("requests.post", side_effect=_mock_post_seq(tool_responses)):
        result = run_mode_a(prom, app_config, *time_range, max_iterations=3)

    assert result  # 不應是空字串


def test_mode_a_handles_llm_error(app_config, time_range):
    prom = _prom_mock(time_range)

    with patch("requests.post", side_effect=Exception("LLM API down")):
        with pytest.raises(Exception, match="LLM API down"):
            run_mode_a(prom, app_config, *time_range)


# ── Mode B ────────────────────────────────────────────────────────────────────

def test_mode_b_with_tool_call(app_config, time_range):
    prom = _prom_mock(time_range)

    responses = [
        llm_tool_call_response("query_metric", {"metric_name": "cpu_usage"}, "call_1"),
        llm_text_response(FINAL_ANALYSIS),
    ]

    with patch("requests.post", side_effect=_mock_post_seq(responses)):
        result = run_mode_b(prom, app_config, *time_range)

    assert "Root Cause" in result


def test_mode_b_without_tool_call(app_config, time_range):
    """Model 直接根據異常摘要回答，不呼叫工具"""
    prom = _prom_mock(time_range)

    with patch("requests.post", side_effect=_mock_post_seq([
        llm_text_response(FINAL_ANALYSIS)
    ])):
        result = run_mode_b(prom, app_config, *time_range)

    assert "Root Cause" in result


# ── Mode C ────────────────────────────────────────────────────────────────────

def test_mode_c_returns_analysis(app_config, time_range):
    prom = _prom_mock(time_range)

    with patch("requests.post", side_effect=_mock_post_seq([
        llm_text_response(FINAL_ANALYSIS)
    ])):
        result = run_mode_c(prom, app_config, *time_range)

    assert "Root Cause" in result


def test_mode_c_no_anomalies(app_config, time_range):
    """即使沒有異常也能正常完成分析"""
    start, end = time_range
    prom = MagicMock()
    prom.list_metric_names.return_value = []
    prom.get_metadata.return_value = {}
    prom.query_range.return_value = []

    with patch("requests.post", side_effect=_mock_post_seq([
        llm_text_response("No anomalies detected in the given time range.")
    ])):
        result = run_mode_c(prom, app_config, start, end)

    assert result  # 應有輸出
