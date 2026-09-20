from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Observation(BaseModel):
    kind: Literal["持续上涨", "持续下降", "尖峰", "锯齿", "周期波动", "断流", "no_data", "稳定", "异常分布", "其他"]
    detail: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)


class PrintedValue(BaseModel):
    label: str
    value: str
    source: Literal["legend", "y_axis", "stat", "table_cell", "annotation", "other"]


class NumericEvidence(BaseModel):
    expr: str
    agg: Literal["latest", "max", "min", "first", "last", "change_pct"]
    seriesLabels: dict[str, str]
    value: float
    at: str


class Finding(BaseModel):
    panel_id: int
    panel_title: str
    panel_type: str
    datasource_type: Literal["prometheus", "elasticsearch", "other"]
    status: Literal["normal", "warning", "abnormal", "no_data"]
    observations: list[Observation] = Field(max_length=8)
    printed_values: list[PrintedValue] = Field(max_length=20)
    numeric_evidence: list[NumericEvidence] = Field(default_factory=list)
    analysis_status: Literal["valid", "schema_invalid"] = "valid"
    evidence_status: Literal["not_requested", "ok", "empty", "failed", "unsupported"] = "not_requested"
    evidence_error: str | None = Field(default=None, max_length=300)
    risk: str | None = Field(default=None, max_length=300)
    suggestion: str | None = Field(default=None, max_length=300)
    severity: Literal["P0", "P1", "P2", "NONE"]
    risk_level: Literal["high", "medium", "low", "none"] = "none"
    confidence: float = Field(default=0.8, ge=0, le=1)
    needs_human_confirm: bool = False
    conflict: str | None = None
    tokens_used: int = 0

    @model_validator(mode="after")
    def validate_contract(self):
        if self.severity == "P0":
            self.needs_human_confirm = True
        if self.status in {"warning", "abnormal"}:
            if not (self.risk or self.suggestion) or self.severity == "NONE":
                raise ValueError("warning/abnormal 必须带风险或建议，且严重度不能为 NONE")
        if self.status == "normal" and self.severity != "NONE":
            raise ValueError("normal 的 severity 必须为 NONE")
        if self.status == "no_data" and self.severity != "NONE":
            raise ValueError("no_data 的 severity 必须为 NONE")
        if self.status in {"normal", "no_data"} and self.risk_level != "none":
            raise ValueError("normal/no_data 的 risk_level 必须为 none")
        if self.datasource_type == "elasticsearch" and self.numeric_evidence:
            raise ValueError("Elasticsearch finding 不允许 numeric_evidence")
        if self.evidence_status == "ok" and not self.numeric_evidence:
            raise ValueError("evidence_status=ok 时必须有 numeric_evidence")
        if self.numeric_evidence and self.evidence_status != "ok":
            raise ValueError("存在 numeric_evidence 时 evidence_status 必须为 ok")
        return self

    @classmethod
    def validate_vlm_payload(cls, payload: dict) -> "Finding":
        if payload.get("numeric_evidence"):
            raise ValueError("VLM 输出不得包含 numeric_evidence")
        if payload.get("evidence_status", "not_requested") != "not_requested" or payload.get("evidence_error"):
            raise ValueError("VLM 不得声明数值取证状态")
        payload = {**payload, "analysis_status": "valid", "evidence_status": "not_requested", "evidence_error": None}
        return cls.model_validate(payload)


class PanelRiskCall(BaseModel):
    panel_id: int
    risk_level: Literal["high", "medium", "low"]


class LayerBrief(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    analysis: str = Field(min_length=1, max_length=400)
    no_risk: bool = False


class ActionItem(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=300)
    panel_ids: list[int] = Field(default_factory=list)


class ReportSynthesis(BaseModel):
    environment_score: int = Field(ge=0, le=100)
    summary: str = Field(min_length=1, max_length=200)
    risk_calls: list[PanelRiskCall] = Field(default_factory=list)
    layers: list[LayerBrief] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list, max_length=12)

    @field_validator("summary", mode="before")
    @classmethod
    def clamp_summary(cls, value: object) -> str:
        return str(value or "").strip()[:200]


def display_risk_level(finding: dict) -> str:
    """展示用风险等级：优先 LLM 分类，缺省时才按 P0/P1/P2 回退。"""
    status = finding.get("status")
    if status not in {"warning", "abnormal"}:
        return "none"
    level = finding.get("risk_level")
    if level in {"high", "medium", "low"}:
        return level
    return {"P0": "high", "P1": "medium", "P2": "low"}.get(str(finding.get("severity") or ""), "medium")
