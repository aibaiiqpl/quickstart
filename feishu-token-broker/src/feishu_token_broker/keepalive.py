"""
后台保活：定期触发一次按需刷新，让 refresh_token 在长期无人调用时也不会过期。

为什么需要：飞书 refresh_token 只有 7 天有效期，而 Broker 只在客户端取 token 时才刷新；
连续 7 天没人用就必须人工重新授权。保活复用 TokenService.get_access_token 的
「快过期才刷新」逻辑，不引入第二套刷新路径，并发安全由同一把文件锁保证。
依赖：TokenService。
"""

from __future__ import annotations

import logging
import threading

from .feishu_client import FeishuApiError
from .service import NotAuthorizedError, TokenService

log = logging.getLogger(__name__)


def keepalive_once(service: TokenService) -> None:
    """
    执行一次保活检查；任何异常都不向外抛，保证保活线程常驻。

    未授权只记 warning（等管理员授权）；FeishuApiError 已在 feishu_client 发生点记过 ERROR，
    这里不重复记录；其余异常记录完整堆栈。
    """
    try:
        service.get_access_token()
    except NotAuthorizedError as exc:
        log.warning("keepalive skipped", extra={"ctx": {"reason": str(exc)}})
    except FeishuApiError:
        pass
    except Exception:
        log.exception("keepalive failed")


def run_keepalive(service: TokenService, stop: threading.Event, interval: float) -> None:
    """
    保活循环：启动时立即检查一次（覆盖服务重启期间可能错过的刷新），之后每 interval 秒一次。

    输入：stop 被 set 后循环在当前等待结束时退出。
    """
    while True:
        keepalive_once(service)
        if stop.wait(interval):
            return
