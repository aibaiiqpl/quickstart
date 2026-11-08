"""FeishuClient 对飞书响应的解析：成功、业务失败（code != 0）、HTTP 错误、缺字段、网络错误。"""

import io
import json
import urllib.error

import pytest

from feishu_token_broker import feishu_client
from feishu_token_broker.feishu_client import FeishuApiError, FeishuClient


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def respond(monkeypatch, body: dict, status: int = 200, captured: list | None = None) -> None:
    raw = json.dumps(body).encode()

    def fake_urlopen(request, timeout):
        if captured is not None:
            captured.append(request)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(raw))
        return FakeResponse(raw)

    monkeypatch.setattr(feishu_client.urllib.request, "urlopen", fake_urlopen)


TOKENS = {
    "code": 0,
    "access_token": "a",
    "expires_in": 7200,
    "refresh_token": "r",
    "refresh_token_expires_in": 604800,
    "scope": "offline_access",
    "token_type": "Bearer",
}


def test_refresh_success_sends_expected_body(monkeypatch) -> None:
    captured: list = []
    respond(monkeypatch, TOKENS, captured=captured)
    tokens = FeishuClient("cli_x", "sec").refresh("old")
    assert (tokens.access_token, tokens.refresh_token, tokens.expires_in) == ("a", "r", 7200)
    sent = json.loads(captured[0].data)
    assert sent == {"grant_type": "refresh_token", "client_id": "cli_x", "client_secret": "sec", "refresh_token": "old"}
    assert captured[0].get_method() == "POST"


def test_http_400_error_is_parsed(monkeypatch) -> None:
    respond(monkeypatch, {"code": 20064, "error": "invalid_grant", "error_description": "revoked"}, status=400)
    with pytest.raises(FeishuApiError) as info:
        FeishuClient("a", "b").refresh("old")
    assert (info.value.http_status, info.value.code, info.value.message) == (400, 20064, "revoked")
    assert info.value.reauth_required


def test_http_200_with_nonzero_code_is_failure(monkeypatch) -> None:
    respond(monkeypatch, {"code": 99991663, "msg": "invalid access token"})
    with pytest.raises(FeishuApiError, match="invalid access token") as info:
        FeishuClient("a", "b").get_user("t")
    assert not info.value.reauth_required


def test_missing_refresh_token_is_failure(monkeypatch) -> None:
    respond(monkeypatch, {k: v for k, v in TOKENS.items() if k != "refresh_token"})
    with pytest.raises(FeishuApiError, match="lacks"):
        FeishuClient("a", "b").exchange_code("c", "https://x/cb")


def test_get_user_uses_bearer_and_get(monkeypatch) -> None:
    captured: list = []
    respond(monkeypatch, {"code": 0, "data": {"name": "张三", "open_id": "ou_1"}}, captured=captured)
    user = FeishuClient("a", "b").get_user("tok")
    assert (user.name, user.open_id) == ("张三", "ou_1")
    assert captured[0].get_method() == "GET" and captured[0].headers["Authorization"] == "Bearer tok"


def test_network_error(monkeypatch) -> None:
    def boom(request, timeout):
        raise urllib.error.URLError("dns failure")

    monkeypatch.setattr(feishu_client.urllib.request, "urlopen", boom)
    with pytest.raises(FeishuApiError, match="network error") as info:
        FeishuClient("a", "b").refresh("old")
    assert info.value.http_status is None
