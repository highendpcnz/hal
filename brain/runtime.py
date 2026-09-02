"""Configured brain facade consumed by the HAL web and mission layers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .base import CommentarySink, EventObserver, KeyedLocks as KeyedLocks
from .events import EventHub
from .gemma import GemmaProvider
from .hermes import HermesProvider


PROVIDER_NAME = os.environ.get("HAL_BRAIN", "gemma").strip().lower()
if PROVIDER_NAME not in {"gemma", "hermes"}:
    raise RuntimeError("HAL_BRAIN must be 'gemma' or 'hermes'")

_events = EventHub()
_provider = HermesProvider() if PROVIDER_NAME == "hermes" else GemmaProvider(_events)

BRIDGE_MODE = _provider.mode
PERMISSION_MODE = _provider.permission_mode
YOLO = PERMISSION_MODE == "yolo"
AGENT_CWD = _provider.agent_cwd


def init(data_dir: Path) -> None:
    _provider.init(data_dir)


async def startup() -> None:
    await _provider.startup()


async def shutdown() -> None:
    await _provider.shutdown()


async def ask(text: str, session_id: str) -> str:
    return await _provider.ask(text, session_id)


async def cancel_session(session_id: str) -> None:
    await _provider.cancel(session_id)


def drop_session(session_id: str) -> None:
    _provider.drop_session(session_id)


def bridge_health() -> dict:
    return _provider.health()


async def list_slash_commands(session_id: str) -> list[dict[str, str | None]]:
    return await _provider.commands(session_id)


def provider_session_for(session_id: str) -> str | None:
    return _provider.provider_session_for(session_id)


async def run_diagnostic_command(args: list[str], timeout: float) -> dict:
    return await _provider.run_diagnostic_command(args, timeout)


def set_event_observer(observer: EventObserver | None) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.bridge.on_event = observer
    else:
        _events.observer = observer


def register_event_queue(session_id: str) -> asyncio.Queue:
    if PROVIDER_NAME == "hermes":
        return _provider.register_event_queue(session_id)
    return _events.register_queue(session_id)


def unregister_event_queue(session_id: str, queue: asyncio.Queue) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.unregister_event_queue(session_id, queue)
    else:
        _events.unregister_queue(session_id, queue)


def alias_events(alias_id: str, session_id: str) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.alias_events(alias_id, session_id)
    else:
        _events.alias(alias_id, session_id)


def unalias_events(alias_id: str) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.unalias_events(alias_id)
    else:
        _events.unalias(alias_id)


def publish_event(session_id: str, payload: dict) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.publish_event(session_id, payload)
    else:
        _events.publish(session_id, payload)


def publish_event_all(payload: dict) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.publish_event_all(payload)
    else:
        _events.publish_all(payload)


def set_commentary_sink(session_id: str, sink: CommentarySink) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.set_commentary_sink(session_id, sink)
    else:
        _events.set_commentary_sink(session_id, sink)


def clear_commentary_sink(session_id: str, sink: CommentarySink | None = None) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.clear_commentary_sink(session_id, sink)
    else:
        _events.clear_commentary_sink(session_id, sink)


def allow_tools_for(session_id: str) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.allow_tools_for(session_id)
    else:
        _events.allow_tools_for(session_id)


def disallow_tools_for(session_id: str) -> None:
    if PROVIDER_NAME == "hermes":
        _provider.disallow_tools_for(session_id)
    else:
        _events.disallow_tools_for(session_id)


def pending_permission_for(session_id: str) -> str | None:
    if PROVIDER_NAME == "hermes":
        return _provider.pending_permission_for(session_id)
    return _events.pending_permission_for(session_id)


def resolve_permission(request_id: str, allow: bool, session_id: str) -> bool:
    if PROVIDER_NAME == "hermes":
        return _provider.resolve_permission(request_id, allow, session_id)
    return _events.resolve_permission(request_id, allow, session_id)
