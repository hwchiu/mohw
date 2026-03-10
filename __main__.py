#!/usr/bin/env python3
"""
prometheus-analyzer：利用 LLM 自動分析 Prometheus metrics，找出系統不穩定的根本原因。

用法範例：
  python __main__.py \
    --prometheus http://prometheus.internal:9090 \
    --start "2026-03-10 14:00:00" \
    --end "2026-03-10 15:00:00"

  # 指定 LLM endpoint（或透過 .env 設定 LLM_BASE_URL / LLM_MODEL_ID）
  python __main__.py \
    --prometheus http://prometheus.internal:9090 \
    --start "2026-03-10 14:00:00" \
    --end "2026-03-10 15:00:00" \
    --llm-url http://test.com \
    --model my-model

  # 強制指定模式（略過 capability probe）
  python __main__.py ... --mode c
"""

import sys
from typing import Optional
from datetime import datetime, timezone
import typer
from rich.console import Console
from rich.panel import Panel
from dateutil import parser as dateparser

from config import AppConfig, LLMConfig, PrometheusConfig
from prometheus_client import PrometheusClient
from capability_probe import probe_model_capability
from analyzer import run_mode_a, run_mode_b, run_mode_c

app = typer.Typer(help="Prometheus 智慧異常分析工具")
console = Console()


def _parse_time(s: str) -> float:
    """解析時間字串為 unix timestamp。支援 ISO 格式或 unix timestamp 數字。"""
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = dateparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        raise typer.BadParameter(f"無法解析時間格式：{s!r}，請使用 ISO 格式或 unix timestamp")


@app.command()
def analyze(
    prometheus: str = typer.Option(
        ...,
        "--prometheus", "-p",
        help="Prometheus base URL，例如 http://prometheus.internal:9090",
    ),
    start: str = typer.Option(
        ...,
        "--start", "-s",
        help="分析開始時間，例如 '2026-03-10 14:00:00' 或 unix timestamp",
    ),
    end: str = typer.Option(
        ...,
        "--end", "-e",
        help="分析結束時間，例如 '2026-03-10 15:00:00' 或 unix timestamp",
    ),
    node: Optional[str] = typer.Option(
        None,
        "--node", "-n",
        help="目標節點名稱（優先於 .env 的 TARGET_NODE）。"
             "例如 worker-01 或 192.168.1.10:9100。"
             "設定後所有 metrics 查詢都只針對此節點。",
    ),
    node_label: Optional[str] = typer.Option(
        None,
        "--node-label",
        help="Prometheus 中代表節點的 label 名稱（優先於 .env 的 NODE_LABEL）。"
             "node_exporter 環境通常是 instance；k8s 環境通常是 node。"
             "預設：instance",
    ),
    llm_url: Optional[str] = typer.Option(
        None,
        "--llm-url",
        help="LLM API base URL（優先於 .env 的 LLM_BASE_URL）",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model", "-m",
        help="Model ID（優先於 .env 的 LLM_MODEL_ID）",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="LLM API key（優先於 .env 的 LLM_API_KEY，預設：none）",
    ),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="強制指定模式：a（Full Agentic）/ b（Semi Auto）/ c（Static）。預設自動偵測。",
    ),
    sigma: float = typer.Option(
        2.5,
        "--sigma",
        help="異常偵測 z-score 閾值（預設 2.5，值越小偵測越敏感）",
    ),
    max_metrics: int = typer.Option(
        500,
        "--max-metrics",
        help="最多掃描幾個 metrics（預設 500）",
    ),
    max_anomalies: int = typer.Option(
        20,
        "--max-anomalies",
        help="最多回報幾個異常 metrics（預設 20）",
    ),
):
    """分析指定時間範圍內 Prometheus metrics 的異常，找出系統不穩定的根本原因。"""

    # ── 建立設定（CLI 參數優先，其次讀 .env，最後用預設值）──────
    base_llm = LLMConfig()
    base_app = AppConfig()
    llm_config = LLMConfig(
        base_url=llm_url or base_llm.base_url,
        model_id=model or base_llm.model_id,
        api_key=api_key or base_llm.api_key,
    )
    prom_config = PrometheusConfig(
        base_url=prometheus,
        max_metrics_scan=max_metrics,
    )
    config = AppConfig(
        llm=llm_config,
        prometheus=prom_config,
        anomaly_sigma_threshold=sigma,
        max_anomalies_to_report=max_anomalies,
        mode_override=mode,
        target_node=node or base_app.target_node,
        node_label=node_label or base_app.node_label,
    )

    # ── 解析時間 ──────────────────────────────
    try:
        start_ts = _parse_time(start)
        end_ts = _parse_time(end)
    except typer.BadParameter as e:
        console.print(f"[red]錯誤：{e}[/red]")
        raise typer.Exit(1)

    if end_ts <= start_ts:
        console.print("[red]錯誤：結束時間必須晚於開始時間[/red]")
        raise typer.Exit(1)

    # ── 顯示啟動資訊 ──────────────────────────
    from datetime import datetime
    def fmt_ts(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    console.print(
        Panel(
            f"[bold]Prometheus:[/bold] {prometheus}\n"
            f"[bold]時間範圍:[/bold] {fmt_ts(start_ts)} ~ {fmt_ts(end_ts)}\n"
            f"[bold]目標節點:[/bold] {config.target_node or '（未指定，分析全部節點）'}"
            + (f"  label={config.node_label}" if config.target_node else "") + "\n"
            f"[bold]LLM:[/bold] {llm_config.base_url} / {llm_config.model_id}\n"
            f"[bold]模式:[/bold] {'自動偵測' if not mode else mode.upper()}",
            title="[bold]Prometheus 智慧異常分析[/bold]",
        )
    )

    # ── 連線測試 ──────────────────────────────
    prom = PrometheusClient(prom_config)
    console.print("[cyan]測試 Prometheus 連線...[/cyan]")
    diag = prom.diagnose_connection()
    if not diag["connected"]:
        console.print(f"[red]無法連線至 Prometheus: {prometheus}[/red]")
        console.print("[yellow]診斷結果：[/yellow]")
        for name, r in diag["probes"].items():
            status = f"HTTP {r['status_code']}" if r["status_code"] else f"錯誤: {r['error']}"
            icon = "✓" if r["ok"] else "✗"
            console.print(f"  [{icon}] {name:20s}  {r['url']}")
            console.print(f"       └─ {status}")
        console.print(
            "\n[dim]排查建議：\n"
            "  1. 確認 URL 格式：http://<host>:<port>（不要加路徑）\n"
            "  2. 用 curl 確認可達：curl <prometheus_url>/-/healthy\n"
            "  3. 若在 Docker 內執行，host 不能是 localhost，改用主機 IP\n"
            "  4. 確認防火牆/網路政策允許此容器連至 Prometheus port[/dim]"
        )
        raise typer.Exit(1)
    # 顯示哪個端點成功
    for name, r in diag["probes"].items():
        if r["ok"]:
            console.print(f"[green]Prometheus 連線正常[/green] [dim]（via {name}）[/dim]")
            break

    # ── Capability Probe ──────────────────────
    selected_mode = probe_model_capability(config)

    # ── 執行分析 ──────────────────────────────
    console.rule("[bold]開始分析[/bold]")
    try:
        if selected_mode == "a":
            result = run_mode_a(prom, config, start_ts, end_ts)
        elif selected_mode == "b":
            result = run_mode_b(prom, config, start_ts, end_ts)
        else:
            result = run_mode_c(prom, config, start_ts, end_ts)
    except KeyboardInterrupt:
        console.print("\n[yellow]分析中斷[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[red]分析失敗：{e}[/red]")
        raise typer.Exit(1)

    # ── 輸出結果 ──────────────────────────────
    console.rule("[bold]分析結果[/bold]")
    console.print(Panel(result, title=f"[bold green]根本原因分析（Mode {selected_mode.upper()}）[/bold green]"))


if __name__ == "__main__":
    app()
