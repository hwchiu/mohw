import numpy as np
from dataclasses import dataclass
from typing import Optional
from rich.console import Console
from config import AppConfig
from prometheus_client import PrometheusClient

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


def _extract_values(series: list[dict]) -> list[float]:
    """從 Prometheus result 中取出數值序列"""
    return [float(v[1]) for v in series.get("values", []) if v[1] != "NaN"]


def detect_anomalies(
    prom: PrometheusClient,
    config: AppConfig,
    start: float,
    end: float,
    step: int = 60,
    progress_callback=None,
) -> list[AnomalyReport]:
    """
    掃描所有 metrics，找出在 [start, end] 時間範圍內有異常的 metrics。
    使用 z-score 判斷：mean + sigma * std 為異常閾值。

    回傳按嚴重度排序的 AnomalyReport 列表。
    """
    sigma = config.anomaly_sigma_threshold
    max_scan = config.prometheus.max_metrics_scan

    # 取得 metadata（help text 與 type）
    try:
        metadata = prom.get_metadata()
    except Exception:
        metadata = {}

    all_metrics = prom.list_metric_names()

    # 過濾掉通常不代表系統狀態的 metrics（純計數器中繼資料類）
    skip_prefixes = ("scrape_", "up", "ALERTS", "go_info", "process_start_time")
    filtered = [
        m for m in all_metrics
        if not any(m.startswith(p) for p in skip_prefixes)
    ][:max_scan]

    console.print(
        f"[dim]掃描 {len(filtered)} 個 metrics 中的異常...[/dim]"
    )

    reports: list[AnomalyReport] = []

    for i, metric_name in enumerate(filtered):
        if progress_callback:
            progress_callback(i, len(filtered), metric_name)

        try:
            results = prom.query_range(metric_name, start, end, step)
        except Exception:
            continue

        for series in results:
            values = _extract_values(series)
            if len(values) < 5:
                continue

            arr = np.array(values, dtype=float)
            if np.all(arr == arr[0]):
                continue  # 完全平坦，無異常

            mean = arr.mean()
            std = arr.std()
            if std == 0:
                continue

            zscores = np.abs((arr - mean) / std)
            max_zscore = zscores.max()

            if max_zscore < sigma:
                continue

            peak_idx = int(zscores.argmax())
            # 計算 anomaly_time（對應的 timestamp）
            ts_list = [v[0] for v in series.get("values", [])]
            anomaly_ts = float(ts_list[peak_idx]) if peak_idx < len(ts_list) else start

            meta_list = metadata.get(metric_name, [])
            mtype = meta_list[0].get("type", "unknown") if meta_list else "unknown"
            mhelp = meta_list[0].get("help", "") if meta_list else ""

            reports.append(
                AnomalyReport(
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
            )

    reports.sort(key=lambda r: -r.severity)
    return reports[: config.max_anomalies_to_report]


def format_anomaly_summary(reports: list[AnomalyReport], start: float, end: float) -> str:
    """將異常報告格式化成給 LLM 的文字摘要"""
    from datetime import datetime, timezone

    def fmt_ts(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        f"=== Prometheus 異常分析報告 ===",
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
