"""通过 CDP 无交互重载 Grafana Lens 扩展（T1 联调辅助）。

前提：Chrome 以 --remote-debugging-port=9222 启动，且关闭了 HTTP 发现端点；
browser WS 路径从 DevToolsActivePort 文件读取。

用法：.venv/bin/python -m devtools.cdp_reload
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import websockets

EXT_ID = "ifpbbimoemkbibijemmkagpngaemojha"
PORT_FILE = pathlib.Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort"


def browser_ws_url() -> str:
    lines = PORT_FILE.read_text().splitlines()
    port = lines[0].strip()
    path = lines[1].strip()
    return f"ws://127.0.0.1:{port}{path}"


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0
        self.session_id = None
        self.pending = {}

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None):
        self.seq += 1
        msg_id = self.seq
        frame = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            frame["sessionId"] = session_id
        await self.ws.send(json.dumps(frame))
        while True:
            raw = await self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})

    async def reload_extension(self, ext_id: str) -> None:
        created = await self.send(
            "Target.createTarget", {"url": f"chrome://extensions/?id={ext_id}"}
        )
        target_id = created["targetId"]
        try:
            attached = await self.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session_id = attached["sessionId"]
            await self.send("Runtime.enable", {}, session_id)
            result = await self.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        f"chrome.developerPrivate.reload('{ext_id}',"
                        "{failQuietly:false}).then(()=>'RELOAD OK')"
                    ),
                    "awaitPromise": True,
                    "returnByValue": True,
                },
                session_id,
            )
            print(result.get("result", {}).get("value"))
        finally:
            await self.send("Target.closeTarget", {"targetId": target_id})


async def connect_with_retry(attempts: int = 5, delay: float = 2.0):
    # browser WS 端点疑似单客户端，被其他 CDP 客户端占用时握手超时，重试即可。
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await websockets.connect(
                browser_ws_url(), max_size=None, open_timeout=8
            )
        except TimeoutError as e:
            last = e
            print(f"connect timeout, retry {i + 1}/{attempts}")
            await asyncio.sleep(delay)
    raise last  # type: ignore[misc]


async def main() -> None:
    ws = await connect_with_retry()
    async with ws:
        cdp = CDP(ws)
        await cdp.reload_extension(EXT_ID)


if __name__ == "__main__":
    asyncio.run(main())
