#!/usr/bin/python3

from datetime import date
from typing import Any

from src.memory.events import EventStore
from src.tools.base import BaseTool


class MemoryEventTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryEventTool which lets the agent explicitly log, list, or
        cancel a time-anchored event (a birthday, trip, deadline) so it can be
        surfaced proactively as it approaches. Most events are captured automatically
        in the background; use this for an explicit "remind me about X" request.
        """
        self._workspace_dir: str = workspace_dir
        self._store: EventStore | None = None
        super().__init__(
            name="memory_event",
            description=(
                "Log, list, or cancel an event you should proactively remember (a birthday, "
                "trip, meeting, deadline). Upcoming events are surfaced to you automatically as "
                "they approach. Use action 'log' when the user asks you to remember a dated "
                "event, 'list' to review known events, and 'cancel' to forget one. Resolve "
                "relative dates to a concrete ISO YYYY-MM-DD yourself first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["log", "list", "cancel"],
                        "description": "What to do: 'log' a new event, 'list' known events, or 'cancel' one.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short event name, e.g. \"Jenna's birthday party\". Required for 'log' and 'cancel'.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Event date as ISO YYYY-MM-DD. Required for 'log' (and for 'cancel' to disambiguate).",
                    },
                    "time_of_day": {
                        "type": "string",
                        "description": "Optional clock time or part of day, e.g. '19:00' or 'evening'.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location.",
                    },
                    "people": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional names of people involved.",
                    },
                    "recurring": {
                        "type": "boolean",
                        "description": "Set true for an annually recurring event (birthday, anniversary).",
                    },
                },
                "required": ["action"],
            },
        )

    def set_event_store(self, store: EventStore) -> None:
        """
        This function injects the event store dependency.
        """
        self._store = store

    def execute(self, **kwargs: Any) -> str:
        """
        This function logs, lists, or cancels an event.
        """
        if self._store is None:
            return "Event memory is not available."

        action: str = (kwargs.get("action") or "").strip().lower()
        if action == "list":
            return self._list()
        if action == "log":
            return self._log(kwargs=kwargs)
        if action == "cancel":
            return self._cancel(kwargs=kwargs)
        return "Error: action must be 'log', 'list', or 'cancel'."

    def _list(self) -> str:
        """
        This function renders all known events as a compact list.
        """
        events: list[dict[str, Any]] = self._store.list_all()
        if not events:
            return "No events recorded."
        lines: list[str] = []
        for event in events:
            lines.append(f"- {self._format(event=event)}")
        return "Known events:\n" + "\n".join(lines)

    def _log(self, kwargs: dict[str, Any]) -> str:
        """
        This function stores a new event.
        """
        title: str = (kwargs.get("title") or "").strip()
        date_iso: str = (kwargs.get("date") or "").strip()
        if not title or not date_iso:
            return "Error: 'log' requires both 'title' and 'date' (ISO YYYY-MM-DD)."
        people: Any = kwargs.get("people", [])
        created: bool = self._store.add_event(
            title=title,
            date_iso=date_iso,
            entities=people if isinstance(people, list) else [],
            location=(kwargs.get("location") or "").strip(),
            time_of_day=(kwargs.get("time_of_day") or "").strip(),
            recurring=bool(kwargs.get("recurring", False)),
            source="manual",
        )
        if not created:
            return "That event is already on file (or the date was invalid)."
        return f"Logged: {title} on {date_iso}."

    def _cancel(self, kwargs: dict[str, Any]) -> str:
        """
        This function removes a matching event by title (and date when given).
        """
        title: str = (kwargs.get("title") or "").strip().lower()
        date_iso: str = (kwargs.get("date") or "").strip()[:10]
        if not title:
            return "Error: 'cancel' requires the event 'title'."
        removed: int = 0
        for event in self._store.list_all():
            if event["title"].strip().lower() != title:
                continue
            if date_iso and event["date"] != date_iso:
                continue
            self._store.delete(event_id=event["id"])
            removed += 1
        if not removed:
            return "No matching event found."
        return f"Cancelled {removed} event(s)."

    @staticmethod
    def _format(event: dict[str, Any]) -> str:
        """
        This function renders one event as a one-liner.
        """
        parts: list[str] = [str(event.get("title", "")).strip(), f"— {event.get('date', '')}"]
        time_of_day: str = str(event.get("time_of_day", "")).strip()
        if time_of_day:
            parts.append(time_of_day)
        location: str = str(event.get("location", "")).strip()
        if location:
            parts.append(f"@ {location}")
        if event.get("recurring"):
            parts.append("(annual)")
        return " ".join(p for p in parts if p)
