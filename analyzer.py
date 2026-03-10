"""
三種分析模式的實作。
每個 mode function 接受相同的參數，回傳分析結果字串。
"""

import json
import requests
from datetime import datetime, timezone
from rich.console import Console
from config import AppConfig
from prometheus_client import PrometheusClient
from tools import ToolExecutor, TOOL_SCHEMAS
from anomaly_detector import detect_anomalies, format_anomaly_summary

console = Console()

_SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer) and systems analyst.
Your task is to analyze Prometheus metrics data to identify what caused system instability during a given time window.

Guidelines:
- Start by calling detect_anomalies to get an overview of what changed
- Follow causal chains: correlate timing of anomalies across different metrics
- Distinguish root causes from symptoms (e.g., high CPU might be a symptom of a memory leak causing GC pressure)
- Be specific about timestamps and affected components
- Provide a concise root cause analysis with confidence level

Output your final analysis in this structure:
1. **Root Cause** (most likely cause)
2. **Evidence** (which metrics, which timestamps)
3. **Propagation Chain** (how the problem spread)
4. **Confidence** (high/medium/low + reasoning)
5. **Suggested Next Steps** (what to investigate further)
"""


def _call_llm(config: AppConfig, messages: list, tools: list = None) -> dict:
    payload = {
        "model": config.llm.model_id,
        "messages": messages,
        "max_tokens": config.llm.max_tokens,
        "temperature": 0.1,
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


def _fmt_range(start: float, end: float) -> str:
    def f(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"{f(start)} ~ {f(end)}"


# ──────────────────────────────────────────
# Mode A：Full Agentic
# ──────────────────────────────────────────

def run_mode_a(
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
    max_iterations: int = 10,
) -> str:
    console.print("[bold green]▶ Mode A: Full Agentic 分析[/bold green]")
    executor = ToolExecutor(prom, config, start, end)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Please analyze the Prometheus metrics for the time range {_fmt_range(start, end)} "
                "and identify what caused system instability. "
                "Use the available tools to explore metrics autonomously."
            ),
        },
    ]

    for iteration in range(max_iterations):
        console.print(f"[dim]  迭代 {iteration + 1}/{max_iterations}...[/dim]")
        response = _call_llm(config, messages, tools=TOOL_SCHEMAS)
        choice = response["choices"][0]
        message = choice["message"]
        messages.append(message)

        finish_reason = choice.get("finish_reason")

        if finish_reason == "stop" or not message.get("tool_calls"):
            # Model 完成分析
            return message.get("content", "(no response)")

        # 執行所有 tool calls
        for tool_call in message.get("tool_calls", []):
            fn_name = tool_call["function"]["name"]
            try:
                args = json.loads(tool_call["function"].get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            console.print(f"[dim]  → 呼叫工具: {fn_name}({args})[/dim]")
            result = executor.execute(fn_name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result,
            })

    # 超過最大迭代次數，要求 model 給出結論
    messages.append({
        "role": "user",
        "content": "Based on all the information gathered, please provide your final root cause analysis now.",
    })
    response = _call_llm(config, messages)
    return response["choices"][0]["message"].get("content", "(no response)")


# ──────────────────────────────────────────
# Mode B：Semi Auto
# ──────────────────────────────────────────

def run_mode_b(
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
) -> str:
    console.print("[bold cyan]▶ Mode B: Semi Auto 分析[/bold cyan]")

    # 程式先跑 detect_anomalies
    console.print("[dim]  程式預掃描異常 metrics...[/dim]")
    reports = detect_anomalies(prom, config, start, end)
    anomaly_summary = format_anomaly_summary(reports, start, end)

    executor = ToolExecutor(prom, config, start, end)

    # 給 model 異常摘要，讓它決定要深挖哪些 metrics（單輪 tool use）
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Time range: {_fmt_range(start, end)}\n\n"
                f"The following anomalies were automatically detected:\n\n"
                f"{anomaly_summary}\n\n"
                "Based on these anomalies, use the available tools to investigate the most suspicious metrics "
                "and provide a root cause analysis."
            ),
        },
    ]

    response = _call_llm(config, messages, tools=TOOL_SCHEMAS)
    choice = response["choices"][0]
    message = choice["message"]
    messages.append(message)

    # 執行 tool calls（單輪）
    if message.get("tool_calls"):
        for tool_call in message.get("tool_calls", []):
            fn_name = tool_call["function"]["name"]
            try:
                args = json.loads(tool_call["function"].get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            console.print(f"[dim]  → 呼叫工具: {fn_name}({args})[/dim]")
            result = executor.execute(fn_name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result,
            })

        # 最終結論
        messages.append({
            "role": "user",
            "content": "Now provide your final root cause analysis based on all the information.",
        })
        final = _call_llm(config, messages)
        return final["choices"][0]["message"].get("content", "(no response)")
    else:
        return message.get("content", "(no response)")


# ──────────────────────────────────────────
# Mode C：Static Analysis
# ──────────────────────────────────────────

def run_mode_c(
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
) -> str:
    console.print("[bold yellow]▶ Mode C: Static Analysis 分析[/bold yellow]")

    console.print("[dim]  收集異常資料...[/dim]")
    reports = detect_anomalies(prom, config, start, end)
    anomaly_summary = format_anomaly_summary(reports, start, end)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Time range: {_fmt_range(start, end)}\n\n"
                f"{anomaly_summary}\n\n"
                "Based solely on the above data, provide a detailed root cause analysis "
                "of what caused the system instability."
            ),
        },
    ]

    response = _call_llm(config, messages)
    return response["choices"][0]["message"].get("content", "(no response)")
