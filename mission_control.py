"""Autonomous mission orchestration for the HAL frontend.

A mission is a named, single-prompt background task that runs in its own
Hermes session while the user keeps talking on theirs. Lifecycle updates go
out over the owning browser session's SSE stream; completion is reported to
main.py through the on_complete callback (which speaks over the WebSocket).
"""
import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal, Optional

import hermes_bridge


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

    def create_mission(self, cookie_id: str, title: str, prompt: str) -> Mission:
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
        return [asdict(m) for m in self.missions.values() if m.cookie_id == cookie_id]

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
            hermes_bridge.unalias_events(mission.session_id)
            # One-shot session: drop the cookie -> hermes-session mapping so
            # hermes_sessions.json doesn't accumulate an entry per mission.
            hermes_bridge.drop_session(mission.session_id)
            self._save(mission)
            hermes_bridge.publish_event(
                mission.cookie_id, {"type": "mission_update", "mission": asdict(mission)}
            )
            if self.on_complete:
                await self.on_complete(mission)


manager: MissionManager | None = None


def init(data_dir: Path) -> None:
    global manager
    manager = MissionManager(data_dir)
