"""联调辅助：反复把 Grafana 标签前置 N 秒（用户同时在用 Chrome 时避免活动标签丢失）。

用法：.venv/bin/python -m devtools.cdp_hold_front [秒数，默认120]
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from urllib.parse import urlparse

import websockets

PORT_FILE = pathlib.Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort"


def _is_grafana_dashboard(url: str) -> bool:
    """匹配任意 Grafana 看板 URL，不绑定具体内网域名。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    path = parsed.path or ""
    return "/d/" in path or "/d-solo/" in path or "/dashboard/" in path


async def main(seconds: int) -> None:
    lines = PORT_FILE.read_text().splitlines()
    url = f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"
    async with websockets.connect(url, max_size=None) as ws:
        seq = 0

        async def call(method, params=None, sid=None):
            nonlocal seq
            seq += 1
            mid = seq
            frame = {"id": mid, "method": method, "params": params or {}}
            if sid:
                frame["sessionId"] = sid
            await ws.send(json.dumps(frame))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == mid:
                    if "error" in r:
                        raise RuntimeError(r["error"])
                    return r.get("result", {})

        deadline = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < deadline:
            r = await call("Target.getTargets")
            g = [
                t
                for t in r["targetInfos"]
                if t["type"] == "page" and _is_grafana_dashboard(t.get("url") or "")
            ]
            if g:
                a = await call(
                    "Target.attachToTarget", {"targetId": g[0]["targetId"], "flatten": True}
                )
                sid = a["sessionId"]
                await call("Page.enable", {}, sid)
                await call("Page.bringToFront", {}, sid)
            await asyncio.sleep(0.3)


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    asyncio.run(main(secs))
