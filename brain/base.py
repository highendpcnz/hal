"""Contracts shared by HAL brain providers without web, audio, or model dependencies."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable, Protocol


EventObserver = Callable[[str, dict], None]
CommentarySink = Callable[[str], None]


class BrainProviderError(RuntimeError):
    """A provider could not complete a brain operation."""


class KeyedLocks:
    """Serialize work per session while allowing unrelated sessions to overlap."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._refs: dict[str, int] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        self._refs[key] = self._refs.get(key, 0) + 1
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                yield
        finally:
            if self._refs[key] == 1:
                del self._refs[key]
                self._locks.pop(key, None)
            else:
                self._refs[key] -= 1


class BrainProvider(Protocol):
    """Minimal lifecycle and conversation contract consumed by HAL."""

    name: str
    mode: str
    permission_mode: str
    agent_cwd: str

    def init(self, data_dir: Path) -> None: ...

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def ask(self, text: str, session_id: str) -> str: ...

    async def cancel(self, session_id: str) -> None: ...

    def drop_session(self, session_id: str) -> None: ...

    def health(self) -> dict: ...

    async def commands(self, session_id: str) -> list[dict[str, str | None]]: ...

    def provider_session_for(self, session_id: str) -> str | None: ...
