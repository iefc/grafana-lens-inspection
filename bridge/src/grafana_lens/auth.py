"""本地桥认证辅助。"""

from __future__ import annotations

import hmac


def origin_matches(origin: str | None, expected_origin: str) -> bool:
    """仅接受固定扩展 Origin，拒绝缺失、前后缀及大小写变体。"""
    return isinstance(origin, str) and hmac.compare_digest(origin, expected_origin)


def token_matches(token: str | None, expected_token: str) -> bool:
    """以恒定时间比较 loopback WS token。"""
    return isinstance(token, str) and hmac.compare_digest(token, expected_token)
