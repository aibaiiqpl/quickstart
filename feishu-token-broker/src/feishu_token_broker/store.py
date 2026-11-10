"""
state 目录的持久化层：token.json 与 pending_oauth.json 的加锁、原子读写。

为什么要这么谨慎：飞书 refresh_token 每刷新一次就作废换新。如果新 token
没有可靠落盘（半截写入、并发覆盖），Broker 会永久失去授权，只能人工重新授权。
因此所有写入都走「同目录临时文件 → fsync → rename → fsync 目录」，
所有读改写都在排他锁内完成。
依赖：仅标准库（fcntl，仅支持 Linux/Unix）。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .config import LOCK_FILE, PENDING_OAUTH_FILE, TOKEN_STATE_FILE
from .models import PendingOAuth, TokenState

_FILE_MODE = 0o600


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """
        获取 state 目录的排他锁，用于包住一次完整的读-改-写。

        每次调用都重新 open 锁文件：flock 锁的是 open file description，
        因此同进程内不同线程、以及 authorize 命令所在的另一进程都会互斥，
        不需要再叠加 threading.Lock。
        """
        fd = os.open(self._dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, _FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def load_tokens(self) -> TokenState | None:
        """读取授权状态；尚未授权（文件不存在）返回 None，文件损坏则抛异常。"""
        data = self._read_json(self._dir / TOKEN_STATE_FILE)
        return None if data is None else TokenState.from_dict(data)

    def save_tokens(self, state: TokenState) -> None:
        self._write_json_atomic(self._dir / TOKEN_STATE_FILE, state.to_dict())

    def save_pending(self, pending: PendingOAuth) -> None:
        """覆盖保存待完成授权；新发起的授权会让旧链接立即失效。"""
        self._write_json_atomic(self._dir / PENDING_OAUTH_FILE, pending.to_dict())

    def load_pending(self) -> PendingOAuth | None:
        data = self._read_json(self._dir / PENDING_OAUTH_FILE)
        return None if data is None else PendingOAuth.from_dict(data)

    def clear_pending(self) -> None:
        """删除待完成授权，保证同一个 state 只能被回调消费一次。"""
        (self._dir / PENDING_OAUTH_FILE).unlink(missing_ok=True)
        self._fsync_dir()

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return json.loads(text)

    def _write_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
        try:
            os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        self._fsync_dir()

    def _fsync_dir(self) -> None:
        # rename/unlink 只有在目录项落盘后才算持久，断电时才不会回滚到旧 token
        fd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
