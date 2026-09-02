"""Explicit compatibility adapter for the legacy Hermes Agent bridge."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType


class HermesProvider:
    name = "hermes"

    def __init__(self) -> None:
        self._bridge: ModuleType | None = None

    @property
    def bridge(self) -> ModuleType:
        if self._bridge is None:
            self._bridge = importlib.import_module("hermes_bridge")
        return self._bridge

    @property
    def mode(self) -> str:
        return str(self.bridge.BRIDGE_MODE)

    @property
    def permission_mode(self) -> str:
        return str(self.bridge.PERMISSION_MODE)

    @property
    def agent_cwd(self) -> str:
        return str(self.bridge.AGENT_CWD)

    def init(self, data_dir: Path) -> None:
        self.bridge.init(data_dir)

    async def startup(self) -> None:
        await self.bridge.startup()

    async def shutdown(self) -> None:
        await self.bridge.shutdown()

    async def ask(self, text: str, session_id: str) -> str:
        return await self.bridge.ask_hermes(text, session_id)

    async def cancel(self, session_id: str) -> None:
        await self.bridge.cancel_session(session_id)

    def drop_session(self, session_id: str) -> None:
        self.bridge.drop_session(session_id)

    def health(self) -> dict:
        return {**self.bridge.bridge_health(), "provider": self.name}

    async def commands(self, session_id: str) -> list[dict[str, str | None]]:
        return await self.bridge.list_slash_commands(session_id)

    def provider_session_for(self, session_id: str) -> str | None:
        return self.bridge.acp_session_for(session_id)

    async def run_diagnostic_command(self, args: list[str], timeout: float) -> dict:
        import asyncio
        import os

        env = {
            **os.environ,
            "TERM": "dumb",
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            process = await asyncio.create_subprocess_exec(
                self.bridge.HERMES_BIN,
                *args,
                cwd=self.agent_cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return {"ok": False, "code": None, "text": f"Hermes command unavailable: {error}"}
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "ok": False,
                "code": None,
                "text": f"Hermes command timed out after {timeout:g}s.",
            }
        out = stdout.decode("utf-8", "replace")
        err = stderr.decode("utf-8", "replace")
        text = out if process.returncode == 0 else "\n".join(part for part in (out, err) if part)
        return {"ok": process.returncode == 0, "code": process.returncode, "text": text}

    def __getattr__(self, name: str):
        return getattr(self.bridge, name)
