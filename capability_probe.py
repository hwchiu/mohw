"""
Model 能力探針：自動測試 LLM 的 function calling 能力，決定運行模式。

模式：
  A - Full Agentic：model 自主多輪呼叫工具探索
  B - Semi Auto：由程式先篩選異常，model 做引導式深挖（單輪 tool use）
  C - Static：純文字，model 只做閱讀分析（不需要 tool use）
"""

import json
import requests
from rich.console import Console
from config import AppConfig

console = Console()

# 探針用的假工具
_PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_metric_value",
            "description": "Get the current value of a metric",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_name": {
                        "type": "string",
                        "description": "The metric name to query",
                    }
                },
                "required": ["metric_name"],
            },
        },
    }
]

_PROBE_MESSAGES = [
    {
        "role": "system",
        "content": (
            "You are a monitoring assistant. "
            "Use the available tools to answer questions."
        ),
    },
    {
        "role": "user",
        "content": "What is the current value of cpu_usage?",
    },
]

_PROBE_MESSAGES_ROUND2_TEMPLATE = [
    {
        "role": "system",
        "content": (
            "You are a monitoring assistant. "
            "Use the available tools to answer questions."
        ),
    },
    {
        "role": "user",
        "content": (
            "First check cpu_usage, then based on that result "
            "decide whether you also need to check memory_usage."
        ),
    },
]


def _call_llm(config: AppConfig, messages: list, tools: list = None) -> dict:
    payload = {
        "model": config.llm.model_id,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(
        config.llm.chat_endpoint,
        headers={
            "Authorization": f"Bearer {config.llm.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.llm.timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _has_tool_call(response: dict) -> bool:
    try:
        choice = response["choices"][0]
        return choice.get("finish_reason") == "tool_calls" or bool(
            choice.get("message", {}).get("tool_calls")
        )
    except (KeyError, IndexError):
        return False


def probe_model_capability(config: AppConfig) -> str:
    """
    探測 model 的能力，回傳 'a', 'b', 或 'c'。
    """
    if config.mode_override:
        mode = config.mode_override.lower()
        console.print(f"[yellow]已強制指定模式: {mode.upper()}[/yellow]")
        return mode

    console.print("[cyan]正在探測 model 能力...[/cyan]")

    # Test 1：是否支援 function calling
    try:
        resp1 = _call_llm(config, _PROBE_MESSAGES, tools=_PROBE_TOOLS)
        supports_tool_call = _has_tool_call(resp1)
    except Exception as e:
        console.print(f"[yellow]Tool call 測試失敗: {e}，降級為 C 模式[/yellow]")
        return "c"

    if not supports_tool_call:
        console.print(
            "[yellow]Model 不支援 function calling → 使用 C 模式（靜態分析）[/yellow]"
        )
        return "c"

    # Test 2：多輪推理（給工具結果，看是否繼續呼叫第二個工具）
    try:
        tool_call = resp1["choices"][0]["message"]["tool_calls"][0]
        tool_call_id = tool_call["id"]

        round2_messages = _PROBE_MESSAGES_ROUND2_TEMPLATE + [
            resp1["choices"][0]["message"],  # assistant 的 tool call
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps({"cpu_usage": 85.3}),
            },
        ]
        resp2 = _call_llm(config, round2_messages, tools=_PROBE_TOOLS)
        supports_multi_round = _has_tool_call(resp2)
    except Exception:
        supports_multi_round = False

    if supports_multi_round:
        console.print(
            "[green]Model 支援多輪 tool calling → 使用 A 模式（Full Agentic）[/green]"
        )
        return "a"
    else:
        console.print(
            "[cyan]Model 支援單輪 tool calling → 使用 B 模式（Semi Auto）[/cyan]"
        )
        return "b"
