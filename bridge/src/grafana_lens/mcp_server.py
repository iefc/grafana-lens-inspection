"""MCP STDIO server：注册 9 个 Lens 工具（01 文档 §4.1）。

T0 骨架阶段：扩展尚未联通，所有工具统一返回 PLUGIN_OFFLINE。
后续里程碑逐个替换方法体，工具名与入参 schema 保持稳定。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .errors import LensError, lens_error_result
from .plugin import PluginTimeoutError


def _offline():
    return lens_error_result(LensError("PLUGIN_OFFLINE"))


def build_server(plugin=None, ark=None, inspection=None, reporter=None) -> MCPServer:
    server = MCPServer(
        name="grafana-lens",
        title="Grafana Lens",
        version="1.0.0",
        description="在已登录 Grafana 看板上截图取证、视觉分析并生成巡检报告。",
    )

    async def forward(type_: str, payload: dict, timeout: float):
        if plugin is None:
            return _offline()
        try:
            return await plugin.call(type_, payload, timeout)
        except LensError as error:
            return lens_error_result(error)
        except PluginTimeoutError as error:
            code = "RENDER_TIMEOUT" if type_.startswith(
                "capture") else "RUN_INTERRUPTED"
            return lens_error_result(LensError(code, str(error)))

    @server.tool(
        "get_dashboard_meta",
        description="获取当前标签页看板元信息（uid、title、url、folder、panel 总数）。",
    )
    async def get_dashboard_meta() -> dict:
        return await forward("get_meta", {}, 15)

    @server.tool(
        "list_panels",
        description="列出当前看板全部面板（panelId、title、type、网格位置、折叠行归属）。",
    )
    async def list_panels() -> dict:
        return await forward("list_panels", {}, 15)

    @server.tool(
        "capture_panel",
        description="对单个面板做 SPA 单面板截图，返回截图 base64 与渲染状态。",
    )
    async def capture_panel(
        panelId: int,
        fromTime: str | None = None,
        toTime: str | None = None,
        theme: str = "light",
    ) -> dict:
        payload = {"panelId": panelId}
        if theme != "light":
            payload["theme"] = theme
        if fromTime is not None:
            payload["from"] = fromTime
        if toTime is not None:
            payload["to"] = toTime
        return await forward("capture_panel", payload, 120)

    @server.tool(
        "capture_dashboard",
        description="逐面板串行截图整个看板，返回每面板截图与折叠行展开结果。",
    )
    async def capture_dashboard(
        fromTime: str | None = None,
        toTime: str | None = None,
        maxPanels: int | None = None,
    ) -> dict:
        if plugin is None:
            return _offline()
        try:
            dashboard = await plugin.call("get_meta", {}, 15)
            panels = await plugin.call("list_panels", {}, 15)
            limit = maxPanels if maxPanels is not None else len(panels)
            items = []
            for panel in panels[: max(0, limit)]:
                panel_id = panel.get("panelId") if isinstance(
                    panel, dict) else None
                if not isinstance(panel_id, int):
                    continue
                payload = {"panelId": panel_id}
                if fromTime is not None:
                    payload["from"] = fromTime
                if toTime is not None:
                    payload["to"] = toTime
                try:
                    capture = await plugin.call("capture_panel", payload, 120)
                    items.append(
                        {"panelId": panel_id, "ok": True, "capture": capture})
                except LensError as error:
                    items.append(
                        {"panelId": panel_id, "ok": False, "error": error.to_body()})
                except PluginTimeoutError as error:
                    items.append(
                        {
                            "panelId": panel_id,
                            "ok": False,
                            "error": LensError("RENDER_TIMEOUT", str(error)).to_body(),
                        }
                    )
            return {"dashboard": dashboard, "panels": items}
        except LensError as error:
            return lens_error_result(error)
        except PluginTimeoutError as error:
            return lens_error_result(LensError("RUN_INTERRUPTED", str(error)))

    @server.tool(
        "capture_viewport",
        description="截取当前视口整屏，用于不适合单面板截图的自定义面板兜底。",
    )
    async def capture_viewport() -> dict:
        return await forward("capture_viewport", {}, 30)

    @server.tool(
        "query_datasource",
        description="经扩展 cookie 代理 /api/ds/query（Grafana 8.5 帧格式），取回数值证据。",
    )
    async def query_datasource(
        panelId: int,
        fromTime: str | None = None,
        toTime: str | None = None,
        mode: str = "range",
    ) -> dict:
        if inspection is None:
            return _offline()
        try:
            return await inspection.query_datasource(
                panelId,
                from_=fromTime,
                to=toTime,
                mode=mode,
            )
        except LensError as error:
            return lens_error_result(error)
        except PluginTimeoutError as error:
            return lens_error_result(LensError("RUN_INTERRUPTED", str(error)))

    @server.tool(
        "analyze_panel",
        description="对方舟发送单面板截图做视觉分析，回填数值证据，产出 finding。",
    )
    async def analyze_panel(
        panelId: int,
        fromTime: str | None = None,
        toTime: str | None = None,
    ) -> dict:
        if inspection is None:
            return _offline()
        try:
            return await inspection.analyze_panel(panelId, from_=fromTime, to=toTime)
        except LensError as error:
            return lens_error_result(error)
        except PluginTimeoutError as error:
            return lens_error_result(LensError("RENDER_TIMEOUT", str(error)))

    @server.tool(
        "analyze_dashboard",
        description="编排整看板：先截完所有面板并落盘，再按 vision_batch_size 读本地 JPEG 调方舟（截完后不再抢 Chrome 焦点）。后台持续进行；本工具最多等待约 20 秒即返回进度。未传 resumeRunId 时自动复用同一看板未完成 run。remaining>0 时再次调用即可，不要 forceNew。",
    )
    async def analyze_dashboard(
        fromTime: str | None = None,
        toTime: str | None = None,
        maxPanels: int | None = None,
        resumeRunId: str | None = None,
        forceNew: bool = False,
    ) -> dict:
        if inspection is None:
            return _offline()
        try:
            return await inspection.analyze_dashboard(
                from_=fromTime,
                to=toTime,
                max_panels=maxPanels,
                resume_run_id=resumeRunId,
                force_new=forceNew,
            )
        except FileNotFoundError as error:
            return lens_error_result(LensError("RUN_INTERRUPTED", f"run 不存在：{error}"))
        except LensError as error:
            return lens_error_result(error)
        except PluginTimeoutError as error:
            return lens_error_result(LensError("RUN_INTERRUPTED", str(error)))

    @server.tool(
        "render_report",
        description="把指定 run 的 findings 合成为综述并渲染为单文件自包含 HTML 巡检报告。",
    )
    async def render_report(
        runId: str,
        execSummary: str = "",
        openReport: bool = True,
    ) -> dict:
        if reporter is None:
            return _offline()
        try:
            if inspection is not None:
                await inspection.synthesize_run(runId, exec_hint=execSummary)
            return reporter.render(
                runId,
                exec_summary=execSummary,
                open_browser=openReport,
            )
        except FileNotFoundError:
            return lens_error_result(LensError("RUN_INTERRUPTED", f"run 不存在：{runId}"))
        except Exception as error:
            return lens_error_result(LensError("CAPTURE_FAILED", f"报告渲染失败：{error}"))

    return server
