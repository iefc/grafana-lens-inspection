"""数值取证：Grafana 8.5 DataFrame 抽取、冲突降级、enrich 分支。"""

from grafana_lens.errors import LensError
from grafana_lens.evidence import apply_conflict_policy, enrich_with_evidence, extract_numeric_evidence
from grafana_lens.models import Finding, Observation, PrintedValue


def _frame(times, values, labels=None):
    return {
        "schema": {
            "fields": [
                {"name": "Time", "type": "time"},
                {"name": "Value", "type": "number", "labels": labels or {"instance": "node-3"}},
            ]
        },
        "data": {"values": [times, values]},
    }


def _raw(*frames, ref="A"):
    return {"results": {ref: {"frames": list(frames)}}}


def _finding(**overrides):
    data = dict(
        panel_id=12,
        panel_title="CPU",
        panel_type="timeseries",
        datasource_type="prometheus",
        status="warning",
        observations=[Observation(kind="持续上涨", detail="曲线上升", confidence=0.9)],
        printed_values=[PrintedValue(label="node-3", value="89%", source="legend")],
        risk="可能资源紧张",
        suggestion="核对 limit",
        severity="P1",
    )
    data.update(overrides)
    return Finding(**data)


def test_extracts_latest_max_min_and_change_pct():
    evidence = extract_numeric_evidence(
        _raw(_frame([1_000, 2_000], [0.5, 0.9])),
        {"targets": [{"refId": "A", "expr": "up"}]},
    )
    by_agg = {item.agg: item for item in evidence}
    assert by_agg["latest"].value == 0.9
    assert by_agg["max"].value == 0.9
    assert by_agg["min"].value == 0.5
    assert by_agg["change_pct"].value == 80.0
    assert by_agg["latest"].expr == "up"
    assert by_agg["latest"].seriesLabels == {"instance": "node-3"}


def test_extract_skips_empty_or_non_numeric_frames():
    empty = extract_numeric_evidence({"results": {"A": {"frames": []}}}, {"targets": []})
    bad = extract_numeric_evidence(
        {"results": {"A": "not-a-frame"}},
        {"targets": [{"refId": "A", "expr": "up"}]},
    )
    nan = extract_numeric_evidence(
        _raw(_frame([1_000], [float("nan")])),
        {"targets": [{"refId": "A", "expr": "up"}]},
    )
    assert empty == []
    assert bad == []
    assert nan == []


def test_conflict_when_printed_percent_deviates_over_10():
    finding = _finding(
        numeric_evidence=extract_numeric_evidence(
            _raw(_frame([1_000, 2_000], [0.10, 0.12])),
            {"targets": [{"refId": "A", "expr": "node_memory"}]},
        ),
        evidence_status="ok",
    )
    result = apply_conflict_policy(finding)
    assert result.conflict == "printed_vs_evidence"
    assert result.needs_human_confirm is True
    assert result.confidence == finding.confidence * 0.5


def test_no_conflict_when_printed_matches_latest():
    finding = _finding(
        printed_values=[PrintedValue(label="node-3", value="90%", source="stat")],
        numeric_evidence=extract_numeric_evidence(
            _raw(_frame([1_000, 2_000], [0.88, 0.90])),
            {"targets": [{"refId": "A", "expr": "up"}]},
        ),
        evidence_status="ok",
    )
    result = apply_conflict_policy(finding)
    assert result.conflict is None
    assert result.confidence == 0.8


async def test_enrich_skips_normal_and_marks_es_unsupported():
    class Plugin:
        async def call(self, *args, **kwargs):
            raise AssertionError("normal / ES 不应取证")

    normal = await enrich_with_evidence(Plugin(), _finding(status="normal", severity="NONE", risk=None, suggestion=None), {})
    es = await enrich_with_evidence(
        Plugin(),
        _finding(datasource_type="elasticsearch", printed_values=[]),
        {"datasourceType": "elasticsearch"},
    )
    assert normal.evidence_status == "not_requested"
    assert es.evidence_status == "unsupported"


async def test_enrich_queries_prometheus_and_backfills():
    class Plugin:
        async def call(self, type_, payload, timeout):
            assert type_ == "query_datasource"
            assert payload["mode"] == "range"
            return _raw(_frame([1_000, 2_000], [0.5, 0.9]))

    result = await enrich_with_evidence(
        Plugin(),
        _finding(),
        {"targets": [{"refId": "A", "expr": "up"}], "from": "now-1h", "to": "now"},
    )
    assert result.evidence_status == "ok"
    assert any(item.agg == "latest" and item.value == 0.9 for item in result.numeric_evidence)


async def test_enrich_records_failed_when_plugin_errors():
    class Plugin:
        async def call(self, *args, **kwargs):
            raise LensError("AUTH_REQUIRED", "401")

    result = await enrich_with_evidence(
        Plugin(),
        _finding(),
        {"targets": [{"refId": "A", "expr": "up"}]},
    )
    assert result.evidence_status == "failed"
    assert "401" in (result.evidence_error or "")
