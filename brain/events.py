"""Provider-independent browser event and permission routing."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import uuid

from .base import CommentarySink, EventObserver


class EventHub:
    """Transient fan-out for brain, mission, tool, and permission events."""

    def __init__(self) -> None:
        self.observer: EventObserver | None = None
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._aliases: dict[str, str] = {}
        self._commentary_sinks: dict[str, CommentarySink] = {}
        self._allowed_tools: set[str] = set()
        self._permissions: dict[str, tuple[asyncio.Future, str, str]] = {}

    def register_queue(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._queues[session_id].add(queue)
        return queue

    def unregister_queue(self, session_id: str, queue: asyncio.Queue) -> None:
        queues = self._queues.get(session_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self._queues.pop(session_id, None)

    def alias(self, alias_id: str, session_id: str) -> None:
        self._aliases[alias_id] = session_id

    def unalias(self, alias_id: str) -> None:
        self._aliases.pop(alias_id, None)

    def set_commentary_sink(self, session_id: str, sink: CommentarySink) -> None:
        self._commentary_sinks[session_id] = sink

    def clear_commentary_sink(self, session_id: str, sink: CommentarySink | None = None) -> None:
        if sink is None or self._commentary_sinks.get(session_id) is sink:
            self._commentary_sinks.pop(session_id, None)

    def commentary(self, session_id: str, text: str) -> None:
        sink = self._commentary_sinks.get(session_id)
        if sink is not None:
            sink(text)

    def publish_all(self, payload: dict) -> None:
        data = json.dumps(payload)
        for queues in list(self._queues.values()):
            for queue in list(queues):
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass

    def publish(self, session_id: str, payload: dict) -> None:
        owner = self._aliases.get(session_id)
        if owner is not None:
            payload = {**payload, "mission_session": session_id}
            session_id = owner
        if self.observer is not None:
            try:
                self.observer(session_id, payload)
            except Exception as error:
                print(f"[brain.events] observer failed: {error!r}")
        data = json.dumps(payload)
        for queue in list(self._queues.get(session_id, ())):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    def allow_tools_for(self, session_id: str) -> None:
        self._allowed_tools.add(session_id)

    def disallow_tools_for(self, session_id: str) -> None:
        self._allowed_tools.discard(session_id)

    def tools_allowed_for(self, session_id: str) -> bool:
        return session_id in self._allowed_tools

    def register_permission(self, owner: str, title: str) -> tuple[str, asyncio.Future]:
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._permissions[request_id] = (future, owner, title)
        return request_id, future

    def pending_permission_for(self, owner: str) -> str | None:
        for request_id, (_future, candidate, _title) in self._permissions.items():
            if candidate == owner:
                return request_id
        return None

    def resolve_permission(self, request_id: str, allow: bool, owner: str) -> bool:
        entry = self._permissions.get(request_id)
        if entry is None:
            return False
        future, expected_owner, _title = entry
        if expected_owner != owner or future.done():
            return False
        future.set_result(allow)
        return True

    def finish_permission(self, request_id: str) -> None:
        self._permissions.pop(request_id, None)

    def drop_session(self, session_id: str) -> None:
        self._queues.pop(session_id, None)
        self._commentary_sinks.pop(session_id, None)
        self._allowed_tools.discard(session_id)
