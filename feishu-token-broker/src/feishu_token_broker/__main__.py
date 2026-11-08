"""
命令行入口：
  serve      启动 Broker HTTP 服务（systemd 调用）
  authorize  生成一次性飞书授权链接（管理员在服务器上以 feishu 用户执行）
两者读取同一个 env 文件，保证配置来源唯一。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_ENV_FILE, PENDING_OAUTH_TTL_SECONDS, ConfigError, Settings, load_settings
from .feishu_client import FeishuClient
from .log import setup_logging
from .server import build_server
from .service import TokenService
from .store import StateStore

log = logging.getLogger(__name__)


def _build_service(settings: Settings) -> TokenService:
    return TokenService(settings, StateStore(settings.state_dir), FeishuClient(settings.app_id, settings.app_secret))


def _serve(settings: Settings) -> None:
    server = build_server(_build_service(settings), settings.api_key, settings.listen_port)
    log.info("broker listening", extra={"ctx": {"address": "%s:%d" % server.server_address[:2]}})
    server.serve_forever()


def _authorize(settings: Settings) -> None:
    url = _build_service(settings).begin_authorization()
    print(f"Open this URL in a browser within {PENDING_OAUTH_TTL_SECONDS // 60} minutes and approve:\n\n{url}\n")
    print(f"Redirect URI (must be whitelisted in the Feishu console): {settings.redirect_uri}")
    print(f"Requested scope: {settings.scope}")


def main() -> None:
    """
    解析命令行并执行子命令。

    约束：配置错误以退出码 2 结束并输出原因（不含密钥），让 systemd 和管理员能立即看到。
    """
    parser = argparse.ArgumentParser(prog="feishu-token-broker")
    parser.add_argument("command", choices=("serve", "authorize"))
    parser.add_argument("--env-file", type=Path, default=Path(DEFAULT_ENV_FILE))
    args = parser.parse_args()

    setup_logging()
    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        log.error("invalid configuration", extra={"ctx": {"reason": str(exc)}})
        sys.exit(2)

    if args.command == "serve":
        _serve(settings)
    else:
        _authorize(settings)


if __name__ == "__main__":
    main()
