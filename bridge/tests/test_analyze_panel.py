import json
from types import SimpleNamespace

from grafana_lens.inspection import InspectionService
from grafana_lens.mcp_server import build_server
from grafana_lens.models import Finding, Observation
from grafana_lens.resume import RunStore


class Plugin:
    async def call(self, type_, payload, timeout):
        if type_ == "get_meta":
            return {"uid": "dash", "title": "Dash", "from": "now-1h", "to": "now"}
        if type_ == "list_panels":
            return [
                {
                    "panelId": 7,
                    "title": "CPU",
                    "type": "stat",
                    "datasourceType": "prometheus",
                    "from": "now-1h",
                    "to": "now",
                }
            ]
        if type_ == "capture_panel":
            assert payload["panelId"] == 7
            return {
                "imageBase64": "aGVsbG8=",
                "mimeType": "image/jpeg",
                "width": 10,
                "height": 10,
                "sizeBytes": 5,
                "panelMeta": {"panelId": 7, "title": "CPU", "type": "stat", "datasourceType": "prometheus"},
            }
        raise AssertionError(type_)


class Ark:
    async def vision_inspect(self, image, meta):
        assert image == "aGVsbG8="
        assert meta["panelId"] == 7
        return Finding(
            panel_id=7,
            panel_title="CPU",
            panel_type="stat",
            datasource_type="prometheus",
            status="normal",
            observations=[Observation(kind="稳定", detail="稳定", confidence=1)],
            printed_values=[],
            severity="NONE",
        )


async def test_analyze_panel_captures_then_inspects(tmp_path):
    service = InspectionService(
        Plugin(),
        Ark(),
        SimpleNamespace(analyze_concurrency=2,
                        max_panels_per_run=40, model="test"),
        run_store=RunStore(tmp_path),
    )
    server = build_server(plugin=Plugin(), inspection=service)
    result = await server.call_tool("analyze_panel", {"panelId": 7})
    body = json.loads(result.content[0].text)
    assert result.is_error is False
    assert body["panel_id"] == 7
    assert "runId" in body
    assert "imageBase64" not in body


async def test_analyze_panel_without_inspection_is_offline():
    server = build_server(plugin=Plugin(), ark=Ark())
    result = await server.call_tool("analyze_panel", {"panelId": 7})
    body = json.loads(result.content[0].text)
    assert result.is_error is True
    assert body["code"] == "PLUGIN_OFFLINE"
