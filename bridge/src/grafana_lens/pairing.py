"""一次性本地扩展配对窗口。"""

from __future__ import annotations

import time
from collections.abc import Callable


class PairingWindow:
    """管理固定时长、一次消费的 token 下发窗口。"""

    def __init__(
        self,
        token: str,
        *,
        ttl_seconds: float = 60,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not token:
            raise ValueError("配对 token 不能为空")
        if ttl_seconds <= 0:
            raise ValueError("配对窗口时长必须为正数")
        self._token = token
        self._ttl_seconds = ttl_seconds
        self._now = now
        self._deadline: float | None = None
        self._consumed = False

    def open(self) -> None:
        self._deadline = self._now() + self._ttl_seconds
        self._consumed = False

    @property
    def is_open(self) -> bool:
        if self._deadline is None or self._consumed:
            return False
        if self._now() >= self._deadline:
            self._deadline = None
            return False
        return True

    @property
    def seconds_remaining(self) -> float:
        if not self.is_open or self._deadline is None:
            return 0.0
        return max(0.0, self._deadline - self._now())

    @property
    def consumed(self) -> bool:
        return self._consumed

    def consume(self) -> str | None:
        if not self.is_open:
            return None
        self._consumed = True
        self._deadline = None
        return self._token
