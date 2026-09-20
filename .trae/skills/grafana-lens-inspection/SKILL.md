---
name: grafana-lens-inspection
description: Use when the user asks to inspect, patrol, diagnose, analyze, or generate a report for the currently open Grafana dashboard through the grafana-lens MCP connector.
allowed-tools:
  - mcp__grafana-lens__get_dashboard_meta
  - mcp__grafana-lens__list_panels
  - mcp__grafana-lens__capture_panel
  - mcp__grafana-lens__capture_dashboard
  - mcp__grafana-lens__capture_viewport
  - mcp__grafana-lens__query_datasource
  - mcp__grafana-lens__analyze_panel
  - mcp__grafana-lens__analyze_dashboard
  - mcp__grafana-lens__render_report
compatibility: Requires the grafana-lens MCP STDIO connector configured in Trae settings, the Chrome extension paired, and a Grafana dashboard open in the active Chrome tab.
metadata:
  author: argus
  version: "1.0"
---

# Grafana Lens 巡检

## 核心原则

Agent 只负责编排和摘要；截图、方舟分析、数值取证、冲突降级、断点续跑和 HTML 渲染均由 `grafana-lens` MCP bridge 完成。不得在 Agent 侧重新拼接低层采集流水线或估算图中数值。

## 标准看板巡检

1. 调用 `get_dashboard_meta`。若失败，原样展示 `nextAction` 并停止。
2. 调用 `list_panels`，说明面板数与时间范围。面板数超过 40 时先限制 `maxPanels=40`，不得静默全量超限。
3. 循环调用 `analyze_dashboard`，沿用 meta 的 `from/to`。面板数超过 40 时先限制 `maxPanels=40`，不得静默全量超限。第一次不带 `resumeRunId`。开跑后不要再调 `get_dashboard_meta` / `list_panels`：未完成 run 时桥只读盘续跑，再打 meta 会被正在进行的截图堵住，表现为连接器超时。超时或「连接器暂时不可用」时立刻再调（不要 `forceNew`）：桥先截完所有面板再读图分析，约 20 秒回包。截图阶段会抢 Chrome 焦点，截完后不再抢窗口。直到 `remaining=0` 再 `render_report`。只有用户明确要求重新巡检才 `forceNew=true`。
4. 仅基于 findings 撰写 3–5 句执行摘要：问题数、最高严重度、待确认项和主要风险。不得引入 findings 之外的数字或因果结论。
5. 调用 `render_report(runId, execSummary, openReport=true)`。
6. 回复报告路径、P0/P1/P2 数量和待人工确认数量；提醒截图像素可能含敏感信息，外发前人工检查。

## 单面板排障

用户明确指定 panelId 时，调用 `analyze_panel`，再用返回的 `runId` 调用 `render_report`。不要先手动调用 `capture_panel` 或 `query_datasource`。只有用户明确要求“只截图”或“只查询数据源”时，才调用低层工具。

## 报告样式与运维

- 报告为暗色「运维控制台」风格（近黑蓝底、点阵网格、风险色 KPI 卡、深色表格），`@media print` 自动切换浅色以保证 PDF 可读；结构为概览 / 风险识别 / 巡检详情 / 行动建议 / 附录五段，不得由 Agent 改写 HTML 结构或自行生成报告。
- 样式资产：`report/template.html`（结构）+ `report/report.css`（样式，渲染时从磁盘热读，改动无需重启 bridge）+ `bridge/src/grafana_lens/report.py` 内联 SVG（评分环/风险甜甜圈/分层竖柱，Python 模块，改动后必须重启 grafana-lens MCP bridge 进程才生效）。整看板先截完再按 `vision_batch_size`（默认 8）一次多图读方舟，不要拆成逐图请求，也不要在截图未完成时打开 `report.html`。
- 报告为单文件自包含 HTML，输出在项目根 `reports/<dashboardUid>/<UTC时间戳>/report.html`（已 gitignore，不入库），截图以 base64 内联；findings 文本会脱敏 IP/URL，但截图为原始 Grafana 画面、像素中可能含敏感信息，外发前必须人工检查。

## 错误与证据规则

- 任一 `isError`：展示 `code/message/nextAction`，确定性错误不重试；瞬时错误最多重试一次。
- 数字只能来自 `printed_values` 或 `numeric_evidence`，并标明来源；不得从曲线像素插值。
- `needs_human_confirm=true`、双源冲突、P0 必须显式提示。
- ES 面板未取证是受支持状态，不得伪造 numeric evidence。
- 不把截图、token、API Key 或完整内网信息发送给其他连接器。

## 禁止模式

- 禁止按面板执行 `capture_panel → query_datasource → analyze_panel`；bridge 已内置该流程。
- 禁止在截图未完成时打开本地 `report.html` 抢掉 Grafana 标签。
- 禁止 Agent 自己生成 HTML；必须调用 `render_report`。
- 禁止无限重试、改变时间窗而不披露、把无数据当正常、把相关性描述为确定根因。
