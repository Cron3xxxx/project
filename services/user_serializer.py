from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager


class _LockInfo:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.ref_count = 0


class UserOperationSerializer:
    """Сериализует критичные действия на уровне пользователя"""

    def __init__(self) -> None:
        self._locks: dict[int, _LockInfo] = {}
        self._global_lock = threading.Lock()

    def _get_lock_info(self, user_id: int) -> _LockInfo:
        with self._global_lock:
            info = self._locks.get(user_id)
            if info is None:
                info = _LockInfo()
                self._locks[user_id] = info
            info.ref_count += 1
            return info

    def _release_lock_info(self, user_id: int, info: _LockInfo) -> None:
        with self._global_lock:
            info.ref_count -= 1
            if info.ref_count <= 0:
                self._locks.pop(user_id, None)

    @contextmanager
    def serialize(self, user_id: int) -> Generator[None, None, None]:
        info = self._get_lock_info(user_id)
        info.lock.acquire()
        try:
            yield
        finally:
            info.lock.release()
            self._release_lock_info(user_id, info)
