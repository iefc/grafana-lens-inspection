"""统一错误码目录与 LensError（01 文档 §4.2）。

所有 MCP 工具失败时返回 {code,message,nextAction} 信封，nextAction 是人话提示，
Agent 据此继续 SOP，无需解析堆栈。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorEntry:
    code: str
    message: str
    nextAction: str


ERROR_CATALOG: dict[str, ErrorEntry] = {
    "PANEL_NOT_FOUND": ErrorEntry(
        "PANEL_NOT_FOUND",
        "指定的面板不存在",
        "用 list_panels 核对 panelId 后重试",
    ),
    "RENDER_TIMEOUT": ErrorEntry(
        "RENDER_TIMEOUT",
        "面板渲染超时",
        "检查面板是否依赖离线数据源，或重试 analyze_panel（必要时回退 d-solo）",
    ),
    "NOT_ON_DASHBOARD": ErrorEntry(
        "NOT_ON_DASHBOARD",
        "当前标签页不在 Grafana 看板页",
        "在浏览器中打开目标 Grafana 看板（/d/<uid>）后重试",
    ),
    "TAB_NOT_ACTIVE": ErrorEntry(
        "TAB_NOT_ACTIVE",
        "目标标签页未在前台可见，无法截图",
        "把 Grafana 标签页切到前台、不要最小化，然后重试",
    ),
    "AUTH_REQUIRED": ErrorEntry(
        "AUTH_REQUIRED",
        "未授权或登录态失效",
        "点扩展图标在该 Grafana 域启用巡检；若仍失败，重新登录 Grafana",
    ),
    "CAPTURE_FAILED": ErrorEntry(
        "CAPTURE_FAILED",
        "截图失败",
        "确认标签页前台可见后重试 capture_panel；连续失败请检查扩展权限",
    ),
    "BRIDGE_OFFLINE": ErrorEntry(
        "BRIDGE_OFFLINE",
        "扩展无法连接本地桥",
        "确认 grafana-lens-bridge serve 已启动，必要时在扩展 options 检查桥地址",
    ),
    "PLUGIN_OFFLINE": ErrorEntry(
        "PLUGIN_OFFLINE",
        "Grafana Lens 扩展未连接",
        "在 Chrome 中加载/启用扩展并完成一键配对，确认 popup 显示已连接",
    ),
    "EVIDENCE_UNSUPPORTED": ErrorEntry(
        "EVIDENCE_UNSUPPORTED",
        "该数据源类型不支持数值取证",
        "Elasticsearch 面板仅做视觉分析，报告将标注“未取证（ES）”",
    ),
    "SCHEMA_INVALID_RETRIED": ErrorEntry(
        "SCHEMA_INVALID_RETRIED",
        "模型返回结构不合规，重试后仍失败",
        "该 finding 已降级为“待人工确认”，请在报告中人工核对截图",
    ),
    "ARK_ERROR": ErrorEntry(
        "ARK_ERROR",
        "方舟分析服务调用失败",
        "检查方舟 API Key/接入点配置与网络后重试；doctor 可辅助自检",
    ),
    "RUN_INTERRUPTED": ErrorEntry(
        "RUN_INTERRUPTED",
        "巡检中断开",
        "稍后用同一 runId（resumeRunId）恢复，已完成面板不会重跑",
    ),
}


class LensError(Exception):
    """带稳定错误码的业务异常。

    detail 存在时拼到目录默认 message 前，保留上下文（如面板号）。
    """

    def __init__(self, code: str, detail: str | None = None):
        if code not in ERROR_CATALOG:
            raise ValueError(f"未知错误码: {code}")
        entry = ERROR_CATALOG[code]
        self.code = code
        self.nextAction = entry.nextAction
        if detail:
            self.message = f"{entry.message}：{detail}"
        else:
            self.message = entry.message
        super().__init__(self.message)

    def to_body(self) -> dict:
        return {"code": self.code, "message": self.message, "nextAction": self.nextAction}


def lens_error_payload(err: LensError) -> dict:
    """渲染成 MCP 工具错误响应信封（dict 形态，供契约测试与非 MCP 复用）。"""
    return {
        "isError": True,
        "content": [{"type": "text", "text": json.dumps(err.to_body(), ensure_ascii=False)}],
    }


def lens_error_result(err: LensError):
    """渲染成 mcp 2.x CallToolResult，工具函数直接 return。"""
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=json.dumps(err.to_body(), ensure_ascii=False))],
    )
