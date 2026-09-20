"""bridge serve 的 MCP/WS 共用生命周期（T2.7）。"""

import asyncio

from grafana_lens.cli import _serve_async


class FakeMcp:
    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def run_stdio_async(self):
        self.started.set()
        await self.finish.wait()


class FakeUvicorn:
    def __init__(self):
        self.started = asyncio.Event()
        self.should_exit = False
        self.stopped = False

    async def serve(self):
        self.started.set()
        while not self.should_exit:
            await asyncio.sleep(0)
        self.stopped = True


async def test_serve_stops_ws_after_stdio_finishes():
    mcp = FakeMcp()
    ws = FakeUvicorn()
    task = asyncio.create_task(_serve_async(mcp, ws))

    await ws.started.wait()
    assert mcp.started.is_set()
    mcp.finish.set()
    await task

    assert ws.should_exit is True
    assert ws.stopped is True
