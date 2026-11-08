"""
Broker 内部流转的数据结构，仅定义字段与序列化，不含业务逻辑。

所有 token 字段都设置 repr=False，防止对象被打印或写进日志时泄露密钥。
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class FeishuTokens:
    """飞书 /authen/v2/oauth/token 一次成功响应的有效载荷。"""

    access_token: str = dataclasses.field(repr=False)
    expires_in: int
    refresh_token: str = dataclasses.field(repr=False)
    refresh_token_expires_in: int
    scope: str


@dataclasses.dataclass(frozen=True)
class FeishuUser:
    name: str
    open_id: str


@dataclasses.dataclass(frozen=True)
class TokenState:
    """持久化在 state/token.json 里的完整授权状态；时间均为 Unix 秒。"""

    access_token: str = dataclasses.field(repr=False)
    access_expires_at: float
    refresh_token: str = dataclasses.field(repr=False)
    refresh_expires_at: float
    scope: str
    user_name: str
    open_id: str
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenState:
        return cls(**{field.name: data[field.name] for field in dataclasses.fields(cls)})


@dataclasses.dataclass(frozen=True)
class PendingOAuth:
    """管理员发起、尚未完成的一次授权；state 只能被消费一次。"""

    state: str = dataclasses.field(repr=False)
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingOAuth:
        return cls(state=data["state"], expires_at=data["expires_at"])
