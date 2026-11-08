"""
HTTP 接口层：路由、Bearer 鉴权、请求解析与响应格式化，业务全部委托给 TokenService。

接口（与旧版 Broker 保持兼容，客户端 skill 无需改动）：
  GET /health                 无鉴权，存活探测
  GET /v1/whoami              Bearer，授权用户摘要与 token 剩余秒数
  GET /v1/user_access_token   Bearer，返回短期 user_access_token
  GET /oauth/callback         飞书授权回调，靠一次性 state 防伪
只监听回环地址，由 Caddy 终结 TLS 后反代进来。
依赖：标准库 http.server（ThreadingHTTPServer，每请求一线程）。
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import LISTEN_HOST, SERVICE_NAME
from .feishu_client import FeishuApiError
from .service import NotAuthorizedError, OAuthStateError, TokenService

log = logging.getLogger(__name__)


def build_server(service: TokenService, api_key: str, port: int, host: str = LISTEN_HOST) -> ThreadingHTTPServer:
    """
    构造（但不启动）Broker HTTP 服务。

    输入：业务服务、客户端 API key、监听端口（测试传 0 由系统分配）。
    输出：已绑定端口的 ThreadingHTTPServer，调用方负责 serve_forever / shutdown。
    """
    expected_key = api_key.encode("utf-8")

    class Handler(BrokerHandler):
        pass

    Handler.service = service
    Handler.expected_key = expected_key
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


class BrokerHandler(BaseHTTPRequestHandler):
    server_version = SERVICE_NAME
    sys_version = ""
    service: TokenService
    expected_key: bytes

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定的方法名
        started = time.monotonic()
        parsed = urllib.parse.urlsplit(self.path)
        routes = {
            "/health": self._health,
            "/v1/whoami": self._whoami,
            "/v1/user_access_token": self._user_access_token,
            "/oauth/callback": self._oauth_callback,
        }
        handler = routes.get(parsed.path)
        try:
            if handler is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            else:
                handler(urllib.parse.parse_qs(parsed.query))
        except Exception:
            log.exception("unhandled error", extra={"ctx": {"path": parsed.path}})
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error"})
        finally:
            # 只记录 path，不记录 query：回调的 query 里有一次性授权码
            log.info(
                "request",
                extra={
                    "ctx": {
                        "path": parsed.path,
                        "status": getattr(self, "_status", None),
                        "duration_ms": round((time.monotonic() - started) * 1000, 1),
                        "client": self.headers.get("X-Forwarded-For", self.client_address[0]),
                    }
                },
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # 关闭 http.server 默认的明文访问日志，统一由 do_GET 输出结构化日志
        pass

    def _health(self, _query: dict[str, list[str]]) -> None:
        self._send_json(HTTPStatus.OK, {"ok": True, "service": SERVICE_NAME})

    def _whoami(self, _query: dict[str, list[str]]) -> None:
        if not self._authenticated():
            return
        try:
            self._send_json(HTTPStatus.OK, self.service.whoami())
        except NotAuthorizedError as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "not_authorized", "detail": str(exc)})

    def _user_access_token(self, _query: dict[str, list[str]]) -> None:
        if not self._authenticated():
            return
        try:
            state = self.service.get_access_token()
        except NotAuthorizedError as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "not_authorized", "detail": str(exc)})
            return
        except FeishuApiError as exc:
            error = "reauthorization_required" if exc.reauth_required else "feishu_refresh_failed"
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": error, "feishu_code": exc.code, "detail": exc.message})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "access_token": state.access_token,
                "token_type": "Bearer",
                "expires_in": max(0, int(state.access_expires_at - time.time())),
            },
        )

    def _oauth_callback(self, query: dict[str, list[str]]) -> None:
        state = query.get("state", [""])[0]
        if "error" in query:
            self._send_html(HTTPStatus.BAD_REQUEST, f"Authorization was not granted: {query['error'][0]}")
            return
        code = query.get("code", [""])[0]
        if not code or not state:
            self._send_html(HTTPStatus.BAD_REQUEST, "Missing code or state.")
            return
        try:
            authorized = self.service.complete_authorization(code, state)
        except OAuthStateError as exc:
            log.warning("oauth callback rejected", extra={"ctx": {"reason": str(exc)}})
            self._send_html(HTTPStatus.BAD_REQUEST, f"Authorization rejected: {exc}. Run the authorize command again.")
            return
        except FeishuApiError as exc:
            self._send_html(HTTPStatus.BAD_GATEWAY, f"Feishu rejected the authorization: code={exc.code} {exc.message}")
            return
        self._send_html(
            HTTPStatus.OK,
            f"Broker authorized as {authorized.user_name}. Granted scope: {authorized.scope}. You can close this page.",
        )

    def _authenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme == "Bearer" and hmac.compare_digest(token.strip().encode("utf-8"), self.expected_key):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _send_html(self, status: HTTPStatus, message: str) -> None:
        page = f"<!doctype html><meta charset='utf-8'><title>{SERVICE_NAME}</title><p>{html.escape(message)}</p>"
        self._send(status, "text/html; charset=utf-8", page.encode("utf-8"))

    def _send(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
        self._status = int(status)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
