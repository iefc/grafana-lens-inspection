# Grafana Lens

Grafana Lens 通过 Agent Skill 驱动 MCP bridge，复用 Chrome 中已登录的 Grafana 会话完成逐面板截图、方舟视觉分析、Prometheus 数值取证和本地 HTML 报告生成。

```text
Agent Skill → MCP STDIO → Python bridge → WebSocket → Chrome 扩展 → Grafana
                         ├→ 火山方舟
                         └→ run state / HTML report
```

## 安装 bridge

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e "./bridge[dev]"
.venv/bin/grafana-lens-bridge init-config
```

编辑 `~/.grafana-lens/config`，配置方舟 `api_key` 和 `model`（或 `endpoint_id`），文件权限保持 `600`。

## 加载并配对扩展

1. Chrome 扩展开发者模式加载 `extension/`。
2. 在 Grafana 页面通过 popup 授权当前域。
3. 运行 `.venv/bin/grafana-lens-bridge pair`，60 秒内点击 popup 的“一键配对本地桥”。
4. Agent MCP 连接器以 STDIO 启动 `<repo>/.venv/bin/grafana-lens-bridge serve`。

## Skill 入口

- Trae：`.trae/skills/grafana-lens-inspection/SKILL.md`
- Kiro：`.kiro/skills/grafana-lens-inspection/SKILL.md`
- 豆包工作：`~/Library/Application Support/Doubao/Default/.doubao/agent_mode/workspace/.user_skills/grafana-lens-inspection/SKILL.md`，绑定 `grafana-lens` 连接器。

用户只需输入“巡检当前 Grafana 看板”。Skill 调用：

```text
get_dashboard_meta → list_panels → analyze_dashboard → render_report
```

## 本地产物

- run 状态：`~/.grafana-lens/runs/<runId>/state.json`
- 事件流：`~/.grafana-lens/runs/<runId>/events.jsonl`
- 截图 artifact：`~/.grafana-lens/runs/<runId>/artifacts/`
- 报告：`reports/<dashboardUid>/<timestamp>/report.html`

报告截图像素不会自动脱敏，外发前必须人工检查。
