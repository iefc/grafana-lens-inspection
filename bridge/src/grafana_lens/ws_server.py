"""Loopback HTTP/WS server：T2.1 先提供受限的一次性配对端点。"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from .auth import origin_matches, token_matches
from .config import DEFAULT_EXTENSION_ORIGIN
from .pairing import PairingWindow
from .plugin import PluginConnection


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }


def create_app(
    *,
    pairing: PairingWindow,
    ws_token: str | None = None,
    extension_origin: str = DEFAULT_EXTENSION_ORIGIN,
    plugin: PluginConnection | None = None,
) -> FastAPI:
    app = FastAPI()
    plugin = plugin or PluginConnection()
    app.state.plugin = plugin

    def allowed(request: Request) -> bool:
        return origin_matches(request.headers.get("origin"), extension_origin)

    @app.websocket("/ws")
    async def plugin_ws(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        token = websocket.query_params.get("token")
        if ws_token is None or not origin_matches(origin, extension_origin) or not token_matches(
            token, ws_token
        ):
            await websocket.close(code=4403)
            return

        await plugin.attach(websocket)
        try:
            while True:
                frame = await websocket.receive_json()
                if isinstance(frame, dict):
                    await plugin.handle_frame(websocket, frame)
        except WebSocketDisconnect:
            pass
        finally:
            await plugin.detach(websocket)

    @app.options("/pair")
    async def pair_preflight(request: Request) -> Response:
        if not allowed(request):
            return Response(status_code=403)
        return Response(status_code=204, headers=_cors_headers(extension_origin))

    @app.post("/pair")
    async def pair(request: Request) -> Response:
        if not allowed(request):
            return Response(status_code=403)
        token = pairing.consume()
        if token is None:
            return Response(status_code=403)
        return JSONResponse(
            {"token": token},
            headers=_cors_headers(extension_origin),
        )

    return app


async def run_pairing_server(*, token: str, host: str, port: int) -> bool:
    """在 loopback 临时运行 /pair，成功消费或 60 秒到期后停止。"""
    pairing = PairingWindow(token)
    pairing.open()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(pairing=pairing),
            host=host,
            port=port,
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        while pairing.is_open and not task.done():
            await asyncio.sleep(min(0.1, pairing.seconds_remaining))
        return pairing.consumed
    finally:
        server.should_exit = True
        await task
