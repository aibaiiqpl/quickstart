"""保活：定期触发刷新，使 7 天有效的 refresh_token 在无人调用时也能持续轮换。"""

import threading

from conftest import FakeFeishu, authorized_state

from feishu_token_broker.config import REFRESH_MARGIN_SECONDS
from feishu_token_broker.feishu_client import FeishuApiError
from feishu_token_broker.keepalive import keepalive_once, run_keepalive
from feishu_token_broker.service import TokenService
from feishu_token_broker.store import StateStore


def save(store: StateStore, **kwargs) -> None:
    with store.locked():
        store.save_tokens(authorized_state(**kwargs))


def test_refreshes_when_near_expiry(service: TokenService, store: StateStore, feishu: FakeFeishu) -> None:
    save(store, access_expires_in=REFRESH_MARGIN_SECONDS - 1)
    keepalive_once(service)
    assert feishu.refresh_calls == ["refresh-0"]
    assert store.load_tokens().refresh_token == "refresh-1"


def test_no_refresh_when_fresh(service: TokenService, store: StateStore, feishu: FakeFeishu) -> None:
    save(store)
    keepalive_once(service)
    assert feishu.refresh_calls == []


def test_not_authorized_does_not_raise(service: TokenService, caplog) -> None:
    keepalive_once(service)
    assert "keepalive skipped" in caplog.text


def test_feishu_error_does_not_raise(service: TokenService, store: StateStore, feishu: FakeFeishu) -> None:
    save(store, access_expires_in=0)
    feishu.refresh_error = FeishuApiError("refresh", 500, 99991, "busy")
    keepalive_once(service)
    assert store.load_tokens().refresh_token == "refresh-0"


def test_unexpected_error_is_logged_not_raised(service: TokenService, monkeypatch, caplog) -> None:
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(service, "get_access_token", boom)
    keepalive_once(service)
    assert "keepalive failed" in caplog.text and "disk full" in caplog.text


def test_loop_ticks_immediately_and_stops(service: TokenService, monkeypatch) -> None:
    ticks = threading.Event()
    stop = threading.Event()
    monkeypatch.setattr(service, "get_access_token", ticks.set)
    thread = threading.Thread(target=run_keepalive, args=(service, stop, 3600), daemon=True)
    thread.start()
    assert ticks.wait(timeout=5)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
