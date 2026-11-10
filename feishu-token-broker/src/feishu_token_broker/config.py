"""
Broker 常量与配置的唯一定义处。

职责：集中定义飞书接口地址、刷新阈值、文件名等常量；把 KEY=VALUE 格式的
env 文件（生产为 /opt/feishu/feishu.env）解析成不可变的 Settings。
依赖：仅标准库。服务与 authorize 命令共用同一份解析逻辑，避免两套配置来源漂移。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

SERVICE_NAME = "feishu-token-broker"

FEISHU_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
FEISHU_HTTP_TIMEOUT_SECONDS = 30

# access_token 剩余有效期低于该值就刷新：保证客户端拿到的 token 至少还能用这么久
REFRESH_MARGIN_SECONDS = 300
# 保活检查间隔：飞书 refresh_token 仅 7 天有效，无人调用时靠定期刷新持续轮换；
# access_token 约 2 小时有效，每小时检查一次即可保证每 2~3 小时轮换一次
KEEPALIVE_INTERVAL_SECONDS = 3600
# 管理员发起授权后，必须在该时间内完成浏览器授权，过期的 state 一律拒绝
PENDING_OAUTH_TTL_SECONDS = 600
# 拒绝占位符或弱口令被当成 API key 使用
MIN_API_KEY_LENGTH = 24
# 没有 offline_access 飞书不会下发 refresh_token，Broker 就无法续期
REQUIRED_SCOPE = "offline_access"

LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 8789
DEFAULT_ENV_FILE = "/opt/feishu/feishu.env"

TOKEN_STATE_FILE = "token.json"
PENDING_OAUTH_FILE = "pending_oauth.json"
LOCK_FILE = ".lock"

REQUIRED_KEYS = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BROKER_API_KEY",
    "FEISHU_OAUTH_REDIRECT_URI",
    "FEISHU_BROKER_STATE_DIR",
)
OPTIONAL_KEYS = ("FEISHU_OAUTH_SCOPE", "FEISHU_BROKER_LISTEN_PORT")


class ConfigError(Exception):
    """env 文件缺失、格式错误或取值不合法。"""


@dataclasses.dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str = dataclasses.field(repr=False)
    api_key: str = dataclasses.field(repr=False)
    redirect_uri: str
    scope: str
    state_dir: Path
    listen_port: int


def parse_env_file(path: Path) -> dict[str, str]:
    """
    解析 KEY=VALUE 格式的 env 文件。

    输入：文件路径。输出：键值字典。
    约束：忽略空行与 # 注释；值两侧成对的单/双引号会被去掉；不支持变量展开，
    保持和 systemd EnvironmentFile 的常见写法兼容。格式错误时抛 ConfigError 并指出行号，
    但不回显该行内容（可能含密钥）。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"cannot read env file {path}: {exc}") from exc

    values: dict[str, str] = {}
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ConfigError(f"{path}:{lineno}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(path: Path) -> Settings:
    """
    读取 env 文件并校验成 Settings。

    输入：env 文件路径。输出：Settings。
    约束：出现未知键直接失败——既能发现拼写错误，也能拦住旧版遗留的
    FEISHU_USER_REFRESH_TOKEN 之类不该再出现在配置里的密钥。
    scope 会自动补上 offline_access。
    """
    values = parse_env_file(path)

    unknown = sorted(set(values) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS))
    if unknown:
        raise ConfigError(f"{path}: unknown keys {unknown}")
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise ConfigError(f"{path}: missing required keys {missing}")

    api_key = values["FEISHU_BROKER_API_KEY"]
    if len(api_key) < MIN_API_KEY_LENGTH:
        raise ConfigError(f"FEISHU_BROKER_API_KEY must be at least {MIN_API_KEY_LENGTH} characters")

    redirect_uri = values["FEISHU_OAUTH_REDIRECT_URI"]
    if not redirect_uri.startswith("https://"):
        raise ConfigError("FEISHU_OAUTH_REDIRECT_URI must be an https URL")

    scopes = values.get("FEISHU_OAUTH_SCOPE", "").split()
    if REQUIRED_SCOPE not in scopes:
        scopes.append(REQUIRED_SCOPE)

    port_text = values.get("FEISHU_BROKER_LISTEN_PORT", str(DEFAULT_LISTEN_PORT))
    if not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise ConfigError(f"FEISHU_BROKER_LISTEN_PORT invalid: {port_text!r}")

    return Settings(
        app_id=values["FEISHU_APP_ID"],
        app_secret=values["FEISHU_APP_SECRET"],
        api_key=api_key,
        redirect_uri=redirect_uri,
        scope=" ".join(scopes),
        state_dir=Path(values["FEISHU_BROKER_STATE_DIR"]),
        listen_port=int(port_text),
    )
