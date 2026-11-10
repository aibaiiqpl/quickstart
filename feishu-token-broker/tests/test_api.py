"""通过真实监听的 HTTP 服务端到端验证接口行为，飞书侧用 FakeFeishu 替身。"""

import threading
import urllib.parse

from conftest import NOW, FakeClock, FakeFeishu, authorized_state, get

from feishu_token_broker.config import PENDING_OAUTH_TTL_SECONDS, REFRESH_MARGIN_SECONDS
from feishu_token_broker.feishu_client import FeishuApiError
from feishu_token_broker.service import TokenService
from feishu_token_broker.store import StateStore


def save(store: StateStore, **kwargs) -> None:
    with store.locked():
        store.save_tokens(authorized_state(**kwargs))


def test_health_needs_no_auth(broker: str) -> None:
    assert get(f"{broker}/health", key=None) == (200, {"ok": True, "service": "feishu-token-broker"})


def test_unknown_path_404(broker: str) -> None:
    assert get(f"{broker}/v1/nope")[0] == 404


def test_protected_endpoints_reject_missing_or_wrong_key(broker: str) -> None:
    for path in ("/v1/whoami", "/v1/user_access_token"):
        assert get(f"{broker}{path}", key=None) == (401, {"error": "unauthorized"})
        assert get(f"{broker}{path}", key="k" * 63 + "x") == (401, {"error": "unauthorized"})


def test_not_authorized_yet(broker: str) -> None:
    for path in ("/v1/whoami", "/v1/user_access_token"):
        status, body = get(f"{broker}{path}")
        assert status == 503 and body["error"] == "not_authorized"


def test_whoami_reports_user_without_tokens(broker: str, store: StateStore) -> None:
    save(store)
    status, body = get(f"{broker}/v1/whoami")
    assert status == 200
    assert body == {
        "name": "张三",
        "open_id_present": True,
        "scope": "offline_access",
        "access_token_expires_in": 7200,
        "refresh_token_expires_in": 604800,
    }


def test_cached_token_returned_without_refresh(broker: str, store: StateStore, feishu: FakeFeishu) -> None:
    save(store, access_expires_in=REFRESH_MARGIN_SECONDS)
    status, body = get(f"{broker}/v1/user_access_token")
    assert status == 200 and body["access_token"] == "access-0" and body["token_type"] == "Bearer"
    assert feishu.refresh_calls == []


def test_near_expiry_refreshes_and_persists_rotated_token(
    broker: str, store: StateStore, feishu: FakeFeishu, clock: FakeClock
) -> None:
    save(store, access_expires_in=REFRESH_MARGIN_SECONDS - 1)
    status, body = get(f"{broker}/v1/user_access_token")
    assert status == 200 and body["access_token"] == "access-1"
    assert feishu.refresh_calls == ["refresh-0"]

    persisted = store.load_tokens()
    assert persisted.refresh_token == "refresh-1"
    assert persisted.access_expires_at == NOW + 7200
    assert (persisted.user_name, persisted.open_id) == ("张三", "ou_123")

    # 第二次刷新必须使用轮换后的新 refresh_token
    clock.now += 7200
    assert get(f"{broker}/v1/user_access_token")[1]["access_token"] == "access-2"
    assert feishu.refresh_calls == ["refresh-0", "refresh-1"]


def test_concurrent_requests_refresh_once(broker: str, store: StateStore, feishu: FakeFeishu) -> None:
    save(store, access_expires_in=0)
    feishu.refresh_delay = threading.Event()
    results: list[tuple[int, dict]] = []
    threads = [
        threading.Thread(target=lambda: results.append(get(f"{broker}/v1/user_access_token"))) for _ in range(5)
    ]
    for t in threads:
        t.start()
    feishu.refresh_delay.set()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 5
    assert all(status == 200 and body["access_token"] == "access-1" for status, body in results)
    assert feishu.refresh_calls == ["refresh-0"]


def test_expired_refresh_token_requires_reauth(broker: str, store: StateStore, feishu: FakeFeishu) -> None:
    save(store, access_expires_in=0, refresh_expires_in=0)
    status, body = get(f"{broker}/v1/user_access_token")
    assert status == 503 and body["detail"] == "refresh_token expired"
    assert feishu.refresh_calls == []


def test_refresh_failure_returns_502_and_keeps_state(broker: str, store: StateStore, feishu: FakeFeishu) -> None:
    save(store, access_expires_in=0)
    before = store.load_tokens()
    feishu.refresh_error = FeishuApiError("refresh", 400, 20064, "refresh token revoked")
    status, body = get(f"{broker}/v1/user_access_token")
    assert status == 502
    assert body == {"error": "reauthorization_required", "feishu_code": 20064, "detail": "refresh token revoked"}
    assert store.load_tokens() == before

    feishu.refresh_error = FeishuApiError("refresh", 500, 99991, "busy")
    assert get(f"{broker}/v1/user_access_token")[1]["error"] == "feishu_refresh_failed"


def callback(broker: str, **params: str):
    return get(f"{broker}/oauth/callback?{urllib.parse.urlencode(params)}", key=None)


def pending_state(service: TokenService) -> str:
    url = service.begin_authorization()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["redirect_uri"] == ["https://ft.example.com/oauth/callback"]
    assert query["scope"] == ["docx:document offline_access"]
    assert query["response_type"] == ["code"]
    return query["state"][0]


def test_oauth_happy_path_then_replay_rejected(
    broker: str, service: TokenService, store: StateStore, feishu: FakeFeishu
) -> None:
    state = pending_state(service)
    status, text = callback(broker, code="c1", state=state)
    assert status == 200 and "张三" in text
    assert feishu.exchange_calls == [("c1", "https://ft.example.com/oauth/callback")]
    assert store.load_tokens().refresh_token == "refresh-initial"
    assert store.load_pending() is None

    assert callback(broker, code="c1", state=state)[0] == 400


def test_oauth_wrong_state_does_not_burn_pending(
    broker: str, service: TokenService, store: StateStore, feishu: FakeFeishu
) -> None:
    state = pending_state(service)
    status, text = callback(broker, code="c1", state="forged")
    assert status == 400 and "state mismatch" in text
    assert feishu.exchange_calls == []
    assert callback(broker, code="c2", state=state)[0] == 200


def test_oauth_expired_pending(broker: str, service: TokenService, clock: FakeClock, feishu: FakeFeishu) -> None:
    state = pending_state(service)
    clock.now += PENDING_OAUTH_TTL_SECONDS
    status, text = callback(broker, code="c1", state=state)
    assert status == 400 and "expired" in text
    assert feishu.exchange_calls == []


def test_oauth_user_denied_and_missing_params(broker: str, service: TokenService) -> None:
    pending_state(service)
    status, text = callback(broker, error="access_denied", state="x")
    assert status == 400 and "access_denied" in text
    assert callback(broker, state="x")[0] == 400


def test_oauth_without_pending(broker: str) -> None:
    status, text = callback(broker, code="c", state="s")
    assert status == 400 and "no pending authorization" in text


def test_callback_html_is_escaped(broker: str, service: TokenService, feishu: FakeFeishu, monkeypatch) -> None:
    from feishu_token_broker.models import FeishuUser

    monkeypatch.setattr(feishu, "get_user", lambda _t: FeishuUser(name="<script>", open_id="ou"))
    status, text = callback(broker, code="c", state=pending_state(service))
    assert status == 200 and "<script>" not in text and "&lt;script&gt;" in text
