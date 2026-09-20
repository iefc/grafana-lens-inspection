"""grafana-lens-bridge 命令行入口（01 §7.3）。"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys

from . import config


def _cmd_init_config(_args) -> int:
    path = config.config_path()
    try:
        config.write_config_template(path)
    except FileExistsError:
        print(f"配置文件已存在：{path}，如需重写请先手动删除。", file=sys.stderr)
        return 1
    print(f"已生成配置模板：{path}")
    print("请填入方舟 api_key 与 endpoint_id 后重新运行 doctor。")
    return 0


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _cmd_doctor(_args) -> int:
    print("== Grafana Lens Bridge doctor ==")

    cfg_path = config.config_path()
    if cfg_path.exists():
        print(f"[OK]   配置文件：{cfg_path}")
        cfg = config.LensConfig.load()
        if cfg.api_key:
            print("[OK]   ARK API Key：已配置")
        else:
            print("[WARN] ARK API Key：未配置（分析类工具暂不可用）")
        host, port = cfg.ws_host, cfg.ws_port
    else:
        print(f"[WARN] 配置文件：不存在（{cfg_path}），先运行 init-config")
        print("[WARN] ARK API Key：未配置")
        host, port = "127.0.0.1", 9527

    token = config.token_path()
    if token.exists():
        print(f"[OK]   token：{token}")
    else:
        print(f"[WARN] token：未生成（serve 首启自动生成，或运行 pair）：{token}")

    if _port_in_use(host, port):
        print(f"[WARN] WS 端口 {port}：已被占用（可能已有 serve 在跑）")
    else:
        print(f"[OK]   WS 端口 {port}：空闲，serve 可监听")

    print("[WARN] 扩展连接：待 T2 联通后检测")
    print("[WARN] 方舟可达性：配置 Key 后检测")
    return 0


async def _serve_async(mcp_server, uvicorn_server, closeables=()) -> None:
    ws_task = asyncio.create_task(uvicorn_server.serve())
    mcp_task = asyncio.create_task(mcp_server.run_stdio_async())
    try:
        done, _ = await asyncio.wait({ws_task, mcp_task}, return_when=asyncio.FIRST_COMPLETED)
        if ws_task in done and mcp_task not in done:
            ws_task.result()
            raise RuntimeError("WS server 提前退出，请检查监听端口和配置")
        await mcp_task
    finally:
        uvicorn_server.should_exit = True
        if not ws_task.done():
            await ws_task
        else:
            ws_task.result()
        if not mcp_task.done():
            mcp_task.cancel()
            try:
                await mcp_task
            except asyncio.CancelledError:
                pass
        for resource in closeables:
            close = getattr(resource, "aclose", None)
            if close is not None:
                await close()


def _cmd_serve(_args) -> int:
    token = config.ensure_token()
    cfg = config.LensConfig.load()
    import uvicorn
    from .analyzer import ArkClient
    from .inspection import InspectionService
    from .mcp_server import build_server
    from .pairing import PairingWindow
    from .plugin import PluginConnection
    from .report import ReportRenderer
    from .ws_server import create_app

    plugin = PluginConnection()
    ark = ArkClient(cfg)
    inspection = InspectionService(plugin, ark, cfg)
    reporter = ReportRenderer(cfg, run_store=inspection.runs)
    mcp_server = build_server(
        plugin=plugin,
        ark=ark,
        inspection=inspection,
        reporter=reporter,
    )
    app = create_app(
        pairing=PairingWindow(token),
        ws_token=token,
        extension_origin=cfg.extension_origin,
        plugin=plugin,
    )
    ws_server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.ws_host, port=cfg.ws_port, log_level="warning")
    )
    asyncio.run(_serve_async(mcp_server, ws_server, closeables=(ark,)))
    return 0


def _cmd_pair(_args) -> int:
    token = config.ensure_token()
    cfg = config.LensConfig.load()
    from .ws_server import run_pairing_server

    print(f"已打开 60 秒一键配对窗口：POST http://{cfg.ws_host}:{cfg.ws_port}/pair")
    paired = asyncio.run(
        run_pairing_server(token=token, host=cfg.ws_host, port=cfg.ws_port)
    )
    if paired:
        print(f"已配对扩展 Origin：{cfg.extension_origin}")
        return 0
    print("配对窗口已关闭或超时，未下发 token。")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grafana-lens-bridge",
        description="Grafana Lens 本地桥：MCP STDIO + 扩展 WS 中转 + 方舟分析。",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init-config", help="生成 ~/.grafana-lens/config 模板")
    sub.add_parser("serve", help="启动 MCP STDIO（T2 起同时拉起 WS server）")
    sub.add_parser("pair", help="开 60s 一键配对窗口（T2）")
    sub.add_parser("doctor", help="自检：配置/token/端口/扩展/方舟")
    return parser


_HANDLERS = {
    "init-config": _cmd_init_config,
    "doctor": _cmd_doctor,
    "serve": _cmd_serve,
    "pair": _cmd_pair,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_usage(sys.stderr)
        return 2
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
