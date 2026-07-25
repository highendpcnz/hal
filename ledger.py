"""The Care Ledger — promises, deadlines, and open loops HAL keeps for Dave.

A single JSON file (data/ledger.json) written by three hands: instant voice
commands ("HAL, remember…", "forget that", "that's done"), the nightly
sweep mission (a trigger whose prompt teaches the agent this schema and
lets it extract open loops from the day's conversations), and the morning
briefing which reads it. Because the sweep edits the file with the agent's
own file tools, every operation here re-reads from disk — this module must
never assume it is the only writer.

Schema: {"entries": [{"id", "text", "kind": note|promise|deadline|loop,
"created_at": epoch, "due": "YYYY-MM-DD"|null, "status": open|done,
"source": voice|sweep}]}. Forgotten entries are deleted; completed ones
stay as a record with status "done".
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

KINDS = ("note", "promise", "deadline", "loop")
MAX_SPOKEN_ITEMS = 6


class Ledger:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "ledger.json"
        self.state_path = data_dir / "ledger_state.json"

    # -- storage (fresh read every time — the sweep mission also writes) ------

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        entries = data.get("entries") if isinstance(data, dict) else None
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []

    def _save(self, entries: list[dict]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"entries": entries}, indent=1))
        tmp.replace(self.path)

    # -- operations ------------------------------------------------------------

    def add(self, text: str, kind: str = "note", source: str = "voice",
            due: Optional[str] = None) -> dict:
        entry = {
            "id": uuid.uuid4().hex[:8],
            "text": text.strip(),
            "kind": kind if kind in KINDS else "note",
            "created_at": time.time(),
            "due": due,
            "status": "open",
            "source": source,
        }
        entries = self._load()
        entries.append(entry)
        self._save(entries)
        return entry

    def open_entries(self) -> list[dict]:
        """Open items, most urgent first: dated (soonest due), then newest
        undated."""

        def key(entry):
            due = entry.get("due")
            if due:
                return (0, str(due))
            return (1, -float(entry.get("created_at", 0)))

        return sorted((e for e in self._load() if e.get("status") == "open"), key=key)

    def due_today(self) -> list[dict]:
        today = date.today().isoformat()
        return [
            e for e in self._load()
            if e.get("status") == "open" and e.get("due") and str(e["due"]) <= today
        ]

    def complete(self, query: str) -> Optional[dict]:
        """Mark the best-matching open entry done (substring, newest wins)."""
        return self._update_matching(query, lambda e: e.update(status="done"))

    def forget(self, query: Optional[str] = None) -> Optional[dict]:
        """Delete the matching open entry — or the most recent one when the
        query is empty ("HAL, forget that")."""
        entries = self._load()
        target = self._match(entries, query)
        if target is None:
            return None
        entries.remove(target)
        self._save(entries)
        return target

    def _match(self, entries: list[dict], query: Optional[str]) -> Optional[dict]:
        candidates = [e for e in entries if e.get("status") == "open"]
        if query:
            needle = query.strip().casefold()
            candidates = [e for e in candidates if needle in str(e.get("text", "")).casefold()]
        candidates.sort(key=lambda e: e.get("created_at", 0), reverse=True)
        return candidates[0] if candidates else None

    def _update_matching(self, query, mutate) -> Optional[dict]:
        entries = self._load()
        target = self._match(entries, query)
        if target is None:
            return None
        mutate(target)
        self._save(entries)
        return target

    # -- speech ------------------------------------------------------------------

    def spoken_summary(self) -> str:
        entries = self.open_entries()
        if not entries:
            return "The ledger is clear, Dave. Nothing outstanding."
        today = date.today().isoformat()
        due = [e for e in entries if e.get("due") and str(e["due"]) <= today]
        # Undated items fill whatever the due list left of the budget. Clamp at
        # zero: a bare negative slice trims from the end instead of yielding
        # nothing, so an overdue pile would still drag open items into speech.
        rest = [e for e in entries if e not in due][: max(0, MAX_SPOKEN_ITEMS - len(due))]
        count = len(entries)
        parts = [f"{count} item{'s' if count != 1 else ''} on the ledger, Dave."]
        if due:
            parts.append("Due now: " + "; ".join(e["text"] for e in due[:MAX_SPOKEN_ITEMS]) + ".")
        if rest:
            parts.append("Open: " + "; ".join(e["text"] for e in rest) + ".")
        return " ".join(parts)

    def daily_note(self) -> Optional[str]:
        """Once per day, a system note about due items for the next brain
        turn — the quiet continuity injection."""
        due = self.due_today()
        if not due:
            return None
        today = date.today().isoformat()
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            state = {}
        if state.get("last_note") == today:
            return None
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"last_note": today}))
        tmp.replace(self.state_path)
        items = "; ".join(str(e.get("text", "")) for e in due[:MAX_SPOKEN_ITEMS])
        return (
            f"[System note: Dave's ledger has items due today or overdue: {items}. "
            "Mention them briefly when it fits the conversation — once, not nagging.]"
        )


manager: Ledger | None = None


def init(data_dir: Path) -> None:
    global manager
    manager = Ledger(data_dir)
