import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

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
        self.on_complete: Optional[Callable[[Mission], asyncio.Task]] = None
        self._load()

    def _load(self):
        for f in self.missions_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                m = Mission(**data)
                # If active on restart, fail it
                if m.status == "active":
                    m.status = "failed"
                    m.result = "Server restarted during mission."
                    self._save(m)
                self.missions[m.id] = m
            except Exception:
                pass

    def _save(self, mission: Mission):
        f = self.missions_dir / f"{mission.id}.json"
        f.write_text(json.dumps(asdict(mission), indent=2))

    def create_mission(self, cookie_id: str, title: str, prompt: str) -> Mission:
        m = Mission(
            id=str(uuid.uuid4()),
            title=title,
            cookie_id=cookie_id,
            session_id=str(uuid.uuid4()),
            prompt=prompt
        )
        self.missions[m.id] = m
        self._save(m)
        asyncio.create_task(self.run_mission(m.id))
        return m

    def list_missions(self, cookie_id: str) -> list[dict]:
        return [asdict(m) for m in self.missions.values() if m.cookie_id == cookie_id]

    async def run_mission(self, mission_id: str):
        m = self.missions[mission_id]
        
        hermes_bridge._publish(m.cookie_id, {
            "type": "mission_update",
            "mission": asdict(m)
        })
        
        try:
            # Map this session to the user's cookie so they see the tool events
            hermes_bridge._acp_to_cookie[m.session_id] = m.cookie_id
            
            result = await hermes_bridge.ask_hermes(m.prompt, m.session_id)
            m.status = "completed"
            m.result = result
        except Exception as e:
            m.status = "failed"
            m.result = str(e)
        finally:
            hermes_bridge._acp_to_cookie.pop(m.session_id, None)
            self._save(m)
            hermes_bridge._publish(m.cookie_id, {
                "type": "mission_update",
                "mission": asdict(m)
            })
            
            if self.on_complete:
                await self.on_complete(m)

manager: MissionManager | None = None

def init(data_dir: Path):
    global manager
    manager = MissionManager(data_dir)
