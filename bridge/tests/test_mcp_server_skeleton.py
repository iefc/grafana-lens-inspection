"""MCP server 骨架：9 个工具注册齐全（01 文档 §4.1），骨架阶段一律 PLUGIN_OFFLINE。"""

import json

import pytest

from grafana_lens.mcp_server import build_server

EXPECTED_TOOLS = [
    "get_dashboard_meta",
    "list_panels",
    "capture_panel",
    "capture_dashboard",
    "capture_viewport",
    "query_datasource",
    "analyze_panel",
    "analyze_dashboard",
    "render_report",
]


@pytest.fixture
def server():
    return build_server()


async def test_tools_list_names(server):
    tools = await server.list_tools()
    names = [t.name for t in tools]
    assert names == EXPECTED_TOOLS
    assert len(names) == 9


async def test_each_tool_has_description_and_schema(server):
    for tool in await server.list_tools():
        assert tool.description
        assert tool.input_schema is not None
        assert "type" in tool.input_schema


@pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
async def test_skeleton_tools_return_plugin_offline(server, tool_name):
    args = {}
    if tool_name in ("capture_panel", "analyze_panel", "query_datasource"):
        args = {"panelId": 1}
    elif tool_name == "render_report":
        args = {"runId": "run-x"}
    result = await server.call_tool(tool_name, args)
    body = json.loads(result.content[0].text)
    assert body["code"] == "PLUGIN_OFFLINE"
    assert result.is_error is True
