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
# Seconds a finished mission's Hermes session stays alive for follow-up
# questions ("HAL, ask the mission: …") before the reaper drops it.
STEERABLE_TTL = float(os.environ.get("HAL_MISSION_STEERABLE_TTL", str(30 * 60)))


class MissionLimitError(RuntimeError):
    """Raised when a session already has MAX_ACTIVE_MISSIONS running."""


def _parse_at(raw) -> Optional[tuple[int, int]]:
    """Validate a daily-trigger time of day; "07:30" -> (7, 30)."""
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return hh, mm
    return None


def _last_occurrence(at: tuple[int, int], now: float) -> float:
    """Epoch of the most recent local-time occurrence of HH:MM at or before
    now. mktime resolves DST (tm_isdst=-1); the day-step back can be an hour
    off across a DST switch, which for a daily trigger is acceptable."""
    lt = time.localtime(now)
    sched = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, at[0], at[1], 0, 0, 0, -1))
    if sched > now:
        sched -= 86400
    return sched


@dataclass
class Mission:
    id: str
    title: str
    cookie_id: str
    session_id: str
    status: Literal["active", "completed", "failed", "cancelled"] = "active"
    prompt: str = ""
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # Tool-permission requests from this mission are auto-allowed (ACP mode),
    # overriding HAL_PERMISSION_MODE. Granted only by a trigger's
    # "permissions": "allow" — the trigger file is the trust boundary.
    allow_tools: bool = False
    # A finished mission stays "steerable" — its Hermes session alive for
    # follow-up questions — until dismissed or reaped (STEERABLE_TTL).
    session_dropped: bool = False
    dismissed_at: Optional[float] = None


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

    def create_mission(
        self, cookie_id: str, title: str, prompt: str, allow_tools: bool = False
    ) -> Mission:
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
            allow_tools=allow_tools,
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
        to everyone), newest first. Dismissed missions stay on disk but
        leave the board."""
        own = [
            m for m in self.missions.values()
            if m.cookie_id in (cookie_id, TRIGGER_COOKIE) and m.dismissed_at is None
        ]
        own.sort(key=lambda m: m.created_at, reverse=True)
        return [asdict(m) for m in own[:50]]

    def _visible_to(self, mission_id: str, cookie_id: str) -> Optional[Mission]:
        """The mission, if this browser session may act on it. Trigger
        missions belong to everyone — same visibility rule as list_missions."""
        mission = self.missions.get(mission_id)
        if mission is None or mission.cookie_id not in (cookie_id, TRIGGER_COOKIE):
            return None
        return mission

    def latest_active(self, cookie_id: str) -> Optional[Mission]:
        candidates = [
            m for m in self.missions.values()
            if m.cookie_id in (cookie_id, TRIGGER_COOKIE) and m.status == "active"
        ]
        return max(candidates, key=lambda m: m.created_at, default=None)

    def steerable_mission(self, cookie_id: str) -> Optional[Mission]:
        """Newest finished mission whose Hermes session is still alive —
        the target for follow-up questions."""
        now = time.time()
        candidates = [
            m for m in self.missions.values()
            if m.cookie_id in (cookie_id, TRIGGER_COOKIE)
            and m.status != "active"
            and not m.session_dropped
            and m.dismissed_at is None
            and m.finished_at is not None
            and now - m.finished_at <= STEERABLE_TTL
        ]
        return max(candidates, key=lambda m: m.finished_at, default=None)

    async def cancel_mission(self, mission_id: str, cookie_id: str) -> Optional[Mission]:
        """Mark a running mission cancelled and interrupt its agent turn.
        run_mission's bookkeeping respects the flag when the turn returns."""
        mission = self._visible_to(mission_id, cookie_id)
        if mission is None or mission.status != "active":
            return None
        mission.status = "cancelled"
        self._save(mission)
        await hermes_bridge.cancel_session(mission.session_id)
        return mission

    def dismiss_mission(self, mission_id: str, cookie_id: str) -> bool:
        """Drop a finished mission from the board and release its session."""
        mission = self._visible_to(mission_id, cookie_id)
        if mission is None or mission.status == "active":
            return False
        if not mission.session_dropped:
            hermes_bridge.drop_session(mission.session_id)
            mission.session_dropped = True
        mission.dismissed_at = time.time()
        self._save(mission)
        hermes_bridge.publish_event(
            mission.cookie_id, {"type": "mission_update", "mission": asdict(mission)}
        )
        return True

    def _reap_sessions(self, now: float | None = None) -> None:
        """Release Hermes sessions of missions past their steerable window."""
        now = time.time() if now is None else now
        for mission in self.missions.values():
            if (
                mission.status != "active"
                and not mission.session_dropped
                and mission.finished_at is not None
                and now - mission.finished_at > STEERABLE_TTL
            ):
                hermes_bridge.drop_session(mission.session_id)
                mission.session_dropped = True
                self._save(mission)

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
        if mission.allow_tools:
            hermes_bridge.allow_tools_for(mission.session_id)
        try:
            result = await hermes_bridge.ask_hermes(mission.prompt, mission.session_id)
            # cancel_mission may have flipped the status while the turn was
            # in flight — a cancelled mission must not report as completed.
            if mission.status == "cancelled":
                mission.result = "Cancelled by Dave."
            else:
                mission.status = "completed"
                mission.result = result
        except Exception as exc:
            if mission.status == "cancelled":
                mission.result = "Cancelled by Dave."
            else:
                mission.status = "failed"
                mission.result = str(exc)
        finally:
            mission.finished_at = time.time()
            hermes_bridge.disallow_tools_for(mission.session_id)
            hermes_bridge.unalias_events(mission.session_id)
            # The session stays alive for follow-up questions ("HAL, ask the
            # mission: …"); dismiss_mission or _reap_sessions releases it.
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
    #   {"title": "...", "prompt": "...", "at": "07:30"}              (daily)
    # "enabled": false disables an entry; "permissions": "allow" auto-allows
    # the mission's tool-permission requests (ACP mode) regardless of
    # HAL_PERMISSION_MODE — the trigger file is the trust boundary.
    # Interval triggers arm on first sight (no boot storm); watchers baseline
    # on first sight and fire when the newest mtime under the glob advances;
    # "at" triggers arm on first sight, then fire once per day — including a
    # catch-up fire when a tick discovers the time passed while the server
    # was down or the laptop asleep.
    # Trigger state persists in data/trigger_state.json so a restart doesn't
    # re-arm intervals or re-baseline watchers.
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
            at = _parse_at(item.get("at"))
            if not title or not prompt or (every <= 0 and not watch and at is None):
                continue
            triggers.append({
                "title": title,
                "prompt": prompt,
                "every_minutes": every,
                "watch": watch,
                "at": at,
                "allow_tools": str(item.get("permissions", "")).strip().lower() == "allow",
            })
        return triggers

    def _load_trigger_state(self) -> dict[str, dict]:
        try:
            state = json.loads((self.data_dir / "trigger_state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save_trigger_state(self, state: dict[str, dict]) -> None:
        f = self.data_dir / "trigger_state.json"
        tmp = f.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(state, indent=1))
            tmp.replace(f)
        except OSError as exc:
            print(f"[mission_control] trigger state write failed: {exc}")

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
            if trig["at"] is not None:
                # Fire when the most recent daily occurrence postdates the
                # last fire. With persisted state this also catches up a time
                # that passed while the server was down — once, not per day.
                sched = _last_occurrence(trig["at"], now)
                last = st.get("last_at")
                if last is None:
                    st["last_at"] = now  # arm on first sight — no boot storm
                elif last < sched:
                    st["last_at"] = now
                    fire = True
            if fire:
                try:
                    self.create_mission(
                        TRIGGER_COOKIE, trig["title"], prompt,
                        allow_tools=trig["allow_tools"],
                    )
                    print(f"[mission_control] trigger fired: {trig['title']}")
                except MissionLimitError:
                    print(f"[mission_control] trigger skipped (at mission cap): {trig['title']}")

    async def scheduler_loop(self) -> None:
        state = self._load_trigger_state()
        saved = json.dumps(state, sort_keys=True)
        while True:
            try:
                self._scheduler_tick(state)
                self._reap_sessions()
                snapshot = json.dumps(state, sort_keys=True)
                if snapshot != saved:
                    self._save_trigger_state(state)
                    saved = snapshot
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
