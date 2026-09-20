"""真实链路联调：本机桥 WS ← Chrome 扩展 ← 已打开的 Grafana，再跑高层巡检并出报告。

用法（cwd=bridge/）：
  ../.venv/bin/python -m devtools.live_skill_pipeline --max-panels 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import uvicorn

from grafana_lens.analyzer import ArkClient
from grafana_lens.config import LensConfig, ensure_token
from grafana_lens.errors import LensError
from grafana_lens.inspection import InspectionService
from grafana_lens.pairing import PairingWindow
from grafana_lens.plugin import PluginConnection, PluginTimeoutError
from grafana_lens.report import ReportRenderer
from grafana_lens.ws_server import create_app


async def _wait_plugin(plugin: PluginConnection, seconds: float) -> bool:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if plugin.connected:
            return True
        await asyncio.sleep(0.4)
    return plugin.connected


async def _run(max_panels: int, wait_plugin_s: float) -> dict:
    cfg = LensConfig.load()
    token = ensure_token()
    plugin = PluginConnection()
    ark = ArkClient(cfg)
    inspection = InspectionService(plugin, ark, cfg)
    reporter = ReportRenderer(cfg, run_store=inspection.runs)
    app = create_app(
        pairing=PairingWindow(token),
        ws_token=token,
        extension_origin=cfg.extension_origin,
        plugin=plugin,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.ws_host,
                       port=cfg.ws_port, log_level="warning")
    )
    ws_task = asyncio.create_task(server.serve())
    try:
        if not await _wait_plugin(plugin, wait_plugin_s):
            return {
                "ok": False,
                "stage": "plugin",
                "code": "PLUGIN_OFFLINE",
                "message": "扩展未连上本地桥",
                "nextAction": "Chrome 加载 extension/，打开 Grafana 看板，popup 一键配对后保持 popup 显示在线",
            }

        meta = await plugin.call("get_meta", {}, 15)
        panels = await plugin.call("list_panels", {}, 15)
        connected = {
            "stage": "connected",
            "uid": meta.get("uid") if isinstance(meta, dict) else None,
            "title": meta.get("title") if isinstance(meta, dict) else None,
            "panelCount": len(panels) if isinstance(panels, list) else None,
        }

        result = await inspection.analyze_dashboard(max_panels=max_panels)
        report = reporter.render(
            result["runId"],
            exec_summary="真实链路联调：截图经插件回桥，方舟分析后本地渲染。",
            open_browser=False,
        )
        return {
            "ok": not result.get("interrupted"),
            **connected,
            "runId": result["runId"],
            "summary": result["summary"],
            "errors": result.get("errors") or [],
            "report": report["path"],
        }
    except (LensError, PluginTimeoutError) as error:
        payload = error.to_body() if isinstance(error, LensError) else {
            "code": "RUN_INTERRUPTED",
            "message": str(error),
            "nextAction": "把 Grafana 看板标签切到前台后重试",
        }
        return {"ok": False, "stage": "inspect", **payload}
    finally:
        server.should_exit = True
        if not ws_task.done():
            await ws_task
        await ark.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-panels", type=int, default=2)
    parser.add_argument("--wait-plugin", type=float, default=45)
    args = parser.parse_args()
    result = asyncio.run(_run(args.max_panels, args.wait_plugin))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
