"""插件连接 req-N 帧配对与生命周期（T2.4）。"""

import asyncio

import pytest

from grafana_lens.errors import LensError
from grafana_lens.plugin import PluginConnection, PluginTimeoutError


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed: list[int] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, frame: dict) -> None:
        self.sent.append(frame)

    async def close(self, code: int) -> None:
        self.closed.append(code)


async def test_call_assigns_req_id_and_resolves_matching_result():
    connection = PluginConnection()
    websocket = FakeWebSocket()
    await connection.attach(websocket)

    request = asyncio.create_task(connection.call("get_meta", {"scope": "tab"}, timeout=1))
    await asyncio.sleep(0)
    assert websocket.sent == [{"id": "req-1", "type": "get_meta", "payload": {"scope": "tab"}}]

    await connection.handle_frame(websocket, {"id": "req-1", "type": "result", "payload": {"uid": "abc"}})

    assert await request == {"uid": "abc"}
    assert connection.pending_count == 0


async def test_call_raises_remote_lens_error_for_matching_error_frame():
    connection = PluginConnection()
    websocket = FakeWebSocket()
    await connection.attach(websocket)

    request = asyncio.create_task(connection.call("capture_panel", {"panelId": 9}, timeout=1))
    await asyncio.sleep(0)
    await connection.handle_frame(
        websocket,
        {"id": "req-1", "type": "error", "error": {"code": "PANEL_NOT_FOUND", "message": "9"}},
    )

    with pytest.raises(LensError) as error:
        await request
    assert error.value.code == "PANEL_NOT_FOUND"
    assert connection.pending_count == 0


async def test_timeout_sends_cancel_and_releases_pending_request():
    connection = PluginConnection()
    websocket = FakeWebSocket()
    await connection.attach(websocket)

    with pytest.raises(PluginTimeoutError):
        await connection.call("capture_panel", {"panelId": 9}, timeout=0.001)

    assert websocket.sent == [
        {"id": "req-1", "type": "capture_panel", "payload": {"panelId": 9}},
        {"id": "req-2", "type": "cancel", "payload": {"targetId": "req-1"}},
    ]
    assert connection.pending_count == 0


async def test_detach_releases_pending_request_as_plugin_offline():
    connection = PluginConnection()
    websocket = FakeWebSocket()
    await connection.attach(websocket)

    request = asyncio.create_task(connection.call("list_panels", {}, timeout=1))
    await asyncio.sleep(0)
    await connection.detach(websocket)

    with pytest.raises(LensError) as error:
        await request
    assert error.value.code == "PLUGIN_OFFLINE"
    assert connection.pending_count == 0


async def test_ping_is_answered_without_completing_pending_request():
    connection = PluginConnection()
    websocket = FakeWebSocket()
    await connection.attach(websocket)

    await connection.handle_frame(websocket, {"type": "ping"})

    assert websocket.sent == [{"type": "pong"}]
    assert connection.pending_count == 0


async def test_call_preserves_list_result_payload():
    connection = PluginConnection()
    websocket = FakeWebSocket()
    await connection.attach(websocket)

    request = asyncio.create_task(connection.call("list_panels", {}, timeout=1))
    await asyncio.sleep(0)
    panels = [{"panelId": 12, "title": "CPU"}]
    await connection.handle_frame(
        websocket, {"id": "req-1", "type": "result", "payload": panels}
    )

    assert await request == panels
