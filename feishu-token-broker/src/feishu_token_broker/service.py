"""
Broker 业务逻辑：按需刷新 user_access_token、汇报授权身份、完成管理员发起的 OAuth 授权。

依赖 StateStore（持久化）与 FeishuClient（飞书接口），二者都通过构造函数注入，便于测试替换。
所有读改写都在 StateStore.locked() 内进行：并发请求同时发现 token 将过期时，只会有
一个真正去刷新，其余拿到刷新后的结果，避免用同一个 refresh_token 刷两次导致授权作废。
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from .config import FEISHU_AUTHORIZE_URL, PENDING_OAUTH_TTL_SECONDS, REFRESH_MARGIN_SECONDS, Settings
from .feishu_client import FeishuClient
from .models import FeishuTokens, FeishuUser, PendingOAuth, TokenState
from .store import StateStore

log = logging.getLogger(__name__)


class NotAuthorizedError(Exception):
    """Broker 尚未授权，或 refresh_token 已过期，需要管理员重新执行 authorize。"""


class OAuthStateError(Exception):
    """回调携带的 state 不存在、不匹配或已过期。"""


class TokenService:
    def __init__(
        self,
        settings: Settings,
        store: StateStore,
        client: FeishuClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._store = store
        self._client = client
        self._clock = clock

    def get_access_token(self) -> TokenState:
        """
        返回剩余有效期不少于 REFRESH_MARGIN_SECONDS 的授权状态，必要时先刷新。

        异常：NotAuthorizedError（未授权或 refresh_token 过期）；FeishuApiError（刷新失败）。
        副作用：刷新成功会轮换 refresh_token 并原子落盘；落盘失败会直接抛出，
        此时旧 refresh_token 已作废，需要重新授权——宁可显式失败也不返回一个无法续期的状态。
        """
        with self._store.locked():
            state = self._store.load_tokens()
            if state is None:
                raise NotAuthorizedError("broker has not been authorized yet")
            now = self._clock()
            if state.access_expires_at - now >= REFRESH_MARGIN_SECONDS:
                return state
            if state.refresh_expires_at <= now:
                raise NotAuthorizedError("refresh_token expired")

            tokens = self._client.refresh(state.refresh_token)
            refreshed = self._build_state(tokens, FeishuUser(name=state.user_name, open_id=state.open_id), now)
            self._store.save_tokens(refreshed)
            log.info(
                "user_access_token refreshed",
                extra={"ctx": {"expires_in": tokens.expires_in, "refresh_expires_in": tokens.refresh_token_expires_in}},
            )
            return refreshed

    def whoami(self) -> dict[str, Any]:
        """汇报当前授权用户与 token 剩余秒数；不触发刷新，也不返回任何 token。"""
        with self._store.locked():
            state = self._store.load_tokens()
        if state is None:
            raise NotAuthorizedError("broker has not been authorized yet")
        now = self._clock()
        return {
            "name": state.user_name,
            "open_id_present": bool(state.open_id),
            "scope": state.scope,
            "access_token_expires_in": max(0, int(state.access_expires_at - now)),
            "refresh_token_expires_in": max(0, int(state.refresh_expires_at - now)),
        }

    def begin_authorization(self) -> str:
        """
        生成一次性 state 并返回飞书授权链接（仅供服务器上的 authorize 命令调用）。

        副作用：覆盖 pending_oauth.json，之前发起但未完成的授权链接随即失效。
        """
        pending = PendingOAuth(state=secrets.token_urlsafe(32), expires_at=self._clock() + PENDING_OAUTH_TTL_SECONDS)
        with self._store.locked():
            self._store.save_pending(pending)
        query = urllib.parse.urlencode(
            {
                "client_id": self._settings.app_id,
                "response_type": "code",
                "redirect_uri": self._settings.redirect_uri,
                "scope": self._settings.scope,
                "state": pending.state,
                "prompt": "consent",
            },
            quote_via=urllib.parse.quote,
        )
        return f"{FEISHU_AUTHORIZE_URL}?{query}"

    def complete_authorization(self, code: str, state: str) -> TokenState:
        """
        校验回调 state 后用授权码换 token，查询用户身份并落盘。

        异常：OAuthStateError（state 无效/过期/已用）；FeishuApiError（换 token 或查用户失败）。
        约束：state 不匹配时不消费 pending，避免任何人随手请求回调就让管理员的授权链接作废；
        state 匹配后无论成败都消费掉，失败只能重新发起授权。
        """
        with self._store.locked():
            pending = self._store.load_pending()
            now = self._clock()
            if pending is None:
                raise OAuthStateError("no pending authorization")
            if not hmac.compare_digest(pending.state.encode(), state.encode()):
                raise OAuthStateError("state mismatch")
            self._store.clear_pending()
            if pending.expires_at <= now:
                raise OAuthStateError("pending authorization expired")

            tokens = self._client.exchange_code(code, self._settings.redirect_uri)
            user = self._client.get_user(tokens.access_token)
            authorized = self._build_state(tokens, user, now)
            self._store.save_tokens(authorized)
        log.info("broker authorized", extra={"ctx": {"user_name": user.name, "scope": tokens.scope}})
        return authorized

    @staticmethod
    def _build_state(tokens: FeishuTokens, user: FeishuUser, now: float) -> TokenState:
        return TokenState(
            access_token=tokens.access_token,
            access_expires_at=now + tokens.expires_in,
            refresh_token=tokens.refresh_token,
            refresh_expires_at=now + tokens.refresh_token_expires_in,
            scope=tokens.scope,
            user_name=user.name,
            open_id=user.open_id,
            updated_at=now,
        )
