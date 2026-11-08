"""测试公共夹具：临时 state 目录、可控时钟、飞书客户端替身、真实监听的 Broker。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from feishu_token_broker.config import Settings
from feishu_token_broker.feishu_client import FeishuApiError
from feishu_token_broker.models import FeishuTokens, FeishuUser, TokenState
from feishu_token_broker.server import build_server
from feishu_token_broker.service import TokenService
from feishu_token_broker.store import StateStore

API_KEY = "k" * 64
NOW = 1_800_000_000.0


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeFeishu:
    """按调用顺序记录参数；refresh 每次返回递增编号的新 token 对，模拟飞书的轮换。"""

    refresh_calls: list[str] = field(default_factory=list)
    exchange_calls: list[tuple[str, str]] = field(default_factory=list)
    refresh_error: FeishuApiError | None = None
    refresh_delay: threading.Event | None = None

    def refresh(self, refresh_token: str) -> FeishuTokens:
        self.refresh_calls.append(refresh_token)
        if self.refresh_delay is not None:
            self.refresh_delay.wait(timeout=5)
        if self.refresh_error is not None:
            raise self.refresh_error
        n = len(self.refresh_calls)
        return FeishuTokens(f"access-{n}", 7200, f"refresh-{n}", 604800, "offline_access docx:document")

    def exchange_code(self, code: str, redirect_uri: str) -> FeishuTokens:
        self.exchange_calls.append((code, redirect_uri))
        return FeishuTokens("access-initial", 7200, "refresh-initial", 604800, "offline_access")

    def get_user(self, access_token: str) -> FeishuUser:
        return FeishuUser(name="张三", open_id="ou_123")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_id="cli_test",
        app_secret="secret",
        api_key=API_KEY,
        redirect_uri="https://ft.example.com/oauth/callback",
        scope="docx:document offline_access",
        state_dir=tmp_path,
        listen_port=0,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def feishu() -> FakeFeishu:
    return FakeFeishu()


@pytest.fixture
def store(settings: Settings) -> StateStore:
    return StateStore(settings.state_dir)


@pytest.fixture
def service(settings: Settings, store: StateStore, feishu: FakeFeishu, clock: FakeClock) -> TokenService:
    return TokenService(settings, store, feishu, clock=clock)  # type: ignore[arg-type]


@pytest.fixture
def broker(service: TokenService) -> Iterator[str]:
    server = build_server(service, API_KEY, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def authorized_state(access_expires_in: float = 7200, refresh_expires_in: float = 604800) -> TokenState:
    return TokenState(
        access_token="access-0",
        access_expires_at=NOW + access_expires_in,
        refresh_token="refresh-0",
        refresh_expires_at=NOW + refresh_expires_in,
        scope="offline_access",
        user_name="张三",
        open_id="ou_123",
        updated_at=NOW,
    )


def get(url: str, key: str | None = API_KEY) -> tuple[int, Any]:
    """发 GET 请求，返回 (状态码, 解析后的 JSON 或原始文本)，非 2xx 不抛异常。"""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, raw, ctype = response.status, response.read(), response.headers["Content-Type"]
    except urllib.error.HTTPError as exc:
        status, raw, ctype = exc.code, exc.read(), exc.headers["Content-Type"]
    text = raw.decode("utf-8")
    return status, json.loads(text) if ctype.startswith("application/json") else text
