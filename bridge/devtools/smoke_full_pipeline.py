"""不访问真实 Grafana/方舟的完整 run→evidence→report 冒烟。"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path
from types import SimpleNamespace

from grafana_lens.inspection import InspectionService
from grafana_lens.models import Finding, Observation, PrintedValue
from grafana_lens.report import ReportRenderer
from grafana_lens.resume import RunStore


class FakePlugin:
    async def call(self, type_, payload, timeout):
        if type_ == "get_meta":
            return {"uid": "smoke", "title": "Smoke Dashboard", "from": "now-1h", "to": "now"}
        if type_ == "list_panels":
            return [{"panelId": 1, "title": "CPU", "type": "stat", "datasourceType": "prometheus", "from": "now-1h", "to": "now", "targets": [{"refId": "A", "expr": "up", "datasource": {"uid": "prom", "type": "prometheus"}}]}]
        if type_ == "capture_panel":
            return {"imageBase64": base64.b64encode(b"jpeg-smoke").decode(), "mimeType": "image/jpeg", "width": 320, "height": 180, "sizeBytes": 10, "panelMeta": {"panelId": 1, "title": "CPU", "type": "stat", "datasourceType": "prometheus", "viewPath": "spa"}}
        if type_ == "query_datasource":
            return {"results": {"A": {"frames": [{"schema": {"fields": [{"name": "Time", "type": "time"}, {"name": "Value", "type": "number", "labels": {}}]}, "data": {"values": [[1000, 2000], [0.5, 0.9]]}}]}}}
        raise AssertionError(type_)


class FakeArk:
    async def vision_inspect(self, image_b64, panel_meta):
        return Finding(panel_id=1, panel_title="CPU", panel_type="stat", datasource_type="prometheus", status="warning", observations=[Observation(kind="持续上涨", detail="图中数值上升", confidence=0.8)], printed_values=[PrintedValue(label="up", value="90%", source="stat")], risk="指标上升", suggestion="人工复核", severity="P1", risk_level="medium")


async def main():
    config = SimpleNamespace(analyze_concurrency=2, max_panels_per_run=40, model="smoke-model")
    with tempfile.TemporaryDirectory(prefix="grafana-lens-smoke-") as root:
        root_path = Path(root)
        store = RunStore(root_path / "runs")
        service = InspectionService(FakePlugin(), FakeArk(), config, run_store=store)
        result = await service.analyze_dashboard()
        renderer = ReportRenderer(config, run_store=store, output_root=root_path / "reports")
        report = renderer.render(result["runId"], exec_summary="Smoke pipeline completed")
        assert result["summary"]["done"] == 1
        assert Path(report["path"]).exists()
        html = Path(report["path"]).read_text(encoding="utf-8")
        assert "Smoke Dashboard" in html and "data:image/jpeg;base64" in html
        assert "1. 巡检概览" in html and "2. 风险识别" in html
        print({"runId": result["runId"], "summary": result["summary"], "reportBytes": report["sizeBytes"]})


if __name__ == "__main__":
    asyncio.run(main())
