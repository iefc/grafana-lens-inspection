"""60 秒一键配对窗口与 CORS 安全契约（T2.1）。"""

import warnings

warnings.filterwarnings(
    "ignore",
    message="The `anyio\\.abc\\.BlockingPortal alias is deprecated.*",
    category=DeprecationWarning,
    module="starlette\\.testclient",
)

from fastapi.testclient import TestClient

from grafana_lens.config import DEFAULT_EXTENSION_ORIGIN
from grafana_lens.pairing import PairingWindow
from grafana_lens.ws_server import create_app

TOKEN = "a" * 64


def make_client(clock: list[float]) -> tuple[TestClient, PairingWindow]:
    window = PairingWindow(TOKEN, ttl_seconds=60, now=lambda: clock[0])
    return TestClient(create_app(pairing=window)), window


def test_pair_rejects_when_window_is_closed_without_leaking_token():
    client, _window = make_client([100.0])

    response = client.post("/pair", headers={"Origin": DEFAULT_EXTENSION_ORIGIN})

    assert response.status_code == 403
    assert TOKEN not in response.text
    assert "access-control-allow-origin" not in response.headers


def test_pair_options_then_post_returns_token_once_for_fixed_origin():
    client, window = make_client([100.0])
    window.open()

    preflight = client.options("/pair", headers={"Origin": DEFAULT_EXTENSION_ORIGIN})
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == DEFAULT_EXTENSION_ORIGIN
    assert preflight.headers["access-control-allow-methods"] == "POST, OPTIONS"
    assert window.is_open is True

    first = client.post("/pair", headers={"Origin": DEFAULT_EXTENSION_ORIGIN})
    assert first.status_code == 200
    assert first.json() == {"token": TOKEN}
    assert first.headers["access-control-allow-origin"] == DEFAULT_EXTENSION_ORIGIN
    assert window.is_open is False

    second = client.post("/pair", headers={"Origin": DEFAULT_EXTENSION_ORIGIN})
    assert second.status_code == 403
    assert TOKEN not in second.text


def test_pair_rejects_wrong_origin_without_consuming_window_or_cors_header():
    client, window = make_client([100.0])
    window.open()

    denied = client.post("/pair", headers={"Origin": "https://evil.example"})

    assert denied.status_code == 403
    assert TOKEN not in denied.text
    assert "access-control-allow-origin" not in denied.headers
    assert window.is_open is True

    allowed = client.post("/pair", headers={"Origin": DEFAULT_EXTENSION_ORIGIN})
    assert allowed.status_code == 200
    assert allowed.json() == {"token": TOKEN}


def test_pair_rejects_expired_window_without_leaking_token():
    clock = [100.0]
    client, window = make_client(clock)
    window.open()
    clock[0] = 160.0

    response = client.post("/pair", headers={"Origin": DEFAULT_EXTENSION_ORIGIN})

    assert response.status_code == 403
    assert TOKEN not in response.text
    assert window.is_open is False
