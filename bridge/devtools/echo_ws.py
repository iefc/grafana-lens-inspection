"""开发者 echo WS（T1 2.11）：127.0.0.1:9528/ws。

不校验 token/Origin（仅本机开发用）。连上后自动依次下发：
get_meta → list_panels → capture_panel（按类型抽样、全量或限量）→ capture_viewport，
打印插件返回，JPEG 落 /tmp/lens-echo/ 供目检，验证全链路。

用法：
  .venv/bin/python -m devtools.echo_ws [panelId ...]
  .venv/bin/python -m devtools.echo_ws --all [--max-panels N]
  .venv/bin/python -m devtools.echo_ws --max-panels N

无参数时每类面板自动抽一张，且优先覆盖折叠 row。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import pathlib
import sys

from websockets.asyncio.server import serve

# None 表示连上 list_panels 后按类型自动抽样。
PANELS: list[int] | None = None
ALL_PANELS = False
MAX_PANELS: int | None = None
AUTO_TYPES = ["timeseries", "graph", "table", "stat", "piechart", "gauge"]
IMG_DIR = pathlib.Path(os.environ.get("ECHO_IMG_DIR", "/tmp/lens-echo"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("ECHO_PORT", "9528"))


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grafana Lens 开发 echo WS")
    parser.add_argument(
        "--all",
        action="store_true",
        help="按看板原始顺序截图全部已枚举面板",
    )
    parser.add_argument(
        "--max-panels",
        type=positive_int,
        metavar="N",
        help="自动选择时最多截图前 N 个已枚举面板",
    )
    parser.add_argument("panel_ids", nargs="*", type=int, metavar="panelId")
    args = parser.parse_args(argv)
    if args.panel_ids and (args.all or args.max_panels is not None):
        parser.error("显式 panelId 不能与 --all 或 --max-panels 同时使用")
    return args


def select_targets(
    panels: list[dict],
    *,
    explicit_panel_ids: list[int] | None = None,
    all_panels: bool = False,
    max_panels: int | None = None,
) -> list[int]:
    if explicit_panel_ids:
        targets = list(explicit_panel_ids)
    elif all_panels or max_panels is not None:
        targets = [panel["panelId"] for panel in panels]
    else:
        # 每类抽一张；优先选折叠 row 内的，顺带验证折叠直达。
        targets = []
        for panel_type in AUTO_TYPES:
            pick = next(
                (
                    panel["panelId"]
                    for panel in panels
                    if panel.get("type") == panel_type and panel.get("collapsed")
                ),
                None,
            )
            if pick is None:
                pick = next(
                    (panel["panelId"] for panel in panels if panel.get("type") == panel_type),
                    None,
                )
            if pick is not None:
                targets.append(pick)
    return targets[:max_panels] if max_panels is not None else targets


def log(frame: dict) -> None:
    kind = frame.get("type")
    if kind == "result":
        p = frame.get("payload")
        summary = _summarize(p)
        print(f"  <- result {frame.get('id')}: {summary}")
    elif kind == "error":
        print(f"  <- error  {frame.get('id')}: {frame.get('error')}")
    elif kind in ("ping", "pong"):
        print(f"  <- {kind}")
    else:
        print(f"  <- {json.dumps(frame, ensure_ascii=False)[:200]}")


def _summarize(p) -> str:
    if isinstance(p, dict):
        if "panelCount" in p:
            return f"meta panelCount={p['panelCount']} title={p.get('title')}"
        if isinstance(p, list) and p and "panelId" in p[0]:
            return f"panels n={len(p)} first={p[0]}"
        if "imageBase64" in p:
            img = p["imageBase64"]
            return (
                f"image {p.get('width')}x{p.get('height')} "
                f"{p.get('sizeBytes', len(img))}B b64len={len(img)}"
            )
    if isinstance(p, list):
        return f"list n={len(p)} sample={p[:2]}"
    return json.dumps(p, ensure_ascii=False)[:160]


async def send(ws, frame_id: str, frame_type: str, payload=None) -> None:
    frame = {"id": frame_id, "type": frame_type, "payload": payload or {}}
    print(f"-> {frame_type} {frame_id} {json.dumps(payload or {}, ensure_ascii=False)[:120]}")
    await ws.send(json.dumps(frame, ensure_ascii=False))


async def wait_result(ws, expected_id: str) -> dict:
    while True:
        raw = await ws.recv()
        frame = json.loads(raw)
        log(frame)
        # 扩展的应用层心跳必须立即应答；否则长截图会在 pong 超时后主动断线。
        if frame.get("type") == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if frame.get("id") == expected_id:
            return frame


async def handler(ws) -> None:
    print(f"[echo] 插件已连接，开始脚本（面板：{PANELS}）")
    seq = 0

    def nid() -> str:
        nonlocal seq
        seq += 1
        return f"req-{seq}"

    rid = nid()
    await send(ws, rid, "get_meta")
    f = await wait_result(ws, rid)
    if f.get("type") != "result":
        print("[echo] get_meta 失败，终止")
        return

    rid = nid()
    await send(ws, rid, "list_panels")
    f = await wait_result(ws, rid)
    if f.get("type") != "result":
        print("[echo] list_panels 失败，终止")
        return
    panels = f.get("payload") or []
    known = {p["panelId"]: p for p in panels}
    targets = select_targets(
        panels,
        explicit_panel_ids=PANELS,
        all_panels=ALL_PANELS,
        max_panels=MAX_PANELS,
    )
    print(f"[echo] 本次截图面板（{len(targets)}/{len(panels)}）：{targets}")

    ok = 0
    for panel_id in targets:
        if panel_id not in known:
            print(f"[echo] 面板 {panel_id} 不在清单，跳过")
            continue
        rid = nid()
        await send(ws, rid, "capture_panel", {"panelId": panel_id})
        f = await wait_result(ws, rid)
        if f.get("type") != "result":
            continue
        p = f["payload"]
        assert p["sizeBytes"] <= 2 * 1024 * 1024, "单张超过 2MB"
        assert max(p["width"], p["height"]) <= 1280, "长边超过 1280"
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        meta = known[panel_id]
        name = f"panel-{panel_id}-{meta.get('type', 'x')}.jpg"
        (IMG_DIR / name).write_bytes(base64.b64decode(p["imageBase64"]))
        print(f"[echo] 已存 {IMG_DIR / name} viewPath={p.get('panelMeta', {}).get('viewPath')}")
        ok += 1
    print(f"[echo] 面板截图成功 {ok}/{len(targets)}")

    rid = nid()
    await send(ws, rid, "capture_viewport")
    f = await wait_result(ws, rid)
    if f.get("type") == "result":
        p = f["payload"]
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        (IMG_DIR / "viewport.jpg").write_bytes(base64.b64decode(p["imageBase64"]))
        print(f"[echo] 已存 {IMG_DIR / 'viewport.jpg'}")

    print("[echo] 全链路脚本完成")


async def run_once(script=handler, *, serve_fn=serve) -> None:
    """仅执行一次开发脚本，避免扩展重连后重复驱动页面。"""
    completed = asyncio.Event()

    async def run_script(ws) -> None:
        try:
            await script(ws)
        finally:
            completed.set()

    async with serve_fn(run_script, HOST, PORT):
        print(f"[echo] ws://{HOST}:{PORT}/ws 已就绪，等待扩展连接…")
        await completed.wait()


async def amain() -> None:
    await run_once()


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    PANELS = args.panel_ids or None
    ALL_PANELS = args.all
    MAX_PANELS = args.max_panels
    asyncio.run(amain())
