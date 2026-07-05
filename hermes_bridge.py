"""Bridge between the HAL web frontend and Hermes Agent.

Two implementations, selected by HAL_BRIDGE (default "acp"):

acp        One persistent `hermes-acp` process speaking the Agent Client
           Protocol over stdio (agent-client-protocol library). No per-turn
           CLI startup cost — a turn is just session/prompt. ACP sessions
           persist to ~/.hermes/state.db, so browser sessions survive both
           bridge and agent restarts via session/load.

subprocess One `hermes chat -Q -q` process per turn (the original bridge).
           Contract: stdout = clean reply, stderr = `session_id: <id>`.

Both persist the cookie-session -> hermes-session map in DATA_DIR and
serialize turns per browser session — Hermes sessions are single-writer.
The HAL persona comes from AGENTS.md in the agent cwd in both modes.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shlex
import socket
import threading
import time
from collections import defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

BRIDGE_MODE = os.environ.get("HAL_BRIDGE", "acp").strip().lower()
HERMES_BIN = os.path.expanduser(
    os.environ.get("HAL_HERMES_BIN", "~/hermes-agent/.venv/bin/hermes")
)
HERMES_ACP_BIN = os.path.expanduser(
    os.environ.get("HAL_HERMES_ACP_BIN", "~/hermes-agent/.venv/bin/hermes-acp")
)
AGENT_CWD = os.path.expanduser(os.environ.get("HAL_AGENT_CWD", str(Path(__file__).resolve().parent)))
AGENT_TIMEOUT = float(os.environ.get("HAL_AGENT_TIMEOUT", "180"))
OFFLINE_PREFLIGHT = os.environ.get("HAL_OFFLINE_PREFLIGHT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
OFFLINE_CHECK_HOSTS = os.environ.get(
    "HAL_OFFLINE_CHECK_HOSTS",
    "1.1.1.1:443,api.openai.com:443,openrouter.ai:443",
)
OFFLINE_CHECK_TIMEOUT = float(os.environ.get("HAL_OFFLINE_CHECK_TIMEOUT", "0.4"))
OFFLINE_CHECK_TTL = float(os.environ.get("HAL_OFFLINE_CHECK_TTL", "30"))
# Auto-approve tool permission requests (ACP mode) — voice-triggered shell
# access. The subprocess mode equivalent is HAL_HERMES_ARGS="--yolo".
YOLO = os.environ.get("HAL_YOLO", "") == "1"
SESSION_SOURCE = "hal-web"
# Extra CLI args for subprocess mode, e.g. HAL_HERMES_ARGS="-m gpt-5.4 --yolo"
EXTRA_ARGS = shlex.split(os.environ.get("HAL_HERMES_ARGS", ""))

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SESSION_ID_RE = re.compile(r"^session_id:\s*(\S+)", re.M)


class _KeyedLocks:
    """One asyncio.Lock per key, evicted only when truly idle.

    Eviction can't just test lock.locked(): between release() and a queued
    waiter re-acquiring, locked() is False, so a lock with waiters would be
    dropped and the next turn would mint a fresh one — two turns running
    concurrently on a single-writer Hermes session. Refcount holders and
    waiters instead."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._refs: dict[str, int] = {}

    @asynccontextmanager
    async def hold(self, key: str):
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


_cookie_locks = _KeyedLocks()

FAILURE_LINE = "I'm sorry, Dave. I'm afraid something went wrong on my end."
TIMEOUT_LINE = "I'm sorry, Dave. That took longer than I allow myself. Please try again."
OFFLINE_LINE = "I'm sorry, Dave. I am disconnected from inference right now."

_network_lock = threading.Lock()
_network_check_at = 0.0
_network_check_online: bool | None = None


def _parse_check_hosts(raw: str) -> list[tuple[str, int]]:
    hosts: list[tuple[str, int]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        host, sep, port = item.rpartition(":")
        if not sep:
            host, port = item, "443"
        try:
            hosts.append((host.strip("[]"), int(port)))
        except ValueError:
            continue
    return hosts


def _network_available() -> bool:
    """Fast, cached internet check before sending a turn to remote inference."""
    global _network_check_at, _network_check_online
    if not OFFLINE_PREFLIGHT:
        return True

    with _network_lock:
        now = time.monotonic()
        if _network_check_online is not None and now - _network_check_at < OFFLINE_CHECK_TTL:
            return _network_check_online

    hosts = _parse_check_hosts(OFFLINE_CHECK_HOSTS)
    if not hosts:
        return True

    def can_connect(target: tuple[str, int]) -> bool:
        host, port = target
        try:
            with socket.create_connection((host, port), timeout=OFFLINE_CHECK_TIMEOUT):
                return True
        except OSError:
            return False

    online = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futures = [pool.submit(can_connect, target) for target in hosts]
        try:
            for future in concurrent.futures.as_completed(futures, timeout=OFFLINE_CHECK_TIMEOUT):
                if future.result():
                    online = True
                    break
        except concurrent.futures.TimeoutError:
            online = False

    with _network_lock:
        _network_check_at = time.monotonic()
        _network_check_online = online
    return online


class SessionMap:
    """Persistent cookie-session -> hermes-session-id mapping."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        try:
            self._map: dict[str, str] = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            self._map = {}

    def get(self, session_id: str) -> str | None:
        return self._map.get(session_id)

    def set(self, session_id: str, hermes_id: str) -> None:
        with self._lock:
            self._map[session_id] = hermes_id
            self._flush()

    def drop(self, session_id: str) -> None:
        with self._lock:
            if self._map.pop(session_id, None) is not None:
                self._flush()

    def _flush(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._map, indent=1))
        tmp.replace(self._path)


_session_map: SessionMap | None = None
_data_dir: Path | None = None


def init(data_dir: Path) -> None:
    global _session_map, _data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    _data_dir = data_dir
    _session_map = SessionMap(data_dir / "hermes_sessions.json")


def acp_session_for(cookie_id: str) -> str | None:
    return _session_map.get(cookie_id) if _session_map is not None else None


# ---------------------------------------------------------------------------
# In-process SSE event fan-out — one asyncio.Queue per browser/cookie session.
# Transient (not persisted): tool-call/permission events are ephemeral UI
# signal, not conversation state.
# ---------------------------------------------------------------------------

_event_queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
_acp_to_cookie: dict[str, str] = {}  # acp session_id -> cookie_id


def register_event_queue(cookie_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _event_queues[cookie_id].add(q)
    return q


def unregister_event_queue(cookie_id: str, q: asyncio.Queue) -> None:
    queues = _event_queues.get(cookie_id)
    if queues is not None:
        queues.discard(q)
        if not queues:
            _event_queues.pop(cookie_id, None)


# Aliases let a synthetic session key (a mission's private session) deliver
# its events to the owning browser session's queues.
_publish_aliases: dict[str, str] = {}


def alias_events(alias_id: str, cookie_id: str) -> None:
    """Route events published under alias_id to cookie_id's SSE queues."""
    _publish_aliases[alias_id] = cookie_id


def unalias_events(alias_id: str) -> None:
    _publish_aliases.pop(alias_id, None)


def publish_event(cookie_id: str, payload: dict) -> None:
    """Emit an SSE event for a browser session (aliases resolved)."""
    cookie_id = _publish_aliases.get(cookie_id, cookie_id)
    data = json.dumps(payload)
    for q in list(_event_queues.get(cookie_id, ())):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass  # ticker/eye state is ephemeral — drop rather than block


# ---------------------------------------------------------------------------
# ACP mode — one persistent hermes-acp process
# ---------------------------------------------------------------------------


class _TurnAborted(Exception):
    """The agent refused the turn (e.g. its session state evaporated)."""


def _load_acp():
    from acp import PROTOCOL_VERSION, spawn_agent_process  # noqa: F401
    from acp.schema import (  # noqa: F401
        AllowedOutcome,
        ClientCapabilities,
        DeniedOutcome,
        RequestPermissionResponse,
        TextContentBlock,
    )
    return locals()


class _HALClient:
    """Client half of the ACP connection: collects reply text, answers
    permission requests. Chunks are only recorded while a turn is active for
    that session, so session/load history replay never leaks into a reply."""

    def __init__(self, acp_mod):
        self._acp = acp_mod
        self._buffers: dict[str, list[str]] = {}
        self._active: set[str] = set()

    def begin(self, session_id: str) -> None:
        self._buffers[session_id] = []
        self._active.add(session_id)

    def finish(self, session_id: str) -> str:
        self._active.discard(session_id)
        return "".join(self._buffers.pop(session_id, [])).strip()

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        kind = getattr(update, "session_update", "")
        if kind == "agent_message_chunk":
            if session_id not in self._active:
                return
            text = getattr(getattr(update, "content", None), "text", "") or ""
            if text:
                self._buffers.setdefault(session_id, []).append(text)
        elif kind in ("tool_call", "tool_call_update"):
            cookie_id = _acp_to_cookie.get(session_id)
            if cookie_id is None:
                return  # session/load replay before any turn bound this session
            publish_event(cookie_id, {
                "type": kind,
                "tool_call_id": getattr(update, "tool_call_id", None),
                "title": getattr(update, "title", None),
                "kind": getattr(update, "kind", None),
                "status": getattr(update, "status", None),
            })

    async def request_permission(self, options, session_id, tool_call, **kwargs):
        m = self._acp
        if YOLO:
            allow = next((o for o in options if o.kind == "allow_once"), None) or next(
                (o for o in options if o.kind == "allow_always"), None
            )
            if allow is not None:
                print(f"[hermes_bridge] auto-allowing tool call (HAL_YOLO=1): {allow.name}")
                return m["RequestPermissionResponse"](
                    outcome=m["AllowedOutcome"](outcome="selected", option_id=allow.option_id)
                )
        print("[hermes_bridge] denying tool permission request (set HAL_YOLO=1 to allow)")
        cookie_id = _acp_to_cookie.get(session_id)
        if cookie_id:
            publish_event(cookie_id, {
                "type": "permission_denied",
                "tool_call_id": getattr(tool_call, "tool_call_id", None),
                "title": getattr(tool_call, "title", None) or "a tool",
            })
        return m["RequestPermissionResponse"](outcome=m["DeniedOutcome"](outcome="cancelled"))

    # We advertise no fs/terminal capabilities, so these should never fire.
    async def write_text_file(self, **kwargs):
        return None

    async def read_text_file(self, **kwargs):
        raise RuntimeError("fs capability not advertised")

    async def create_terminal(self, **kwargs):
        raise RuntimeError("terminal capability not advertised")

    async def terminal_output(self, **kwargs):
        raise RuntimeError("terminal capability not advertised")

    async def release_terminal(self, **kwargs):
        return None

    async def wait_for_terminal_exit(self, **kwargs):
        raise RuntimeError("terminal capability not advertised")

    async def kill_terminal(self, **kwargs):
        return None

    async def ext_method(self, method: str, params: dict) -> dict:
        return {}

    async def ext_notification(self, method: str, params: dict) -> None:
        return None

    def on_connect(self, conn) -> None:
        return None


class ACPBridge:
    def __init__(self):
        self._stack: AsyncExitStack | None = None
        self._conn = None
        self._proc = None
        self._client: _HALClient | None = None
        self._loaded: set[str] = set()
        self._restart_lock = asyncio.Lock()
        self._acp = None

    async def start(self) -> None:
        async with self._restart_lock:
            await self._ensure_started_locked()

    async def stop(self) -> None:
        async with self._restart_lock:
            await self._teardown_locked()

    async def _teardown_locked(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:
                print(f"[hermes_bridge] ACP teardown: {exc}")
        self._stack = None
        self._conn = None
        self._proc = None
        self._client = None
        self._loaded = set()

    def _alive(self) -> bool:
        return self._conn is not None and self._proc is not None and self._proc.returncode is None

    def health(self) -> dict:
        alive = self._alive()
        return {"alive": alive, "pid": self._proc.pid if alive else None}

    def forget(self, session_id: str) -> None:
        self._loaded.discard(session_id)

    async def _ensure_started_locked(self) -> None:
        if self._alive():
            return
        await self._teardown_locked()
        if self._acp is None:
            self._acp = _load_acp()
        m = self._acp
        log_path = (_data_dir or Path(".")) / "acp.log"
        try:
            # Append-mode forever otherwise; one rotation generation is enough.
            if log_path.stat().st_size > 5 * 1024 * 1024:
                log_path.replace(log_path.with_suffix(".log.1"))
        except OSError:
            pass
        log_file = open(log_path, "ab", buffering=0)
        self._client = _HALClient(m)
        self._stack = AsyncExitStack()
        self._stack.callback(log_file.close)
        env = {**os.environ, "HERMES_ACCEPT_HOOKS": "1"}
        conn, proc = await self._stack.enter_async_context(
            m["spawn_agent_process"](
                self._client,
                HERMES_ACP_BIN,
                cwd=AGENT_CWD,
                env=env,
                transport_kwargs={"stderr": log_file},
            )
        )
        await asyncio.wait_for(
            conn.initialize(
                protocol_version=m["PROTOCOL_VERSION"],
                client_capabilities=m["ClientCapabilities"](),
            ),
            timeout=60,
        )
        self._conn, self._proc = conn, proc
        print(f"[hermes_bridge] hermes-acp up (pid={proc.pid}, log={log_path})")

    async def _resolve_session(self, cookie_id: str) -> str:
        assert _session_map is not None
        if not self._alive():
            raise RuntimeError("ACP bridge is down during session resolution")
        acp_id = _session_map.get(cookie_id)
        if acp_id and acp_id in self._loaded:
            return acp_id
        if acp_id:
            try:
                resp = await asyncio.wait_for(
                    self._conn.load_session(cwd=AGENT_CWD, session_id=acp_id), timeout=60
                )
                if resp is not None:
                    self._loaded.add(acp_id)
                    return acp_id
            except Exception as exc:
                print(f"[hermes_bridge] session/load {acp_id} failed ({exc}); starting fresh")
        resp = await asyncio.wait_for(
            self._conn.new_session(cwd=AGENT_CWD, mcp_servers=[]), timeout=60
        )
        _session_map.set(cookie_id, resp.session_id)
        self._loaded.add(resp.session_id)
        return resp.session_id

    async def ask(self, text: str, cookie_id: str) -> str:
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                async with self._restart_lock:
                    await self._ensure_started_locked()
                session_id = await self._resolve_session(cookie_id)
                _acp_to_cookie[session_id] = cookie_id
                return await self._prompt(session_id, cookie_id, text)
            except asyncio.TimeoutError:
                return TIMEOUT_LINE
            except _TurnAborted:
                # Agent-side session state is gone; forget it and retry fresh.
                _session_map.drop(cookie_id)
                last_exc = None
                continue
            except Exception as exc:
                print(f"[hermes_bridge] ACP attempt {attempt} failed: {exc!r}")
                last_exc = exc
                await self.stop()
        if last_exc is not None:
            print(f"[hermes_bridge] giving up on ACP turn: {last_exc!r}")
        return FAILURE_LINE

    async def _prompt(self, session_id: str, cookie_id: str, text: str) -> str:
        m = self._acp
        assert self._client is not None
        self._client.begin(session_id)
        try:
            resp = await asyncio.wait_for(
                self._conn.prompt(
                    prompt=[m["TextContentBlock"](type="text", text=text)],
                    session_id=session_id,
                ),
                timeout=AGENT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._client.finish(session_id)
            try:
                await self._conn.cancel(session_id=session_id)
            except Exception:
                pass
            raise
        reply = self._client.finish(session_id)
        stop_reason = getattr(resp, "stop_reason", "end_turn")
        if not reply and stop_reason == "refusal":
            self._loaded.discard(session_id)
            raise _TurnAborted(f"prompt refused for {session_id}")
        return reply or FAILURE_LINE


_acp_bridge = ACPBridge() if BRIDGE_MODE == "acp" else None


# ---------------------------------------------------------------------------
# Subprocess mode — one `hermes chat -Q -q` per turn (fallback)
# ---------------------------------------------------------------------------


async def _ask_subprocess(text: str, session_id: str) -> str:
    assert _session_map is not None
    cmd = [HERMES_BIN, "chat", "-Q", "-q", "--source", SESSION_SOURCE, *EXTRA_ARGS]
    hermes_id = _session_map.get(session_id)
    if hermes_id:
        cmd += ["--resume", hermes_id]
    cmd += ["--", text]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=AGENT_CWD,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=AGENT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        print(f"[hermes_bridge] timeout after {AGENT_TIMEOUT}s (session={session_id})")
        return TIMEOUT_LINE

    stderr_text = _ANSI_RE.sub("", err.decode("utf-8", "replace"))
    match = _SESSION_ID_RE.search(stderr_text)
    if match and match.group(1) != hermes_id:
        _session_map.set(session_id, match.group(1))

    reply = _ANSI_RE.sub("", out.decode("utf-8", "replace")).strip()
    if proc.returncode != 0 or not reply:
        print(
            f"[hermes_bridge] hermes exit={proc.returncode} (session={session_id}) "
            f"stderr: {stderr_text.strip()[-2000:]}"
        )
        return reply or FAILURE_LINE
    return reply


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def startup() -> None:
    """Warm the persistent agent so the first spoken turn isn't slow."""
    if _acp_bridge is not None:
        try:
            await _acp_bridge.start()
        except Exception as exc:
            # Lazy retry happens on the first ask(); don't block server boot.
            print(f"[hermes_bridge] ACP warmup failed (will retry on first turn): {exc!r}")


async def shutdown() -> None:
    if _acp_bridge is not None:
        await _acp_bridge.stop()


def bridge_health() -> dict:
    """Liveness of the thinking half, for /api/health and /api/status."""
    info: dict = {"mode": BRIDGE_MODE}
    if _acp_bridge is None:
        # Subprocess mode has no persistent process to probe.
        info.update(alive=True, pid=None)
    else:
        info.update(_acp_bridge.health())
    return info


def drop_session(cookie_id: str) -> None:
    """Forget the Hermes session behind a browser session (/api/session/reset).
    The agent-side session record in ~/.hermes/state.db is left orphaned."""
    if _session_map is None:
        return
    acp_id = _session_map.get(cookie_id)
    _session_map.drop(cookie_id)
    if acp_id:
        _acp_to_cookie.pop(acp_id, None)
        if _acp_bridge is not None:
            _acp_bridge.forget(acp_id)
    _event_queues.pop(cookie_id, None)


async def ask_hermes(text: str, session_id: str) -> str:
    """Send one utterance to Hermes and return its reply text."""
    assert _session_map is not None, "hermes_bridge.init() not called"
    if not await asyncio.to_thread(_network_available):
        print("[hermes_bridge] offline preflight blocked remote inference")
        return OFFLINE_LINE
    async with _cookie_locks.hold(session_id):
        if _acp_bridge is not None:
            result = await _acp_bridge.ask(text, session_id)
        else:
            result = await _ask_subprocess(text, session_id)
    return result
