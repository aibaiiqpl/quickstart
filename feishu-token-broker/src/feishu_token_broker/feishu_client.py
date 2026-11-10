"""
飞书开放平台 OAuth 相关接口的 HTTP 客户端（外部数据源层）。

覆盖三个接口：授权码换 token、refresh_token 换 token（v2 /oauth/token），以及
查询当前用户信息（v1 /user_info）。飞书以响应体里的 code 判定成败，HTTP 200
也可能是失败，所以统一按 code != 0 报错。
失败在这里记录一次 ERROR 日志（根源发生点），上层只负责把 FeishuApiError 映射成响应。
依赖：仅标准库 urllib；会遵循进程环境里的 HTTP(S)_PROXY。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .config import FEISHU_HTTP_TIMEOUT_SECONDS, FEISHU_TOKEN_URL, FEISHU_USER_INFO_URL
from .models import FeishuTokens, FeishuUser

log = logging.getLogger(__name__)

# 这些错误码表示 refresh_token 已失效，只能由管理员重新授权，重试没有意义
REAUTH_REQUIRED_CODES = frozenset({20026, 20037, 20064, 20073, 20024})


class FeishuApiError(Exception):
    def __init__(self, operation: str, http_status: int | None, code: int | None, message: str) -> None:
        super().__init__(f"{operation} failed: http={http_status} code={code} {message}")
        self.operation = operation
        self.http_status = http_status
        self.code = code
        self.message = message

    @property
    def reauth_required(self) -> bool:
        return self.code in REAUTH_REQUIRED_CODES


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret

    def exchange_code(self, code: str, redirect_uri: str) -> FeishuTokens:
        """用授权码换取首个 token 对；code 一次性且 5 分钟有效。"""
        body = self._request_json(
            "exchange_code",
            FEISHU_TOKEN_URL,
            payload={
                "grant_type": "authorization_code",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        return self._parse_tokens("exchange_code", body)

    def refresh(self, refresh_token: str) -> FeishuTokens:
        """
        用 refresh_token 换新 token 对。

        副作用：调用成功后传入的 refresh_token 立即作废，调用方必须先把返回的新
        refresh_token 落盘再使用 access_token。
        """
        body = self._request_json(
            "refresh",
            FEISHU_TOKEN_URL,
            payload={
                "grant_type": "refresh_token",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "refresh_token": refresh_token,
            },
        )
        return self._parse_tokens("refresh", body)

    def get_user(self, access_token: str) -> FeishuUser:
        body = self._request_json("get_user", FEISHU_USER_INFO_URL, bearer=access_token)
        data = body.get("data") or {}
        if not data.get("open_id"):
            raise self._fail("get_user", 200, None, "response has no open_id")
        return FeishuUser(name=data.get("name", ""), open_id=data["open_id"])

    def _parse_tokens(self, operation: str, body: dict[str, Any]) -> FeishuTokens:
        # 没有 refresh_token 说明授权时没带上 offline_access，Broker 无法续期，必须视为失败
        if not body.get("access_token") or not body.get("refresh_token"):
            raise self._fail(operation, 200, body.get("code"), "response lacks access_token or refresh_token")
        return FeishuTokens(
            access_token=body["access_token"],
            expires_in=int(body["expires_in"]),
            refresh_token=body["refresh_token"],
            refresh_token_expires_in=int(body["refresh_token_expires_in"]),
            scope=body.get("scope", ""),
        )

    def _request_json(
        self,
        operation: str,
        url: str,
        payload: dict[str, Any] | None = None,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        """
        发送请求并返回 code == 0 的响应体。

        约束：HTTP 错误、网络错误、非 JSON、code != 0 一律抛 FeishuApiError；
        错误信息只取飞书返回的 error/error_description/msg，不包含请求体（含密钥）。
        """
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")

        try:
            with urllib.request.urlopen(request, timeout=FEISHU_HTTP_TIMEOUT_SECONDS) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise self._fail(operation, None, None, f"network error: {exc}") from exc

        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise self._fail(operation, status, None, f"non-JSON response: {raw[:200]!r}") from None

        code = body.get("code")
        if status != 200 or code != 0:
            detail = body.get("error_description") or body.get("msg") or body.get("error") or "unknown error"
            raise self._fail(operation, status, code, str(detail))
        return body

    def _fail(self, operation: str, http_status: int | None, code: int | None, message: str) -> FeishuApiError:
        log.error(
            "feishu api call failed",
            extra={"ctx": {"operation": operation, "http_status": http_status, "feishu_code": code, "detail": message}},
        )
        return FeishuApiError(operation, http_status, code, message)
