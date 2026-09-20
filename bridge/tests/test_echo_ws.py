"""开发 echo WS 的协议行为回归测试（T1 2.11）。"""

import importlib.util
import json
from pathlib import Path

import pytest


_ECHO_WS_PATH = Path(__file__).parents[1] / "devtools" / "echo_ws.py"
_SPEC = importlib.util.spec_from_file_location("echo_ws", _ECHO_WS_PATH)
assert _SPEC and _SPEC.loader
_ECHO_WS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ECHO_WS)
wait_result = _ECHO_WS.wait_result


class FakeWebSocket:
    def __init__(self, frames: list[dict]):
        self._frames = iter(frames)
        self.sent: list[dict] = []

    async def recv(self) -> str:
        return json.dumps(next(self._frames))

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


async def test_wait_result_replies_to_application_ping_before_business_result():
    ws = FakeWebSocket(
        [
            {"type": "ping"},
            {"id": "req-3", "type": "result", "ok": True, "payload": {}},
        ]
    )

    result = await wait_result(ws, "req-3")

    assert result["id"] == "req-3"
    assert ws.sent == [{"type": "pong"}]


async def test_run_once_stops_listening_after_first_script_finishes():
    events: list[str] = []

    async def script(ws):
        assert ws == "first-connection"
        events.append("script")

    class FakeServer:
        def __init__(self, callback):
            self.callback = callback

        async def __aenter__(self):
            events.append("entered")
            await self.callback("first-connection")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events.append("exited")

    def fake_serve(callback, host, port):
        assert host == "127.0.0.1"
        assert port == 9528
        events.append("listening")
        return FakeServer(callback)

    await _ECHO_WS.run_once(script, serve_fn=fake_serve)

    assert events == ["listening", "entered", "script", "exited"]


_SAMPLE_PANELS = [
    {"panelId": 23, "type": "stat", "collapsed": True},
    {"panelId": 19, "type": "timeseries", "collapsed": True},
    {"panelId": 111, "type": "graph", "collapsed": False},
    {"panelId": 90, "type": "table", "collapsed": True},
    {"panelId": 17, "type": "piechart", "collapsed": False},
]


def test_select_targets_all_keeps_dashboard_order():
    assert _ECHO_WS.select_targets(_SAMPLE_PANELS, all_panels=True) == [23, 19, 111, 90, 17]


def test_select_targets_max_panels_uses_dashboard_order():
    assert _ECHO_WS.select_targets(_SAMPLE_PANELS, max_panels=3) == [23, 19, 111]


def test_parse_args_all_and_max_panels_are_combinable():
    args = _ECHO_WS.parse_args(["--all", "--max-panels", "20"])

    assert args.panel_ids == []
    assert args.all is True
    assert args.max_panels == 20


def test_parse_args_rejects_explicit_ids_with_automatic_selection():
    with pytest.raises(SystemExit):
        _ECHO_WS.parse_args(["19", "--all"])
