"""Autonomous mission orchestration for the HAL frontend.

A mission is a named, single-prompt background task that runs in its own
Hermes session while the user keeps talking on theirs. Lifecycle updates go
out over the owning browser session's SSE stream; completion is reported to
main.py through the on_complete callback (which speaks over the WebSocket).
"""
import asyncio
import glob
import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional

import hermes_bridge

# Missions created by scheduled/watch triggers rather than a browser session.
TRIGGER_COOKIE = "hal-triggers"
# Per-session cap on concurrently running missions — a misheard voice
# trigger must not be able to fan out unbounded agent runs.
MAX_ACTIVE_MISSIONS = int(os.environ.get("HAL_MAX_ACTIVE_MISSIONS", "3"))
# Seconds between trigger scans (data/triggers.json is re-read each scan,
# so edits take effect without a restart).
TRIGGERS_POLL = float(os.environ.get("HAL_TRIGGERS_POLL", "30"))
# Cap on queued mission reports waiting to be fed back to a session's brain.
MAX_PENDING_NOTES = 5


class MissionLimitError(RuntimeError):
    """Raised when a session already has MAX_ACTIVE_MISSIONS running."""


@dataclass
class Mission:
    id: str
    title: str
    cookie_id: str
    session_id: str
    status: Literal["active", "completed", "failed"] = "active"
    prompt: str = ""
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


class MissionManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.missions_dir = data_dir / "missions"
        self.missions_dir.mkdir(parents=True, exist_ok=True)
        self.missions: dict[str, Mission] = {}
        self.on_complete: Optional[Callable[[Mission], Awaitable[None]]] = None
        # asyncio.create_task results must stay referenced or a running
        # mission can be garbage-collected mid-flight.
        self._tasks: set[asyncio.Task] = set()
        # Mission reports queued for injection into the owning session's
        # next Hermes prompt — how results reach HAL's brain, not just the
        # spoken announcement. In-memory: a restart loses only the note, the
        # mission record itself is on disk.
        self._notes: dict[str, list[str]] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._load()

    def _load(self) -> None:
        for f in self.missions_dir.glob("*.json"):
            try:
                mission = Mission(**json.loads(f.read_text()))
            except (TypeError, ValueError, OSError) as exc:
                print(f"[mission_control] skipping unreadable mission {f.name}: {exc}")
                continue
            # A mission that was active when the server died can't be
            # resumed — its asyncio task is gone. Record the failure.
            if mission.status == "active":
                mission.status = "failed"
                mission.result = "Server restarted during mission."
                self._save(mission)
            self.missions[mission.id] = mission

    def _save(self, mission: Mission) -> None:
        f = self.missions_dir / f"{mission.id}.json"
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(mission), indent=2))
        tmp.replace(f)

    def active_count(self, cookie_id: str) -> int:
        return sum(
            1 for m in self.missions.values()
            if m.cookie_id == cookie_id and m.status == "active"
        )

    def create_mission(self, cookie_id: str, title: str, prompt: str) -> Mission:
        if self.active_count(cookie_id) >= MAX_ACTIVE_MISSIONS:
            raise MissionLimitError(
                f"{cookie_id} already has {MAX_ACTIVE_MISSIONS} active missions"
            )
        mission = Mission(
            id=str(uuid.uuid4()),
            title=title,
            cookie_id=cookie_id,
            session_id=str(uuid.uuid4()),
            prompt=prompt,
        )
        self.missions[mission.id] = mission
        self._save(mission)
        task = asyncio.create_task(
            self.run_mission(mission.id), name=f"mission-{mission.id[:8]}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return mission

    def list_missions(self, cookie_id: str) -> list[dict]:
        """A session's own missions plus trigger-created ones (those belong
        to everyone), newest first."""
        own = [
            m for m in self.missions.values()
            if m.cookie_id in (cookie_id, TRIGGER_COOKIE)
        ]
        own.sort(key=lambda m: m.created_at, reverse=True)
        return [asdict(m) for m in own[:50]]

    def drain_notes(self, cookie_id: str) -> list[str]:
        """Take (and clear) mission reports queued for this session's brain."""
        return self._notes.pop(cookie_id, [])

    async def run_mission(self, mission_id: str) -> None:
        mission = self.missions[mission_id]
        hermes_bridge.publish_event(
            mission.cookie_id, {"type": "mission_update", "mission": asdict(mission)}
        )
        # The mission runs in its own Hermes session; alias it so the
        # tool-call events it generates reach the owning browser's SSE stream.
        hermes_bridge.alias_events(mission.session_id, mission.cookie_id)
        try:
            result = await hermes_bridge.ask_hermes(mission.prompt, mission.session_id)
            mission.status = "completed"
            mission.result = result
        except Exception as exc:
            mission.status = "failed"
            mission.result = str(exc)
        finally:
            mission.finished_at = time.time()
            hermes_bridge.unalias_events(mission.session_id)
            # One-shot session: drop the cookie -> hermes-session mapping so
            # hermes_sessions.json doesn't accumulate an entry per mission.
            hermes_bridge.drop_session(mission.session_id)
            self._save(mission)
            if mission.status == "failed":
                self._journal_failure(mission)
            if mission.cookie_id != TRIGGER_COOKIE:
                # Queue the report for the owner's next prompt: the mission ran
                # in its own (now discarded) Hermes session, so this is the only
                # way the result reaches the brain behind the conversation.
                notes = self._notes.setdefault(mission.cookie_id, [])
                notes.append(
                    f'[System note: your background mission "{mission.title}" just '
                    f'{mission.status}. Report: {(mission.result or "(none)")[:2000]}]'
                )
                del notes[:-MAX_PENDING_NOTES]
            hermes_bridge.publish_event(
                mission.cookie_id, {"type": "mission_update", "mission": asdict(mission)}
            )
            if self.on_complete:
                await self.on_complete(mission)

    def _journal_failure(self, mission: Mission) -> None:
        """Append failed missions to a jsonl the reflection loop can analyze
        (reflection/reflection_loop.py --transcript data/missions/failed.jsonl)."""
        try:
            with (self.missions_dir / "failed.jsonl").open("a") as fh:
                fh.write(json.dumps({
                    "ts": mission.finished_at,
                    "title": mission.title,
                    "prompt": mission.prompt,
                    "result": (mission.result or "")[:4000],
                }) + "\n")
        except OSError as exc:
            print(f"[mission_control] failure journal write failed: {exc}")

    # ------------------------------------------------------------------
    # Scheduled + filesystem-watch triggers: data/triggers.json is a list of
    #   {"title": "...", "prompt": "...", "every_minutes": 60}        (cron-ish)
    #   {"title": "...", "prompt": "...", "watch": "~/Downloads/*"}   (watcher)
    # "enabled": false disables an entry. Interval triggers arm on first
    # sight (no boot storm); watchers baseline on first sight and fire when
    # the newest mtime under the glob advances.
    # ------------------------------------------------------------------

    def _load_triggers(self) -> list[dict]:
        path = self.data_dir / "triggers.json"
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        triggers = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            title = str(item.get("title", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            try:
                every = float(item.get("every_minutes", 0) or 0)
            except (TypeError, ValueError):
                every = 0.0
            watch = str(item.get("watch", "")).strip()
            if not title or not prompt or (every <= 0 and not watch):
                continue
            triggers.append(
                {"title": title, "prompt": prompt, "every_minutes": every, "watch": watch}
            )
        return triggers

    @staticmethod
    def _watch_mtime(pattern: str) -> float:
        latest = 0.0
        for path in glob.glob(os.path.expanduser(pattern), recursive=True)[:2000]:
            try:
                latest = max(latest, os.stat(path).st_mtime)
            except OSError:
                continue
        return latest

    def _scheduler_tick(self, state: dict[str, dict], now: float | None = None) -> None:
        now = time.time() if now is None else now
        for trig in self._load_triggers():
            st = state.setdefault(trig["title"], {})
            prompt = trig["prompt"]
            fire = False
            if trig["every_minutes"] > 0:
                next_run = st.get("next_run")
                if next_run is None:
                    st["next_run"] = now + trig["every_minutes"] * 60
                elif now >= next_run:
                    st["next_run"] = now + trig["every_minutes"] * 60
                    fire = True
            if trig["watch"]:
                latest = self._watch_mtime(trig["watch"])
                baseline = st.get("watch_mtime")
                if baseline is None:
                    st["watch_mtime"] = latest
                elif latest > baseline:
                    st["watch_mtime"] = latest
                    fire = True
                    prompt = f"{prompt}\n(Trigger: files changed under {trig['watch']})"
            if fire:
                try:
                    self.create_mission(TRIGGER_COOKIE, trig["title"], prompt)
                    print(f"[mission_control] trigger fired: {trig['title']}")
                except MissionLimitError:
                    print(f"[mission_control] trigger skipped (at mission cap): {trig['title']}")

    async def scheduler_loop(self) -> None:
        state: dict[str, dict] = {}
        while True:
            try:
                self._scheduler_tick(state)
            except Exception as exc:
                print(f"[mission_control] trigger tick failed: {exc!r}")
            await asyncio.sleep(TRIGGERS_POLL)

    def start_scheduler(self) -> None:
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(
                self.scheduler_loop(), name="mission-triggers"
            )

    async def stop_scheduler(self) -> None:
        task, self._scheduler_task = self._scheduler_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


manager: MissionManager | None = None


def init(data_dir: Path) -> None:
    global manager
    manager = MissionManager(data_dir)
