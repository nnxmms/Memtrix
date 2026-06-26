#!/usr/bin/python3

import json
import logging
import os
import threading
import time
import uuid
from datetime import date, timedelta
from typing import Any

import chromadb

from src.core.config import CONFIG_PATH
from src.memory.index import LocalEmbeddingFunction
from src.memory.store import _make_chroma_client, slugify

logger: logging.Logger = logging.getLogger(__name__)

# Event lifecycle status values.
STATUS_UPCOMING: str = "upcoming"
STATUS_PAST: str = "past"


def _parse_iso(date_iso: str) -> date | None:
    """
    This function parses an ISO date string (YYYY-MM-DD) into a date, returning None
    when the value is missing or malformed.
    """
    text: str = (date_iso or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class EventStore:
    """
    This is the EventStore which holds time-anchored events the user mentions (a
    birthday, a trip, a deadline) in a dedicated vector collection. Events power the
    proactive upcoming-events block injected into the agent's context and the
    one-time follow-up nudge after an event passes. Each event is keyed by a
    title+date slug so repeated mentions reinforce rather than duplicate.
    """

    _instances: dict[str, "EventStore"] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls, workspace_dir: str) -> "EventStore":
        """
        This function returns the EventStore for a workspace, creating it once.
        """
        with cls._lock:
            if workspace_dir not in cls._instances:
                cls._instances[workspace_dir] = cls(workspace_dir=workspace_dir)
            return cls._instances[workspace_dir]

    def __init__(self, workspace_dir: str) -> None:
        """
        This initializes the EventStore for a workspace, opening (or creating) the
        shared "events" vector collection.
        """
        self._workspace_dir: str = workspace_dir
        self._write_lock: threading.Lock = threading.Lock()

        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction.get_instance(model_dir=model_dir)
        persist_dir: str = os.path.join(data_dir, "representations", "events")
        self._client: chromadb.ClientAPI = _make_chroma_client(persist_dir=persist_dir)
        self._collection: chromadb.Collection = self._client.get_or_create_collection(
            name="events",
            embedding_function=self._embedding_fn,
        )
        logger.info("Event store ready (records=%d)", self._collection.count())

    def count(self) -> int:
        """
        This function returns the number of stored events.
        """
        return self._collection.count()

    @staticmethod
    def _event_key(title: str, date_iso: str) -> str:
        """
        This function returns the stable identity key for an event (title slug + date),
        used to reinforce repeated mentions instead of duplicating them.
        """
        return f"{slugify(title)}@{(date_iso or '').strip()[:10]}"

    def add_event(self, title: str, date_iso: str, entities: list[str] | None = None,
                  location: str = "", time_of_day: str = "", recurring: bool = False,
                  source: str = "derived") -> bool:
        """
        This function records an event keyed by title+date. A repeated mention of the
        same event refreshes its timestamp rather than creating a duplicate. Returns
        True when a new event was created, False when an existing one was reinforced
        or the input was invalid.
        """
        title = (title or "").strip()
        when: date | None = _parse_iso(date_iso)
        if not title or when is None:
            return False

        key: str = self._event_key(title=title, date_iso=date_iso)
        entity_slugs: list[str] = [slugify(e) for e in (entities or []) if (e or "").strip()]
        document: str = " ".join([title, location, " ".join(entities or [])]).strip()

        with self._write_lock:
            existing: dict[str, Any] = self._collection.get(where={"key": key})
            ids: list[str] = existing.get("ids", []) or []
            if ids:
                # Reinforce: refresh recency and keep the most specific fields.
                self._collection.update(
                    ids=[ids[0]],
                    metadatas=[{"updated_ts": time.time()}],
                )
                logger.info("Reinforced event '%s' on %s", title, date_iso)
                return False

            self._collection.add(
                ids=[str(uuid.uuid4())],
                documents=[document or title],
                metadatas=[{
                    "key": key,
                    "title": title,
                    "date": when.isoformat(),
                    "date_ord": when.toordinal(),
                    "time_of_day": (time_of_day or "").strip(),
                    "entities": json.dumps(entity_slugs),
                    "entity_names": json.dumps([e.strip() for e in (entities or []) if (e or "").strip()]),
                    "location": (location or "").strip(),
                    "status": STATUS_UPCOMING,
                    "recurring": bool(recurring),
                    "reviewed": False,
                    "source": source,
                    "created_ts": time.time(),
                    "updated_ts": time.time(),
                }],
            )
        logger.info("Stored event '%s' on %s", title, date_iso)
        return True

    def _to_record(self, record_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        """
        This function converts raw Chroma metadata into a clean event dict.
        """
        try:
            entities: list[str] = json.loads(meta.get("entities", "[]"))
        except (json.JSONDecodeError, TypeError):
            entities = []
        try:
            entity_names: list[str] = json.loads(meta.get("entity_names", "[]"))
        except (json.JSONDecodeError, TypeError):
            entity_names = []
        return {
            "id": record_id,
            "title": meta.get("title", ""),
            "date": meta.get("date", ""),
            "date_ord": int(meta.get("date_ord", 0)),
            "time_of_day": meta.get("time_of_day", "") or "",
            "entities": entities,
            "entity_names": entity_names,
            "location": meta.get("location", "") or "",
            "status": meta.get("status", STATUS_UPCOMING),
            "recurring": bool(meta.get("recurring", False)),
            "reviewed": bool(meta.get("reviewed", False)),
            "source": meta.get("source", "derived"),
            "created_ts": float(meta.get("created_ts", 0.0)),
        }

    def _all(self) -> list[dict[str, Any]]:
        """
        This function returns every stored event as clean dicts.
        """
        if self._collection.count() == 0:
            return []
        results: dict[str, Any] = self._collection.get()
        ids: list[str] = results.get("ids", []) or []
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []
        return [self._to_record(record_id=i, meta=m) for i, m in zip(ids, metadatas)]

    def upcoming(self, within_days: int = 7, today: date | None = None) -> list[dict[str, Any]]:
        """
        This function returns events occurring from today through the next
        `within_days` days, soonest first, for proactive injection into context.
        """
        ref: date = today or date.today()
        start: int = ref.toordinal()
        end: int = (ref + timedelta(days=max(0, within_days))).toordinal()
        events: list[dict[str, Any]] = [
            e for e in self._all() if start <= e["date_ord"] <= end
        ]
        events.sort(key=lambda e: e["date_ord"])
        return events

    def recently_passed(self, within_days: int = 2, today: date | None = None,
                         unreviewed_only: bool = True) -> list[dict[str, Any]]:
        """
        This function returns events whose date fell within the last `within_days`
        days, used to deliver a one-time post-event follow-up. When unreviewed_only is
        set, events already acknowledged are excluded.
        """
        ref: date = today or date.today()
        start: int = (ref - timedelta(days=max(0, within_days))).toordinal()
        end: int = ref.toordinal() - 1
        events: list[dict[str, Any]] = [
            e for e in self._all()
            if start <= e["date_ord"] <= end and (not unreviewed_only or not e["reviewed"])
        ]
        events.sort(key=lambda e: e["date_ord"])
        return events

    def mark_reviewed(self, event_id: str) -> None:
        """
        This function flags an event as acknowledged so its post-event follow-up is
        delivered only once.
        """
        with self._write_lock:
            self._collection.update(ids=[event_id], metadatas=[{"reviewed": True}])

    def maintain(self, today: date | None = None, retention_days: int = 30) -> int:
        """
        This function performs periodic event upkeep: marks elapsed events as past,
        rolls recurring (annual) events forward to their next occurrence, and prunes
        non-recurring past events older than the retention window. Returns the number
        of events removed.
        """
        ref: date = today or date.today()
        ref_ord: int = ref.toordinal()
        cutoff_ord: int = (ref - timedelta(days=max(0, retention_days))).toordinal()
        removed: int = 0
        with self._write_lock:
            for event in self._all():
                when: date | None = _parse_iso(event["date"])
                if when is None:
                    continue
                if event["date_ord"] < ref_ord:
                    if event["recurring"]:
                        # Roll an annual event forward to its next future occurrence.
                        next_when: date = when
                        while next_when.toordinal() < ref_ord:
                            try:
                                next_when = next_when.replace(year=next_when.year + 1)
                            except ValueError:
                                next_when = next_when + timedelta(days=365)
                        new_key: str = self._event_key(title=event["title"], date_iso=next_when.isoformat())
                        self._collection.update(
                            ids=[event["id"]],
                            metadatas=[{
                                "key": new_key,
                                "date": next_when.isoformat(),
                                "date_ord": next_when.toordinal(),
                                "status": STATUS_UPCOMING,
                                "reviewed": False,
                            }],
                        )
                        continue
                    if event["date_ord"] < cutoff_ord:
                        self._collection.delete(ids=[event["id"]])
                        removed += 1
                        continue
                    if event["status"] != STATUS_PAST:
                        self._collection.update(ids=[event["id"]], metadatas=[{"status": STATUS_PAST}])
        if removed:
            logger.info("Pruned %d past event(s)", removed)
        return removed

    def list_all(self) -> list[dict[str, Any]]:
        """
        This function returns every stored event, soonest date first, for the web
        admin and the memory_event tool.
        """
        events: list[dict[str, Any]] = self._all()
        events.sort(key=lambda e: e["date_ord"])
        return events

    def for_entity(self, slug: str) -> list[dict[str, Any]]:
        """
        This function returns events linked to a given entity slug, soonest first.
        """
        events: list[dict[str, Any]] = [e for e in self._all() if slug in e["entities"]]
        events.sort(key=lambda e: e["date_ord"])
        return events

    def delete(self, event_id: str) -> bool:
        """
        This function removes a single event by id.
        """
        with self._write_lock:
            self._collection.delete(ids=[event_id])
        return True

    def wipe(self) -> int:
        """
        This function removes every stored event. Returns the number removed.
        """
        with self._write_lock:
            count: int = self._collection.count()
            if count:
                results: dict[str, Any] = self._collection.get()
                ids: list[str] = results.get("ids", []) or []
                if ids:
                    self._collection.delete(ids=ids)
        logger.info("Wiped %d event(s)", count)
        return count
