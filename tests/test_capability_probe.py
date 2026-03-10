import pytest
from unittest.mock import patch, MagicMock
from capability_probe import probe_model_capability
from tests.conftest import llm_text_response, llm_tool_call_response


def _mock_post(responses: list):
    """side_effect 依序回傳不同的 LLM 回應"""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    call_iter = iter(responses)

    def side_effect(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = next(call_iter)
        return resp

    return side_effect


# ── Mode override ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("override", ["a", "b", "c"])
def test_mode_override_skips_probe(app_config, override):
    app_config.mode_override = override
    with patch("requests.post") as mock_post:
        result = probe_model_capability(app_config)
    assert result == override
    mock_post.assert_not_called()


# ── Mode A：multi-round tool calling ─────────────────────────────────────────

def test_probe_returns_mode_a_when_multi_round_supported(app_config):
    # Round 1：回傳 tool call（基本 tool use 測試）
    round1 = llm_tool_call_response("get_metric_value", {"metric_name": "cpu_usage"}, "call_1")
    # Round 2：給了工具結果後，再次回傳 tool call（多輪測試）
    round2 = llm_tool_call_response("get_metric_value", {"metric_name": "memory_usage"}, "call_2")

    with patch("requests.post", side_effect=_mock_post([round1, round2])):
        mode = probe_model_capability(app_config)

    assert mode == "a"


# ── Mode B：single-round tool calling ────────────────────────────────────────

def test_probe_returns_mode_b_when_single_round_only(app_config):
    # Round 1：回傳 tool call
    round1 = llm_tool_call_response("get_metric_value", {"metric_name": "cpu_usage"}, "call_1")
    # Round 2：給了工具結果後，回傳純文字（不繼續呼叫工具）
    round2 = llm_text_response("The CPU usage is 85.3%")

    with patch("requests.post", side_effect=_mock_post([round1, round2])):
        mode = probe_model_capability(app_config)

    assert mode == "b"


# ── Mode C：no tool calling ───────────────────────────────────────────────────

def test_probe_returns_mode_c_when_no_tool_call(app_config):
    # Model 直接回文字，不呼叫工具
    round1 = llm_text_response("I cannot use tools, but cpu_usage seems high.")

    with patch("requests.post", side_effect=_mock_post([round1])):
        mode = probe_model_capability(app_config)

    assert mode == "c"


def test_probe_returns_mode_c_on_api_error(app_config):
    with patch("requests.post", side_effect=Exception("connection refused")):
        mode = probe_model_capability(app_config)

    assert mode == "c"


def test_probe_returns_mode_c_on_http_error(app_config):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 500")

    with patch("requests.post", return_value=mock_resp):
        mode = probe_model_capability(app_config)

    assert mode == "c"
