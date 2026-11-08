from pathlib import Path

import pytest

from feishu_token_broker.config import ConfigError, load_settings

VALID = {
    "FEISHU_APP_ID": "cli_x",
    "FEISHU_APP_SECRET": "s",
    "FEISHU_BROKER_API_KEY": "k" * 64,
    "FEISHU_OAUTH_REDIRECT_URI": "https://ft.example.com/oauth/callback",
    "FEISHU_BROKER_STATE_DIR": "/opt/feishu/state",
}


def write_env(tmp_path: Path, values: dict[str, str], extra: str = "") -> Path:
    path = tmp_path / "feishu.env"
    path.write_text("# comment\n\n" + "".join(f"{k}={v}\n" for k, v in values.items()) + extra)
    return path


def test_valid_config_appends_offline_access_and_defaults_port(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, {**VALID, "FEISHU_OAUTH_SCOPE": '"docx:document wiki:wiki"'}))
    assert settings.scope == "docx:document wiki:wiki offline_access"
    assert settings.listen_port == 8789
    assert settings.state_dir == Path("/opt/feishu/state")


def test_secrets_not_in_repr(tmp_path: Path) -> None:
    text = repr(load_settings(write_env(tmp_path, VALID)))
    assert "k" * 64 not in text and "app_secret" not in text


def test_offline_access_not_duplicated(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, {**VALID, "FEISHU_OAUTH_SCOPE": "offline_access"}))
    assert settings.scope == "offline_access"


@pytest.mark.parametrize("missing", list(VALID))
def test_missing_required_key(tmp_path: Path, missing: str) -> None:
    values = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(write_env(tmp_path, values))


def test_legacy_refresh_token_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="FEISHU_USER_REFRESH_TOKEN"):
        load_settings(write_env(tmp_path, {**VALID, "FEISHU_USER_REFRESH_TOKEN": "x"}))


def test_short_api_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="at least"):
        load_settings(write_env(tmp_path, {**VALID, "FEISHU_BROKER_API_KEY": "short"}))


def test_http_redirect_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="https"):
        load_settings(write_env(tmp_path, {**VALID, "FEISHU_OAUTH_REDIRECT_URI": "http://x/cb"}))


def test_bad_port_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="PORT"):
        load_settings(write_env(tmp_path, {**VALID, "FEISHU_BROKER_LISTEN_PORT": "70000"}))


def test_malformed_line_reports_lineno_without_content(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"feishu\.env:8: expected KEY=VALUE") as info:
        load_settings(write_env(tmp_path, VALID, extra="SECRETLINE\n"))
    assert "SECRETLINE" not in str(info.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_settings(tmp_path / "nope.env")
