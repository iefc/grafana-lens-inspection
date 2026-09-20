"""MCP 采集工具到插件请求的转发（T2.5）。"""

import json

from grafana_lens.errors import LensError
from grafana_lens.mcp_server import build_server


class FakePlugin:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def call(self, type_, payload, timeout):
        self.calls.append((type_, payload, timeout))
        if self.error:
            raise self.error
        return self.result


async def test_get_dashboard_meta_forwards_to_plugin():
    plugin = FakePlugin(result={"uid": "dash-1", "panelCount": 3})
    server = build_server(plugin=plugin)

    result = await server.call_tool("get_dashboard_meta", {})

    assert result.is_error is False
    assert json.loads(result.content[0].text) == {"uid": "dash-1", "panelCount": 3}
    assert plugin.calls == [("get_meta", {}, 15)]


async def test_list_panels_forwards_to_plugin():
    plugin = FakePlugin(result=[{"panelId": 12, "title": "CPU"}])
    server = build_server(plugin=plugin)

    result = await server.call_tool("list_panels", {})

    assert result.is_error is False
    assert [json.loads(content.text) for content in result.content] == [
        {"panelId": 12, "title": "CPU"}
    ]
    assert plugin.calls == [("list_panels", {}, 15)]


async def test_forwarded_lens_error_uses_standard_mcp_envelope():
    server = build_server(plugin=FakePlugin(error=LensError("PLUGIN_OFFLINE")))

    result = await server.call_tool("get_dashboard_meta", {})

    assert result.is_error is True
    assert json.loads(result.content[0].text)["code"] == "PLUGIN_OFFLINE"


class SequencePlugin:
    def __init__(self):
        self.calls = []

    async def call(self, type_, payload, timeout):
        self.calls.append((type_, payload, timeout))
        if type_ == "list_panels":
            return [{"panelId": 3}, {"panelId": 7}]
        return {"panelId": payload.get("panelId"), "imageBase64": "image"}


async def test_capture_panel_and_viewport_forward_to_plugin():
    plugin = SequencePlugin()
    server = build_server(plugin=plugin)

    panel = await server.call_tool("capture_panel", {"panelId": 3})
    viewport = await server.call_tool("capture_viewport", {})

    assert json.loads(panel.content[0].text)["panelId"] == 3
    assert json.loads(viewport.content[0].text)["imageBase64"] == "image"
    assert plugin.calls == [
        ("capture_panel", {"panelId": 3}, 120),
        ("capture_viewport", {}, 30),
    ]


async def test_capture_dashboard_captures_panels_in_list_order():
    plugin = SequencePlugin()
    server = build_server(plugin=plugin)

    result = await server.call_tool("capture_dashboard", {})

    assert json.loads(result.content[0].text) == {
        "dashboard": {"panelId": None, "imageBase64": "image"},
        "panels": [
            {"panelId": 3, "ok": True, "capture": {"panelId": 3, "imageBase64": "image"}},
            {"panelId": 7, "ok": True, "capture": {"panelId": 7, "imageBase64": "image"}},
        ],
    }
    assert plugin.calls == [
        ("get_meta", {}, 15),
        ("list_panels", {}, 15),
        ("capture_panel", {"panelId": 3}, 120),
        ("capture_panel", {"panelId": 7}, 120),
    ]
