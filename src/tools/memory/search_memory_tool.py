#!/usr/bin/python3

from datetime import date, datetime, timedelta
from typing import Any

from src.memory.index import ConversationIndex
from src.tools.base import BaseTool

# Hard cap on how many days a single range request may span.
MAX_RANGE_DAYS: int = 62


class SearchMemoryTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SearchMemoryTool which searches past conversations by meaning
        and/or by date.
        """
        self._workspace_dir: str = workspace_dir
        super().__init__(
            name="search_memory",
            description=(
                "Search your past conversations with the user. Two ways to recall, which can be combined:\n"
                "- By meaning: pass `query` to find conversations about a topic, tool, project, decision, or name "
                "discussed days or weeks ago.\n"
                "- By date: pass `date` for one specific day, or `start_date` + `end_date` for a period, to recall what "
                "was discussed then. Date recall does NOT need a query — use it alone for questions like 'what did we talk "
                "about on the 15th' or 'what happened last week'.\n"
                "Resolve relative or natural dates (today, yesterday, 'last Wednesday', 'June 15') to ISO YYYY-MM-DD "
                "yourself using the current date before calling. Combine `query` with a date/range to search a topic within "
                "a time window. Returns matching transcript excerpts with their dates."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional. What to recall by meaning, in natural language, e.g. 'the database tool we discussed'. Omit to recall a whole day or period by date alone."
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional. A single day to recall, as ISO YYYY-MM-DD (e.g. '2026-06-15'). Mutually exclusive with start_date/end_date."
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional. Start of a date range to recall, inclusive, as ISO YYYY-MM-DD. Use together with end_date."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional. End of a date range to recall, inclusive, as ISO YYYY-MM-DD. Use together with start_date."
                    }
                },
                "required": []
            }
        )

    @staticmethod
    def _parse_iso(value: str) -> date | None:
        """
        This function parses a strict ISO YYYY-MM-DD date, returning None on any
        malformed input.
        """
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _resolve_dates(self, kwargs: dict[str, Any]) -> tuple[list[str] | None, str | None]:
        """
        This function turns the date/start_date/end_date arguments into an explicit
        list of ISO date strings, or an error message when the input is invalid.
        Returns (dates, error); dates is None when no date filter was requested.
        """
        single: str = (kwargs.get("date") or "").strip()
        start: str = (kwargs.get("start_date") or "").strip()
        end: str = (kwargs.get("end_date") or "").strip()

        if single and (start or end):
            return None, "Error: use either 'date' or 'start_date'+'end_date', not both."

        if single:
            parsed: date | None = self._parse_iso(single)
            if parsed is None:
                return None, f"Error: 'date' must be ISO YYYY-MM-DD, got '{single}'."
            return [parsed.isoformat()], None

        if start or end:
            if not (start and end):
                return None, "Error: a date range needs both 'start_date' and 'end_date'."
            start_d: date | None = self._parse_iso(start)
            end_d: date | None = self._parse_iso(end)
            if start_d is None or end_d is None:
                return None, "Error: 'start_date' and 'end_date' must be ISO YYYY-MM-DD."
            if start_d > end_d:
                return None, "Error: 'start_date' must not be after 'end_date'."
            span: int = (end_d - start_d).days + 1
            if span > MAX_RANGE_DAYS:
                return None, f"Error: date range too large ({span} days); keep it within {MAX_RANGE_DAYS} days."
            dates: list[str] = [(start_d + timedelta(days=i)).isoformat() for i in range(span)]
            return dates, None

        return None, None

    def execute(self, **kwargs: Any) -> str:
        """
        This function searches the conversation index by meaning and/or date and
        returns formatted excerpts.
        """
        query: str = (kwargs.get("query") or "").strip()
        dates, error = self._resolve_dates(kwargs=kwargs)
        if error:
            return error

        if not query and dates is None:
            return "Error: provide a 'query', a 'date', or a 'start_date'+'end_date' range."

        index: ConversationIndex = ConversationIndex.get_instance(workspace_dir=self._workspace_dir)
        matches: list[dict[str, Any]] = index.search(query=query, dates=dates)

        if not matches:
            if dates is not None and not query:
                scope: str = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"
                return f"No conversations found for {scope}."
            return "No matching conversations found."

        lines: list[str] = [
            f"**{match['date']}**\n{match['snippet']}"
            for match in matches
        ]

        return "\n\n---\n\n".join(lines)

