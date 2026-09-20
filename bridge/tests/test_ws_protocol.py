"""插件 WebSocket 握手安全契约（T2.2）。"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from grafana_lens.config import DEFAULT_EXTENSION_ORIGIN
from grafana_lens.pairing import PairingWindow
from grafana_lens.ws_server import create_app

TOKEN = "b" * 64


def client() -> TestClient:
    return TestClient(create_app(pairing=PairingWindow(TOKEN), ws_token=TOKEN))


@pytest.mark.parametrize(
    ("query", "origin"),
    [
        ("", DEFAULT_EXTENSION_ORIGIN),
        ("?token=wrong", DEFAULT_EXTENSION_ORIGIN),
        (f"?token={TOKEN}", None),
        (f"?token={TOKEN}", "https://evil.example"),
    ],
)
def test_ws_rejects_missing_or_invalid_credentials(query: str, origin: str | None):
    headers = {"Origin": origin} if origin is not None else {}

    with pytest.raises(WebSocketDisconnect) as error:
        with client().websocket_connect(f"/ws{query}", headers=headers):
            pass

    assert error.value.code == 4403


def test_ws_accepts_fixed_origin_and_matching_token():
    app = create_app(pairing=PairingWindow(TOKEN), ws_token=TOKEN)
    with TestClient(app).websocket_connect(
        f"/ws?token={TOKEN}", headers={"Origin": DEFAULT_EXTENSION_ORIGIN}
    ) as websocket:
        websocket.send_json({"type": "status", "payload": {"online": True}})

    assert app.state.plugin.status == {"online": True}


def test_ws_replaces_existing_plugin_connection():
    test_client = client()
    headers = {"Origin": DEFAULT_EXTENSION_ORIGIN}

    with test_client.websocket_connect(f"/ws?token={TOKEN}", headers=headers) as first:
        with test_client.websocket_connect(f"/ws?token={TOKEN}", headers=headers):
            with pytest.raises(WebSocketDisconnect) as closed:
                first.receive_json()

    assert closed.value.code == 4001
