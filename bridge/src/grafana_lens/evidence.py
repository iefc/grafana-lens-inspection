"""Grafana 8.5 DataFrame 数值证据抽取与 Finding 回填。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from .errors import LensError
from .models import Finding, NumericEvidence


def _iso_time(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


def extract_numeric_evidence(raw: dict, panel: dict) -> list[NumericEvidence]:
    """从 Grafana 8.5 `/api/ds/query` 的 DataFrame JSON 抽取可追溯证据。"""
    target_expr = {
        str(target.get("refId", "A")): str(target.get("expr", ""))
        for target in panel.get("targets", [])
        if isinstance(target, dict)
    }
    evidence: list[NumericEvidence] = []
    for ref_id, result in (raw.get("results") or {}).items():
        expr = target_expr.get(str(ref_id), "")
        for frame in result.get("frames", []) if isinstance(result, dict) else []:
            fields = frame.get("schema", {}).get("fields", [])
            columns = frame.get("data", {}).get("values", [])
            if not fields or not columns:
                continue
            time_index = next(
                (index for index, field in enumerate(fields) if field.get("type") == "time"),
                0,
            )
            times = columns[time_index] if time_index < len(columns) else []
            for index, field in enumerate(fields):
                if field.get("type") != "number" or index >= len(columns):
                    continue
                points = [
                    (times[position] if position < len(times) else None, value)
                    for position, value in enumerate(columns[index])
                    if isinstance(value, (int, float)) and math.isfinite(value)
                ]
                if not points:
                    continue
                labels = {str(k): str(v) for k, v in (field.get("labels") or {}).items()}
                first_at, first = points[0]
                last_at, last = points[-1]
                maximum_at, maximum = max(points, key=lambda item: item[1])
                minimum_at, minimum = min(points, key=lambda item: item[1])
                evidence.extend(
                    [
                        NumericEvidence(expr=expr, agg="latest", seriesLabels=labels, value=last, at=_iso_time(last_at)),
                        NumericEvidence(expr=expr, agg="max", seriesLabels=labels, value=maximum, at=_iso_time(maximum_at)),
                        NumericEvidence(expr=expr, agg="min", seriesLabels=labels, value=minimum, at=_iso_time(minimum_at)),
                    ]
                )
                if first != 0:
                    evidence.append(
                        NumericEvidence(
                            expr=expr,
                            agg="change_pct",
                            seriesLabels=labels,
                            value=(last - first) / abs(first) * 100,
                            at=f"{_iso_time(first_at)}..{_iso_time(last_at)}",
                        )
                    )
    return evidence


def _printed_number(value: str) -> tuple[float, bool] | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        return None
    return float(match.group()), "%" in value


def apply_conflict_policy(finding: Finding) -> Finding:
    latest = [item for item in finding.numeric_evidence if item.agg == "latest"]
    if not finding.printed_values or not latest:
        return finding
    for printed in finding.printed_values:
        parsed = _printed_number(printed.value)
        if parsed is None:
            continue
        printed_value, is_percent = parsed
        candidates = [
            item
            for item in latest
            if printed.label.lower() in item.expr.lower()
            or any(printed.label.lower() in value.lower() for value in item.seriesLabels.values())
        ]
        if not candidates and len(finding.printed_values) == 1 and len(latest) == 1:
            candidates = latest
        for item in candidates:
            evidence_value = item.value * 100 if is_percent and abs(item.value) <= 1 else item.value
            denominator = max(abs(evidence_value), 1e-12)
            if abs(printed_value - evidence_value) / denominator > 0.10:
                data = finding.model_dump()
                data.update(
                    confidence=finding.confidence * 0.5,
                    needs_human_confirm=True,
                    conflict="printed_vs_evidence",
                )
                return Finding.model_validate(data)
    return finding


async def enrich_with_evidence(plugin, finding: Finding, panel: dict) -> Finding:
    """对非正常 Prometheus finding 自动查询并回填数值证据。"""
    if finding.status == "normal":
        return finding
    if finding.datasource_type == "elasticsearch":
        return Finding.model_validate(
            {**finding.model_dump(), "evidence_status": "unsupported", "evidence_error": "Elasticsearch 不做数值取证"}
        )
    if finding.datasource_type != "prometheus" or not panel.get("targets"):
        return Finding.model_validate(
            {**finding.model_dump(), "evidence_status": "unsupported", "evidence_error": "无可用 Prometheus 查询目标"}
        )
    try:
        raw = await plugin.call(
            "query_datasource",
            {
                "panel": panel,
                "from": panel.get("from") or "now-24h",
                "to": panel.get("to") or "now",
                "mode": "instant" if finding.panel_type in {"stat", "gauge"} else "range",
                "maxDataPoints": 1200,
            },
            30,
        )
        evidence = extract_numeric_evidence(raw, panel)
        if not evidence:
            return Finding.model_validate({**finding.model_dump(), "evidence_status": "empty"})
        enriched = Finding.model_validate(
            {**finding.model_dump(), "numeric_evidence": [item.model_dump() for item in evidence], "evidence_status": "ok"}
        )
        return apply_conflict_policy(enriched)
    except LensError as error:
        return Finding.model_validate(
            {**finding.model_dump(), "evidence_status": "failed", "evidence_error": error.message[:300]}
        )
    except Exception as error:
        return Finding.model_validate(
            {**finding.model_dump(), "evidence_status": "failed", "evidence_error": str(error)[:300]}
        )
