import pytest
from pydantic import ValidationError

from grafana_lens.models import Finding, NumericEvidence, Observation, PrintedValue, ReportSynthesis


def base_finding(**overrides):
    data = {
        "panel_id": 1,
        "panel_title": "CPU",
        "panel_type": "timeseries",
        "datasource_type": "prometheus",
        "status": "normal",
        "observations": [Observation(kind="稳定", detail="曲线稳定", confidence=0.9)],
        "printed_values": [PrintedValue(label="Last", value="20%", source="stat")],
        "severity": "NONE",
    }
    data.update(overrides)
    return data


def test_normal_finding_requires_none_severity():
    with pytest.raises(ValidationError):
        Finding(**base_finding(severity="P1"))


def test_abnormal_finding_requires_risk_or_suggestion_and_severity():
    with pytest.raises(ValidationError):
        Finding(**base_finding(status="abnormal", severity="NONE"))


def test_elasticsearch_finding_rejects_numeric_evidence():
    evidence = NumericEvidence(expr="x", agg="latest", seriesLabels={}, value=1.0, at="2026-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        Finding(**base_finding(datasource_type="elasticsearch", numeric_evidence=[evidence]))


def test_vlm_payload_rejects_numeric_evidence():
    evidence = {"expr": "x", "agg": "latest", "seriesLabels": {}, "value": 1.0, "at": "2026-01-01T00:00:00Z"}
    with pytest.raises(ValueError):
        Finding.validate_vlm_payload(base_finding(numeric_evidence=[evidence]))


def test_normal_finding_rejects_high_risk_level():
    with pytest.raises(ValidationError):
        Finding(**base_finding(risk_level="high"))


def test_synthesis_clamps_summary_to_200_chars():
    synthesis = ReportSynthesis(environment_score=50, summary="字" * 250)
    assert len(synthesis.summary) == 200
