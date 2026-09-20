"""已认证 Chrome 扩展 WebSocket 的 req-N 请求配对。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .errors import ERROR_CATALOG, LensError


class PluginTimeoutError(TimeoutError):
    """插件仍在线但未在调用时限内返回终局帧。"""


@dataclass
class _Pending:
    future: asyncio.Future[Any]
    websocket: Any


class PluginConnection:
    """持有一条插件 WS，并安全配对并发 req-N 调用。"""

    def __init__(self) -> None:
        self._websocket: Any | None = None
        self._pending: dict[str, _Pending] = {}
        self._next_request = 0
        self._send_lock = asyncio.Lock()
        self.status: dict = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def connected(self) -> bool:
        return self._websocket is not None

    async def attach(self, websocket: Any) -> None:
        previous = self._websocket
        self._websocket = websocket
        if previous is not None and previous is not websocket:
            await self._fail_pending_for(previous)
            await previous.close(code=4001)
        await websocket.accept()

    async def detach(self, websocket: Any) -> None:
        await self._fail_pending_for(websocket)
        if self._websocket is websocket:
            self._websocket = None

    async def call(self, type_: str, payload: dict, timeout: float) -> Any:
        async with self._send_lock:
            websocket = self._websocket
            if websocket is None:
                raise LensError("PLUGIN_OFFLINE")
            self._next_request += 1
            request_id = f"req-{self._next_request}"
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            pending = _Pending(future=future, websocket=websocket)
            self._pending[request_id] = pending
            try:
                await websocket.send_json({"id": request_id, "type": type_, "payload": payload})
            except Exception as exc:
                self._pending.pop(request_id, None)
                if not future.done():
                    future.set_exception(LensError("PLUGIN_OFFLINE", str(exc)))

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except asyncio.TimeoutError as exc:
            if self._pending.get(request_id) is pending:
                self._pending.pop(request_id, None)
                await self._send_cancel(websocket, request_id)
            raise PluginTimeoutError(f"插件请求 {request_id} 超时") from exc
        finally:
            if self._pending.get(request_id) is pending:
                self._pending.pop(request_id, None)

    async def handle_frame(self, websocket: Any, frame: dict) -> None:
        frame_type = frame.get("type")
        if frame_type == "ping":
            await websocket.send_json({"type": "pong"})
            return
        if frame_type == "status":
            self.status = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
            return

        request_id = frame.get("id")
        pending = self._pending.pop(request_id, None) if isinstance(request_id, str) else None
        if pending is None or pending.websocket is not websocket or pending.future.done():
            return

        if frame_type == "result":
            pending.future.set_result(frame.get("payload"))
        elif frame_type == "error":
            error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
            code = error.get("code")
            detail = error.get("message")
            mapped_code = code if code in ERROR_CATALOG else "CAPTURE_FAILED"
            pending.future.set_exception(LensError(mapped_code, str(detail) if detail else None))
        else:
            # 非终局帧（如 progress）不应使请求失败或影响其他 req-N。
            self._pending[request_id] = pending

    async def _send_cancel(self, websocket: Any, target_id: str) -> None:
        self._next_request += 1
        try:
            await websocket.send_json(
                {
                    "id": f"req-{self._next_request}",
                    "type": "cancel",
                    "payload": {"targetId": target_id},
                }
            )
        except Exception:
            pass

    async def _fail_pending_for(self, websocket: Any) -> None:
        pending_items = [
            (request_id, pending)
            for request_id, pending in self._pending.items()
            if pending.websocket is websocket
        ]
        for request_id, pending in pending_items:
            self._pending.pop(request_id, None)
            if not pending.future.done():
                pending.future.set_exception(LensError("PLUGIN_OFFLINE"))
