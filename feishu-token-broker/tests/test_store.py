import stat

from conftest import authorized_state

from feishu_token_broker.models import PendingOAuth
from feishu_token_broker.store import StateStore


def test_tokens_roundtrip_with_0600_and_no_tmp_left(store: StateStore, tmp_path) -> None:
    state = authorized_state()
    with store.locked():
        store.save_tokens(state)
    assert store.load_tokens() == state
    assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [".lock", "token.json"]


def test_load_tokens_when_unauthorized(store: StateStore) -> None:
    assert store.load_tokens() is None


def test_pending_clear_is_idempotent(store: StateStore) -> None:
    store.save_pending(PendingOAuth(state="s", expires_at=1.0))
    assert store.load_pending() == PendingOAuth(state="s", expires_at=1.0)
    store.clear_pending()
    store.clear_pending()
    assert store.load_pending() is None
