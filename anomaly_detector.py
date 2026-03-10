import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional
from rich.console import Console
from config import AppConfig
from prometheus_client import PrometheusClient, apply_node_filter

console = Console()


@dataclass
class AnomalyReport:
    metric_name: str
    labels: dict
    severity: float          # max z-score
    anomaly_time: float      # unix timestamp of peak anomaly
    baseline_mean: float
    baseline_std: float
    peak_value: float
    metric_type: str = "unknown"
    metric_help: str = ""
    values: list = None      # 原始時序，供 model 深挖用


def _extract_values(series: dict) -> list[float]:
    """從 Prometheus result 中取出數值序列"""
    return [float(v[1]) for v in series.get("values", []) if v[1] != "NaN"]


def _analyze_series(
    series: dict,
    metric_name: str,
    sigma: float,
    metadata: dict,
) -> Optional[AnomalyReport]:
    """
    對單一 time series 執行 z-score 異常偵測。
    有異常則回傳 AnomalyReport，否則回傳 None。
    """
    values = _extract_values(series)
    if len(values) < 5:
        return None

    arr = np.array(values, dtype=float)
    if np.all(arr == arr[0]):
        return None  # 完全平坦，無異常

    mean = arr.mean()
    std = arr.std()
    if std == 0:
        return None

    zscores = np.abs((arr - mean) / std)
    max_zscore = zscores.max()
    if max_zscore < sigma:
        return None

    peak_idx = int(zscores.argmax())
    ts_list = [v[0] for v in series.get("values", [])]
    anomaly_ts = float(ts_list[peak_idx]) if peak_idx < len(ts_list) else 0.0

    meta_list = metadata.get(metric_name, [])
    mtype = meta_list[0].get("type", "unknown") if meta_list else "unknown"
    mhelp = meta_list[0].get("help", "") if meta_list else ""

    return AnomalyReport(
        metric_name=metric_name,
        labels=series.get("metric", {}),
        severity=round(float(max_zscore), 2),
        anomaly_time=anomaly_ts,
        baseline_mean=round(float(mean), 4),
        baseline_std=round(float(std), 4),
        peak_value=round(float(arr[peak_idx]), 4),
        metric_type=mtype,
        metric_help=mhelp,
        values=series.get("values", []),
    )


def _scan_metric(
    metric_name: str,
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
    step: int,
    metadata: dict,
) -> list[AnomalyReport]:
    """
    查詢單一 metric 的時序資料並掃描異常。
    給 ThreadPoolExecutor 用的 worker 函式。
    """
    query = apply_node_filter(metric_name, config.node_label, config.target_node or "")
    try:
        results = prom.query_range(query, start, end, step)
    except Exception:
        return []

    reports = []
    for series in results:
        r = _analyze_series(series, metric_name, config.anomaly_sigma_threshold, metadata)
        if r:
            reports.append(r)
    return reports


# Prefixes that are typically metadata/infrastructure noise, not system health signals
_SKIP_PREFIXES = ("scrape_", "ALERTS", "go_info", "process_start_time")


def detect_anomalies(
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
    step: int = 60,
    progress_callback=None,
) -> list[AnomalyReport]:
    """
    使用 /api/v1/series 找出目標節點在時間範圍內的所有 metrics，
    再以 ThreadPoolExecutor 並行掃描，找出 z-score 異常。

    config.target_node 必須已設定（由 CLI 保證）。
    回傳按嚴重度排序的 AnomalyReport 列表。
    """
    # 取得 metadata（help text 與 type），失敗時降級為空
    try:
        metadata = prom.get_metadata()
    except Exception:
        metadata = {}

    # ── Step 1: 透過 series API 發現節點的 metrics ──────────────────────────
    console.print(
        f"[dim]📡 探索節點 '{config.target_node}' 的 metrics（/api/v1/series）...[/dim]"
    )
    series_list = prom.list_series_for_node(
        config.node_label,
        config.target_node,
        start,
        end,
    )
    metric_names = sorted(set(
        s["__name__"] for s in series_list if "__name__" in s
    ))

    # 過濾噪音 metrics
    filtered = [
        m for m in metric_names
        if not any(m.startswith(p) for p in _SKIP_PREFIXES)
    ]

    console.print(
        f"[dim]🔍 發現 {len(filtered)} 個 metrics，啟動 {config.scan_workers} workers 並行掃描...[/dim]"
    )

    # ── Step 2: 並行掃描 ────────────────────────────────────────────────────
    reports = _parallel_scan(filtered, prom, config, start, end, step, metadata, progress_callback)

    reports.sort(key=lambda r: -r.severity)
    return reports[: config.max_anomalies_to_report]


def _parallel_scan(
    metric_names: list[str],
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
    step: int,
    metadata: dict,
    progress_callback,
) -> list[AnomalyReport]:
    """並行掃描，使用 ThreadPoolExecutor"""
    reports: list[AnomalyReport] = []
    completed = 0
    total = len(metric_names)

    with ThreadPoolExecutor(max_workers=config.scan_workers) as executor:
        future_to_metric = {
            executor.submit(
                _scan_metric, m, prom, config, start, end, step, metadata
            ): m
            for m in metric_names
        }
        for future in as_completed(future_to_metric):
            completed += 1
            if progress_callback:
                progress_callback(completed, total, future_to_metric[future])
            try:
                reports.extend(future.result())
            except Exception:
                pass

    return reports


def format_anomaly_summary(reports: list[AnomalyReport], start: float, end: float) -> str:
    """將異常報告格式化成給 LLM 的文字摘要"""
    from datetime import datetime, timezone

    def fmt_ts(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "=== Prometheus 異常分析報告 ===",
        f"分析時間範圍：{fmt_ts(start)} ~ {fmt_ts(end)}",
        f"偵測到 {len(reports)} 個異常 metrics（依嚴重度排序）",
        "",
    ]

    for i, r in enumerate(reports, 1):
        label_str = ", ".join(f'{k}="{v}"' for k, v in r.labels.items() if k != "__name__")
        lines.append(
            f"[{i}] {r.metric_name}"
            + (f"{{{label_str}}}" if label_str else "")
        )
        lines.append(f"    類型: {r.metric_type}  |  嚴重度(z-score): {r.severity}")
        if r.metric_help:
            lines.append(f"    說明: {r.metric_help}")
        lines.append(
            f"    基線: mean={r.baseline_mean}, std={r.baseline_std}"
            f"  →  峰值: {r.peak_value}  (發生於 {fmt_ts(r.anomaly_time)})"
        )
        lines.append("")

    return "\n".join(lines)
