#!/usr/bin/python3

import json
import logging
import os
import re
import threading
import time
from datetime import date
from typing import Any

from src.core.config import CONFIG_PATH
from src.core.lifecycle import PAUSE_SENTINEL
from src.providers.base import BaseProvider
from src.memory.events import EventStore
from src.memory.store import KINDS, REASONING_LEVEL_ITEMS, RepresentationStore, slugify

logger: logging.Logger = logging.getLogger(__name__)

# Map a conversation role to the peer whose representation it informs (user only)
ROLE_TO_PEER: dict[str, str] = {"user": "user"}

# Idle flush interval — stragglers below the token threshold are flushed after this
IDLE_FLUSH_SECONDS: float = 90.0

# Re-curate the finite peer card after this many reasoning passes for a peer
CARD_REFRESH_EVERY: int = 3

# Cap curation input size so one verbose conclusion cannot dominate the card
CARD_SOURCE_LIMIT: int = 40
CARD_SOURCE_ITEM_MAX_CHARS: int = 220

# Run one compression retry when the first draft significantly exceeds budget
CARD_RETRY_OVERAGE_CHARS: int = 80

# Peer -> config key that, when true, freezes that peer card against re-curation
FREEZE_FLAGS: dict[str, str] = {"user": "freeze_user_card"}

# Persisted timestamp of the last consolidation pass (survives restarts)
CONSOLIDATION_STATE_FILE: str = os.path.join(os.path.dirname(CONFIG_PATH), ".last-consolidation")

# How often the consolidation scheduler checks whether a pass is due
CONSOLIDATION_CHECK_SECONDS: float = 3600.0


def _estimate_tokens(text: str) -> int:
    """
    This function gives a cheap token estimate (~4 chars per token).
    """
    return max(1, len(text) // 4)


class Deriver:

    def __init__(self, provider: BaseProvider, model: str, store: RepresentationStore,
                 config: dict[str, Any], event_store: EventStore | None = None) -> None:
        """
        This is the Deriver which reasons over batched conversation messages in the
        background to build the user representation, learn about the people, projects
        and places the user mentions, capture time-anchored events, and curate the
        finite profile cards.
        """
        self._provider: BaseProvider = provider
        self._model: str = model
        self._store: RepresentationStore = store
        self._event_store: EventStore | None = event_store
        self._config: dict[str, Any] = config

        self._batch_tokens: int = int(config.get("batch_tokens", 1000))
        self._reasoning_level: str = config.get("reasoning_level", "low")
        self._max_chars: int = int(config.get("peer_card_max_chars", 1500))

        # Entity (people/projects/places) and event learning settings
        self._entity_memory: bool = bool(config.get("entity_memory", True))
        self._entity_card_max_chars: int = int(config.get("entity_card_max_chars", 800))
        self._entity_promote_threshold: int = int(config.get("entity_promote_threshold", 2))
        self._event_retention_days: int = int(config.get("event_retention_days", 30))

        # Daily memory-consolidation (distillation) settings
        self._consolidation: bool = bool(config.get("consolidation", True))
        self._consolidation_interval: float = float(config.get("consolidation_interval_hours", 24)) * 3600.0
        self._consolidation_min_items: int = int(config.get("consolidation_min_items", 12))

        # Per-peer pending message queues and token counters
        self._pending: dict[str, list[dict[str, str]]] = {}
        self._pending_tokens: dict[str, int] = {}
        self._flush_counts: dict[str, int] = {}
        self._lock: threading.Lock = threading.Lock()
        self._wake: threading.Event = threading.Event()
        self._stop: bool = False
        self._thread: threading.Thread | None = None
        self._consolidation_thread: threading.Thread | None = None

    def start(self) -> None:
        """
        This function starts the background worker thread.
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Deriver started (batch_tokens=%d, level=%s)", self._batch_tokens, self._reasoning_level)

    def enqueue(self, role: str, content: str) -> None:
        """
        This function queues a message for background reasoning. Only user messages
        inform the user representation; assistant messages are ignored.
        """
        peer: str | None = ROLE_TO_PEER.get(role)
        if not peer or not (content or "").strip():
            return

        with self._lock:
            self._pending.setdefault(peer, []).append({"role": role, "content": content})
            self._pending_tokens[peer] = self._pending_tokens.get(peer, 0) + _estimate_tokens(content)
            over_threshold: bool = self._pending_tokens[peer] >= self._batch_tokens

        if over_threshold:
            self._wake.set()

    def flush_now(self) -> None:
        """
        This function forces an immediate flush of all pending peers (used by
        write_frequency="turn").
        """
        self._drain(force=True)

    def _loop(self) -> None:
        """
        This function is the worker loop. It flushes over-threshold peers when woken
        and flushes all stragglers on the idle timeout.
        """
        while not self._stop:
            signaled: bool = self._wake.wait(timeout=IDLE_FLUSH_SECONDS)
            self._wake.clear()
            try:
                self._drain(force=not signaled)
            except Exception as e:
                logger.error("Deriver flush error: %s", e, exc_info=True)

    def _drain(self, force: bool) -> None:
        """
        This function flushes peers. When force is True, every non-empty peer is
        flushed; otherwise only peers above the token threshold are flushed. When the
        operator has paused the deriver via the sentinel file, pending messages are
        retained and no reasoning runs.
        """
        if self._is_paused():
            return

        with self._lock:
            peers: list[str] = list(self._pending.keys())

        for peer in peers:
            with self._lock:
                tokens: int = self._pending_tokens.get(peer, 0)
                if not force and tokens < self._batch_tokens:
                    continue
                messages: list[dict[str, str]] = self._pending.pop(peer, [])
                self._pending_tokens[peer] = 0
            if messages:
                self._flush(peer=peer, messages=messages)

    def _flush(self, peer: str, messages: list[dict[str, str]]) -> None:
        """
        This function runs one reasoning pass over a batch of messages for a peer and
        stores the resulting conclusions, periodically re-curating the peer card.
        """
        transcript: str = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        conclusions: dict[str, list[Any]] = self._reason(peer=peer, transcript=transcript)
        if not conclusions:
            return

        records: list[dict[str, Any]] = []
        for item in conclusions.get("explicit", []):
            if isinstance(item, dict):
                content: str = item.get("content", "")
                confidence: str = item.get("confidence", "high")
            else:
                content = str(item)
                confidence = "high"
            records.append({"kind": "observation", "content": content, "premises": [], "confidence": confidence})
        for kind, default_conf in (("deductive", "high"), ("inductive", "medium")):
            for item in conclusions.get(kind, []):
                if not isinstance(item, dict):
                    continue
                records.append({
                    "kind": kind,
                    "content": item.get("conclusion", ""),
                    "premises": item.get("premises", []),
                    "confidence": item.get("confidence", default_conf),
                })

        self._store.add_conclusions(peer=peer, records=records)

        if self._entity_memory:
            self._store_entities(entities=conclusions.get("entities", []))
            self._store_events(events=conclusions.get("events", []))

        with self._lock:
            self._flush_counts[peer] = self._flush_counts.get(peer, 0) + 1
            should_refresh: bool = self._flush_counts[peer] % CARD_REFRESH_EVERY == 0
        if should_refresh:
            self._recurate_card(peer=peer)

    def _store_entities(self, entities: list[Any]) -> None:
        """
        This function stores extracted facts about third-party entities (people,
        projects, places) the user mentions and curates a per-entity profile card once
        the entity has accumulated enough signal to be worth remembering.
        """
        for item in entities:
            if not isinstance(item, dict):
                continue
            name: str = (item.get("name") or "").strip()
            if not name:
                continue
            entity_type: str = (item.get("type") or "person").strip().lower()
            relation: str = (item.get("relation") or "").strip()
            facts_raw: Any = item.get("facts", [])
            fact_records: list[dict[str, Any]] = []
            for fact in facts_raw if isinstance(facts_raw, list) else []:
                if isinstance(fact, dict):
                    content: str = (fact.get("content") or "").strip()
                    confidence: str = fact.get("confidence", "medium")
                else:
                    content = str(fact).strip()
                    confidence = "medium"
                if content:
                    fact_records.append({"content": content, "confidence": confidence})
            if not fact_records:
                continue
            self._store.add_entity_facts(
                name=name, entity_type=entity_type, relation=relation, records=fact_records,
            )
            self._maybe_curate_entity_card(name=name)

    def _store_events(self, events: list[Any]) -> None:
        """
        This function stores extracted time-anchored events for proactive recall.
        """
        if self._event_store is None:
            return
        for item in events:
            if not isinstance(item, dict):
                continue
            title: str = (item.get("title") or "").strip()
            date_iso: str = (item.get("date") or "").strip()
            if not title or not date_iso:
                continue
            entities: Any = item.get("entities", [])
            self._event_store.add_event(
                title=title,
                date_iso=date_iso,
                entities=entities if isinstance(entities, list) else [],
                location=(item.get("location") or "").strip(),
                time_of_day=(item.get("time_of_day") or "").strip(),
                recurring=bool(item.get("recurring", False)),
            )

    def _maybe_curate_entity_card(self, name: str) -> None:
        """
        This function (re)curates an entity's profile card once the entity has crossed
        the promotion threshold — enough facts, or at least one medium/high-confidence
        fact — so fleeting one-off mentions never spawn a card.
        """
        slug: str = slugify(name)
        if not slug:
            return
        facts: list[dict[str, Any]] = self._store.all_for_peer(peer="user", limit=CARD_SOURCE_LIMIT, entity=slug)
        if not facts:
            return
        promoted: bool = (
            len(facts) >= self._entity_promote_threshold
            or self._store.has_medium_or_higher_fact(slug=slug)
        )
        if not promoted:
            return
        subject_desc: str = (
            f"a compact profile of {name} (a person, project, place, or organization the "
            "user talks about): who or what they are, their relationship to the user, and "
            "the durable facts the user has shared about them"
        )
        self._curate_card(
            records=facts,
            existing=self._store.read_entity_card(slug=slug),
            subject_desc=subject_desc,
            max_chars=self._entity_card_max_chars,
            write=lambda text: self._store.write_entity_card(slug=slug, text=text, max_chars=self._entity_card_max_chars),
            label=f"entity '{slug}'",
        )

    def _reason(self, peer: str, transcript: str) -> dict[str, list[Any]]:
        """
        This function asks the configured model to extract explicit observations and
        deductive/inductive conclusions as strict JSON. When entity memory is enabled
        it also extracts facts about the people, projects and places the user mentions
        and any time-anchored events, resolving relative dates against today.
        """
        max_items: int = REASONING_LEVEL_ITEMS.get(self._reasoning_level, 4)
        subject: str = "the user (the human in the conversation)"
        focus: str = (
            "Capture durable facts about who the user is and what they want: their "
            "identity, stable preferences, recurring goals, working style, important "
            "people/projects/tools in their life, and standing instructions for how "
            "they want to be treated."
        )
        today: str = date.today().isoformat()

        entity_schema: str = ""
        entity_rules: str = ""
        if self._entity_memory:
            entity_schema = (
                '  "entities": [{"name": "proper name of a person/project/place/org the user '
                'mentions", "type": "person|project|place|organization", "relation": "their '
                'relationship to the user if stated (e.g. sister, coworker, employer)", '
                '"facts": [{"content": "a durable fact about this entity", "confidence": "high|medium|low"}]}],\n'
                '  "events": [{"title": "short event name", "date": "YYYY-MM-DD", "time_of_day": '
                '"optional clock or part of day", "entities": ["names of people/places involved"], '
                '"location": "optional", "recurring": false}],\n'
            )
            entity_rules = (
                f"- Today is {today}. Resolve every relative date (e.g. 'Saturday', 'next week', "
                "'tomorrow') to a concrete YYYY-MM-DD calendar date. Only emit an event when you "
                "can determine a real date; otherwise omit it.\n"
                "- Under 'entities', record third parties the user talks about, not the user "
                "themselves. Give each its proper name; skip vague references with no name. Set "
                "'relation' only when the transcript states it. Mark a birthday or anniversary "
                "event as recurring.\n"
                "- A fact about an entity is durable info about that entity (a trait, role, "
                "preference, or stable circumstance) — not transient chatter.\n"
            )

        system_prompt: str = (
            "You are a reasoning engine that extracts durable, long-term knowledge from a "
            f"slice of an ongoing conversation about {subject}. Apply careful, formal logic "
            "and do not overreach beyond what the transcript supports. Respond with ONLY a "
            "JSON object — no prose, no code fences.\n\n"
            f"{focus}\n\n"
            "Schema:\n"
            "{\n"
            '  "explicit": [{"content": "a fact explicitly stated", "confidence": "high"}],\n'
            '  "deductive": [{"premises": ["..."], "conclusion": "a certain conclusion", "confidence": "high|medium"}],\n'
            '  "inductive": [{"premises": ["..."], "conclusion": "a likely pattern across messages", "confidence": "medium|low"}],\n'
            f"{entity_schema}"
            "}\n\n"
            "Rules:\n"
            f"- Only record durable, generalizable knowledge about {subject}. Aggressively "
            "skip small talk, transient task state, one-off details, and anything that will "
            "not still be true or useful next week.\n"
            "- Write each item as a self-contained statement that stands on its own without "
            "the transcript (resolve pronouns and references).\n"
            "- Set confidence honestly: 'high' for clearly stated or logically certain facts, "
            "'medium' for well-supported inferences, 'low' for tentative patterns. Prefer "
            "omitting an item over recording a low-confidence guess.\n"
            f"{entity_rules}"
            "- Do not invent, assume, or pad. Each list may be empty.\n"
            f"- Include at most {max_items} items per list. Be concise and high-signal."
        )
        history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcript:\n{transcript}"},
        ]

        parsed: dict[str, Any] | None = self._complete_json(history=history, label=f"reasoning for peer '{peer}'")
        if parsed is None:
            return {}
        return {
            "explicit": parsed.get("explicit", []) or [],
            "deductive": parsed.get("deductive", []) or [],
            "inductive": parsed.get("inductive", []) or [],
            "entities": parsed.get("entities", []) or [],
            "events": parsed.get("events", []) or [],
        }

    def _complete_json(self, history: list[dict[str, str]], label: str) -> dict[str, Any] | None:
        """
        This function runs a JSON-returning completion and, when the first reply does
        not parse, makes one repair attempt that re-prompts for strict JSON before
        giving up — so a single malformed reply no longer silently discards a batch.
        """
        try:
            message: Any = self._provider.completions(model=self._model, history=history, think=False)
            raw: str = message.content or ""
        except Exception as e:
            logger.error("Deriver %s call failed: %s", label, e)
            return None

        parsed: dict[str, Any] | None = self._parse_json(raw)
        if parsed is not None:
            return parsed

        repair_history: list[dict[str, str]] = history + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": (
                "Your previous reply was not valid JSON. Respond again with ONLY the JSON "
                "object — no prose, no explanation, no code fences."
            )},
        ]
        try:
            message = self._provider.completions(model=self._model, history=repair_history, think=False)
            raw = message.content or ""
        except Exception as e:
            logger.error("Deriver %s repair call failed: %s", label, e)
            return None

        parsed = self._parse_json(raw)
        if parsed is None:
            logger.warning("Deriver %s output unparseable after repair retry", label)
        return parsed

    def _recurate_card(self, peer: str) -> None:
        """
        This function condenses a peer's stored conclusions into its finite peer-card
        file, reconciling redundancy within the character budget. When the operator
        has frozen the peer card, re-curation is skipped so manual edits persist.
        """
        if self._is_frozen(peer=peer):
            logger.info("Peer card '%s' is frozen; skipping re-curation", peer)
            return

        records: list[dict[str, Any]] = self._store.all_for_peer(peer=peer, limit=CARD_SOURCE_LIMIT)
        if not records:
            return

        subject_desc: str = (
            "a compact profile of the USER: who they are (name, role, context), what "
            "they care about, their stable preferences and goals, and the people, "
            "projects, and tools that matter to them"
        )
        self._curate_card(
            records=records,
            existing=self._store.read_peer_card(peer=peer),
            subject_desc=subject_desc,
            max_chars=self._max_chars,
            write=lambda text: self._store.write_peer_card(peer=peer, text=text, max_chars=self._max_chars),
            label=f"peer '{peer}'",
        )

    def _curate_card(self, records: list[dict[str, Any]], existing: str, subject_desc: str,
                     max_chars: int, write: Any, label: str) -> None:
        """
        This function condenses a set of stored facts into a finite Markdown card
        within a character budget and persists it via the supplied writer. It is
        shared by the user profile card and the per-entity profile cards.
        """
        shaped_bullets: list[str] = [self._shape_record_bullet(r) for r in records]
        bullet_points: str = "\n".join(b for b in shaped_bullets if b)
        if not bullet_points:
            return

        system_prompt: str = (
            f"You maintain {subject_desc}. Rewrite the card as concise Markdown bullet points. "
            f"Keep it under {max_chars} characters. "
            "The input bullets are tagged with a confidence; trust higher-confidence and "
            "more-reinforced facts, and when two bullets conflict, keep the stronger or more "
            "recent one and drop the other. Merge duplicates, remove anything outdated, "
            "contradicted, or low-signal, and keep only durable, high-value information. "
            "Group related facts so the card reads coherently. "
            "Every bullet must be atomic, complete, and grammatical; never output incomplete "
            "or dangling bullets. Prefer fewer, stronger bullets over broad coverage. "
            "Order the bullets from most to least important, so that if the card is later "
            "trimmed to fit, the least critical bullets are the ones dropped. "
            "Write only the durable facts themselves — no meta-commentary, headings, or "
            "preamble. Respond with ONLY the card content."
        )
        user_prompt: str = (
            f"Current card:\n{existing or '(empty)'}\n\n"
            f"Recently reasoned conclusions:\n{bullet_points}\n\n"
            "Produce the updated card."
        )
        history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            message: Any = self._provider.completions(model=self._model, history=history, think=False)
            card: str = (message.content or "").strip()
        except Exception as e:
            logger.error("Deriver card curation failed for %s: %s", label, e)
            return

        if card:
            card = re.sub(pattern=r"^```[a-zA-Z]*\n?|\n?```$", repl="", string=card).strip()
            if len(card) > max_chars + CARD_RETRY_OVERAGE_CHARS:
                compacted: str | None = self._retry_compact_card(peer=label, subject_desc=subject_desc, card=card)
                if compacted:
                    card = compacted
            write(card)
            logger.info("Re-curated card for %s (draft=%d chars)", label, len(card))

    @staticmethod
    def _shape_record_bullet(record: dict[str, Any]) -> str:
        """
        This function normalizes one conclusion into a compact bullet suitable for
        peer-card curation prompts, annotating it with its kind, confidence, and how
        many times it has been reinforced so the curator can weigh it.
        """
        content: str = re.sub(pattern=r"\s+", repl=" ", string=str(record.get("content", "") or "")).strip()
        if not content:
            return ""
        if len(content) > CARD_SOURCE_ITEM_MAX_CHARS:
            content = content[:CARD_SOURCE_ITEM_MAX_CHARS].rstrip()
        kind: str = str(record.get("kind", "observation") or "observation")
        confidence: str = str(record.get("confidence", "medium") or "medium")
        seen: int = int(record.get("times_seen", 1) or 1)
        tag: str = f"{kind}, {confidence} confidence"
        if seen > 1:
            tag += f", seen {seen}x"
        return f"- ({tag}) {content}"

    def _retry_compact_card(self, peer: str, subject_desc: str, card: str) -> str | None:
        """
        This function performs one bounded retry to compress an oversized peer-card
        draft before the final storage-layer budget enforcement runs.
        """
        system_prompt: str = (
            f"Compress this Markdown bullet card about {subject_desc}. "
            f"Keep it at or below {self._max_chars} characters. "
            "Keep only the highest-signal durable bullets. "
            "Order the bullets from most to least important. "
            "Every bullet must be complete and grammatical. "
            "Respond with ONLY Markdown bullets, no preamble."
        )
        history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Card draft:\n{card}\n\nReturn a compact rewrite."},
        ]
        try:
            message: Any = self._provider.completions(model=self._model, history=history, think=False)
            compacted: str = (message.content or "").strip()
        except Exception as e:
            logger.warning("Peer card compact retry failed for '%s': %s", peer, e)
            return None

        compacted = re.sub(pattern=r"^```[a-zA-Z]*\n?|\n?```$", repl="", string=compacted).strip()
        if not compacted:
            return None
        logger.info("Peer card compact retry used for '%s' (%d chars)", peer, len(compacted))
        return compacted

    # --------------------------------------------------- daily memory consolidation

    def start_consolidation_scheduler(self) -> None:
        """
        This function starts the background scheduler that periodically distills the
        accumulated conclusions into a smaller, cleaner set — like memory
        consolidation during sleep. The schedule is persisted across restarts.
        """
        if not self._consolidation or self._consolidation_thread is not None:
            return
        # Seed the schedule on first ever run so the first pass happens one interval
        # from now rather than immediately on boot.
        if not os.path.isfile(CONSOLIDATION_STATE_FILE):
            self._mark_consolidated()
        self._consolidation_thread = threading.Thread(target=self._consolidation_loop, daemon=True)
        self._consolidation_thread.start()
        logger.info("Memory consolidation scheduled (every %.0fh)", self._consolidation_interval / 3600.0)

    def _consolidation_loop(self) -> None:
        """
        This function periodically checks whether a consolidation pass is due and runs
        it when the interval has elapsed and the deriver is not paused.
        """
        while not self._stop:
            time.sleep(CONSOLIDATION_CHECK_SECONDS)
            if self._stop:
                break
            try:
                if self._consolidation_due() and not self._is_paused():
                    self.consolidate_all()
                    self._mark_consolidated()
            except Exception as e:
                logger.error("Consolidation scheduler error: %s", e, exc_info=True)

    def consolidate_all(self) -> dict[str, tuple[int, int]]:
        """
        This function runs a consolidation pass for every active peer and returns a
        per-peer (removed, added) summary. Honors the deriver pause sentinel.
        """
        if self._is_paused():
            logger.info("Consolidation skipped: deriver is paused")
            return {}

        peers: list[str] = ["user"]

        results: dict[str, tuple[int, int]] = {}
        for peer in peers:
            try:
                results[peer] = self._consolidate(peer=peer)
            except Exception as e:
                logger.error("Consolidation failed for peer '%s': %s", peer, e, exc_info=True)
                results[peer] = (0, 0)

        if self._event_store is not None:
            try:
                self._event_store.maintain(retention_days=self._event_retention_days)
            except Exception as e:
                logger.error("Event maintenance failed: %s", e, exc_info=True)
        return results

    def _consolidate(self, peer: str) -> tuple[int, int]:
        """
        This function distills a single peer's derived conclusions into a smaller,
        higher-signal set and replaces them, then re-curates the peer card. Returns
        (removed, added). Manual conclusions are preserved by the store. Stale,
        never-reinforced, low-confidence derived items are pruned first so they do
        not dilute the distillation input.
        """
        self._store.prune_stale_derived(peer=peer)

        records: list[dict[str, Any]] = self._store.list_conclusions(peer=peer, limit=1000)
        derived: list[dict[str, Any]] = [r for r in records if r.get("source", "derived") != "manual"]
        if len(derived) < self._consolidation_min_items:
            logger.info(
                "Consolidation for '%s' skipped (%d derived < %d minimum)",
                peer, len(derived), self._consolidation_min_items,
            )
            return (0, 0)

        distilled: list[dict[str, Any]] = self._distill(peer=peer, records=derived)
        if not distilled:
            logger.warning("Consolidation for '%s' produced nothing; keeping existing memory", peer)
            return (0, 0)

        removed, added = self._store.replace_derived_conclusions(peer=peer, records=distilled)
        if added:
            self._recurate_card(peer=peer)
            logger.info("Consolidated '%s': %d -> %d derived conclusions", peer, removed, added)
        return (removed, added)

    def _distill(self, peer: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        This function asks the model to merge, prune, and synthesize a peer's stored
        conclusions into a cleaner set, returned as validated records.
        """
        subject: str = "the user"
        listing: str = "\n".join(
            f"{i}. ({r.get('kind', 'observation')}, {r.get('confidence', 'medium')} confidence, "
            f"seen {int(r.get('times_seen', 1))}x) {r['content']}"
            for i, r in enumerate(records, start=1)
        )

        system_prompt: str = (
            "You are a memory-consolidation engine, like a brain during sleep. You are given "
            f"the full set of stored conclusions about {subject}, each tagged with its kind, "
            "confidence, and how many times it has been independently observed. Distill them "
            "into a smaller, cleaner, more reliable set of durable knowledge. Respond with "
            "ONLY a JSON object — no prose, no code fences.\n\n"
            "Schema:\n"
            "{\n"
            '  "conclusions": [{"kind": "observation|deductive|inductive", "content": "...", "premises": ["..."], "confidence": "high|medium|low"}]\n'
            "}\n\n"
            "Rules:\n"
            "- Merge duplicates and near-duplicates into a single clear statement, keeping the "
            "highest confidence and summing the evidence.\n"
            "- Resolve contradictions: when two items conflict, keep the one that is more "
            "reinforced (seen more often) or more recent, and drop the superseded one. Never "
            "keep both sides of a direct contradiction.\n"
            "- Drop anything outdated, trivial, ephemeral, or low-signal. Be willing to remove "
            "weak, once-seen, low-confidence guesses entirely.\n"
            "- You may synthesize a higher-order conclusion from several related items "
            "(kind 'inductive'); base its confidence on how strongly the inputs support it.\n"
            "- Keep only durable, high-signal knowledge. Prefer fewer, stronger statements.\n"
            "- Carry a confidence on every item; do not invent facts unsupported by the input.\n"
            "- 'premises' may be empty; add short supporting premises for deductive/inductive items."
        )
        history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Stored conclusions about {subject}:\n{listing}\n\nProduce the distilled set."},
        ]

        parsed: dict[str, Any] | None = self._complete_json(history=history, label=f"consolidation for '{peer}'")
        if parsed is None:
            return []

        distilled: list[dict[str, Any]] = []
        for item in parsed.get("conclusions", []) or []:
            if not isinstance(item, dict):
                continue
            content: str = (item.get("content") or "").strip()
            if not content:
                continue
            kind: str = item.get("kind", "observation")
            if kind not in KINDS:
                kind = "observation"
            premises: Any = item.get("premises", [])
            if not isinstance(premises, list):
                premises = []
            distilled.append({
                "kind": kind,
                "content": content,
                "premises": premises,
                "confidence": item.get("confidence", "medium"),
            })
        return distilled

    @staticmethod
    def _read_consolidation_ts() -> float:
        """
        This function reads the persisted timestamp of the last consolidation pass,
        returning 0.0 when it has never run.
        """
        try:
            with open(file=CONSOLIDATION_STATE_FILE, mode="r") as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return 0.0

    @staticmethod
    def _mark_consolidated() -> None:
        """
        This function records the current time as the last consolidation pass.
        """
        try:
            with open(file=CONSOLIDATION_STATE_FILE, mode="w") as f:
                f.write(str(time.time()))
        except OSError as e:
            logger.error("Could not persist consolidation timestamp: %s", e)

    def _consolidation_due(self) -> bool:
        """
        This function returns True when at least one interval has elapsed since the
        last consolidation pass.
        """
        return (time.time() - self._read_consolidation_ts()) >= self._consolidation_interval

    @staticmethod
    def _is_paused() -> bool:
        """
        This function returns True when the deriver pause sentinel file is present.
        """
        return os.path.isfile(PAUSE_SENTINEL)

    def _is_frozen(self, peer: str) -> bool:
        """
        This function returns True when the peer's card is frozen in config. The flag
        is re-read from disk so the web panel can toggle it without a restart.
        """
        flag: str | None = FREEZE_FLAGS.get(peer)
        if not flag:
            return False
        try:
            with open(file=CONFIG_PATH, mode="r") as f:
                disk_config: dict[str, Any] = json.load(fp=f)
            return bool((disk_config.get("memory") or {}).get(flag, False))
        except (OSError, json.JSONDecodeError):
            return bool(self._config.get(flag, False))

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        """
        This function extracts a JSON object from a model response, tolerating code
        fences and surrounding prose.
        """
        text: str = raw.strip()
        text = re.sub(pattern=r"^```[a-zA-Z]*\n?|\n?```$", repl="", string=text).strip()
        try:
            value: Any = json.loads(text)
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass

        start: int = text.find("{")
        end: int = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                value = json.loads(text[start:end + 1])
                return value if isinstance(value, dict) else None
            except (json.JSONDecodeError, ValueError):
                return None
        return None
