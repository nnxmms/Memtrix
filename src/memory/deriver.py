#!/usr/bin/python3

import json
import logging
import os
import re
import threading
import time
from typing import Any

from src.core.config import CONFIG_PATH
from src.core.lifecycle import PAUSE_SENTINEL
from src.providers.base import BaseProvider
from src.memory.store import KINDS, REASONING_LEVEL_ITEMS, RepresentationStore

logger: logging.Logger = logging.getLogger(__name__)

# Map an assistant/user role to the peer whose representation it informs
ROLE_TO_PEER: dict[str, str] = {"user": "user", "assistant": "agent"}

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
FREEZE_FLAGS: dict[str, str] = {"user": "freeze_user_card", "agent": "freeze_agent_card"}

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
                 config: dict[str, Any]) -> None:
        """
        This is the Deriver which reasons over batched conversation messages in the
        background to build per-peer representations and curate the finite peer cards.
        """
        self._provider: BaseProvider = provider
        self._model: str = model
        self._store: RepresentationStore = store
        self._config: dict[str, Any] = config

        self._batch_tokens: int = int(config.get("batch_tokens", 1000))
        self._reasoning_level: str = config.get("reasoning_level", "low")
        self._max_chars: int = int(config.get("peer_card_max_chars", 1500))
        self._dual_peer: bool = bool(config.get("dual_peer", True))

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
        This function queues a message for background reasoning. User messages inform
        the user representation; assistant messages inform the agent representation.
        """
        peer: str | None = ROLE_TO_PEER.get(role)
        if not peer or not (content or "").strip():
            return
        if peer == "agent" and not self._dual_peer:
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
            content: str = item.get("content", "") if isinstance(item, dict) else str(item)
            records.append({"kind": "observation", "content": content, "premises": []})
        for kind in ("deductive", "inductive"):
            for item in conclusions.get(kind, []):
                if not isinstance(item, dict):
                    continue
                records.append({
                    "kind": kind,
                    "content": item.get("conclusion", ""),
                    "premises": item.get("premises", []),
                })

        self._store.add_conclusions(peer=peer, records=records)

        with self._lock:
            self._flush_counts[peer] = self._flush_counts.get(peer, 0) + 1
            should_refresh: bool = self._flush_counts[peer] % CARD_REFRESH_EVERY == 0
        if should_refresh:
            self._recurate_card(peer=peer)

    def _reason(self, peer: str, transcript: str) -> dict[str, list[Any]]:
        """
        This function asks the configured model to extract explicit observations and
        deductive/inductive conclusions as strict JSON.
        """
        max_items: int = REASONING_LEVEL_ITEMS.get(self._reasoning_level, 4)
        subject: str = "the user" if peer == "user" else "the AI assistant itself"

        system_prompt: str = (
            "You are a reasoning engine that extracts durable knowledge from a conversation "
            f"transcript about {subject}. Apply formal logic. Respond with ONLY a JSON object, "
            "no prose, no code fences.\n\n"
            "Schema:\n"
            "{\n"
            '  "explicit": [{"content": "a fact explicitly stated"}],\n'
            '  "deductive": [{"premises": ["..."], "conclusion": "a certain conclusion"}],\n'
            '  "inductive": [{"premises": ["..."], "conclusion": "a likely pattern across messages"}]\n'
            "}\n\n"
            f"Rules: Only include durable, generalizable knowledge about {subject} — skip "
            "small talk, transient task details, and anything ephemeral. Each list may be empty. "
            f"Include at most {max_items} items per list. Be concise."
        )
        history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcript:\n{transcript}"},
        ]

        try:
            message: Any = self._provider.completions(model=self._model, history=history, think=False)
            raw: str = message.content or ""
        except Exception as e:
            logger.error("Deriver reasoning call failed: %s", e)
            return {}

        parsed: dict[str, Any] | None = self._parse_json(raw)
        if parsed is None:
            logger.warning("Deriver could not parse reasoning output for peer '%s'", peer)
            return {}
        return {
            "explicit": parsed.get("explicit", []) or [],
            "deductive": parsed.get("deductive", []) or [],
            "inductive": parsed.get("inductive", []) or [],
        }

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

        existing: str = self._store.read_peer_card(peer=peer)
        shaped_bullets: list[str] = [self._shape_record_bullet(r) for r in records]
        bullet_points: str = "\n".join(b for b in shaped_bullets if b)
        if not bullet_points:
            return
        if peer == "user":
            subject_desc: str = (
                "a compact profile of the USER: their name, what they like, their preferences, "
                "goals, and relevant personal context"
            )
        else:
            subject_desc = (
                "a compact self-description of the AI assistant: its persona, where it runs "
                "(e.g. VM/cloud), and how it should behave"
            )

        system_prompt: str = (
            f"You maintain {subject_desc}. Rewrite the card as concise Markdown bullet points. "
            f"Keep it under {self._max_chars} characters. Merge duplicates, drop anything "
            "outdated or contradicted, and keep only durable, high-signal information. "
            "Every bullet must be atomic and complete; never output incomplete or dangling bullets. "
            "Prefer fewer, stronger bullets over broad coverage. "
            "Respond with ONLY the card content, no preamble."
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
            logger.error("Deriver card curation failed for peer '%s': %s", peer, e)
            return

        if card:
            card = re.sub(pattern=r"^```[a-zA-Z]*\n?|\n?```$", repl="", string=card).strip()
            if len(card) > self._max_chars + CARD_RETRY_OVERAGE_CHARS:
                compacted: str | None = self._retry_compact_card(peer=peer, subject_desc=subject_desc, card=card)
                if compacted:
                    card = compacted
            self._store.write_peer_card(peer=peer, text=card, max_chars=self._max_chars)
            logger.info("Re-curated peer card for '%s' (draft=%d chars)", peer, len(card))

    @staticmethod
    def _shape_record_bullet(record: dict[str, Any]) -> str:
        """
        This function normalizes one conclusion into a compact bullet suitable for
        peer-card curation prompts.
        """
        content: str = re.sub(pattern=r"\s+", repl=" ", string=str(record.get("content", "") or "")).strip()
        if not content:
            return ""
        if len(content) > CARD_SOURCE_ITEM_MAX_CHARS:
            content = content[:CARD_SOURCE_ITEM_MAX_CHARS].rstrip()
        kind: str = str(record.get("kind", "observation") or "observation")
        return f"- ({kind}) {content}"

    def _retry_compact_card(self, peer: str, subject_desc: str, card: str) -> str | None:
        """
        This function performs one bounded retry to compress an oversized peer-card
        draft before the final storage-layer budget enforcement runs.
        """
        system_prompt: str = (
            f"Compress this Markdown bullet card about {subject_desc}. "
            f"Keep it at or below {self._max_chars} characters. "
            "Keep only the highest-signal durable bullets. "
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
        if self._dual_peer:
            peers.append("agent")

        results: dict[str, tuple[int, int]] = {}
        for peer in peers:
            try:
                results[peer] = self._consolidate(peer=peer)
            except Exception as e:
                logger.error("Consolidation failed for peer '%s': %s", peer, e, exc_info=True)
                results[peer] = (0, 0)
        return results

    def _consolidate(self, peer: str) -> tuple[int, int]:
        """
        This function distills a single peer's derived conclusions into a smaller,
        higher-signal set and replaces them, then re-curates the peer card. Returns
        (removed, added). Manual conclusions are preserved by the store.
        """
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
        subject: str = "the user" if peer == "user" else "the AI assistant itself"
        listing: str = "\n".join(
            f"{i}. ({r.get('kind', 'observation')}, seen {int(r.get('times_seen', 1))}x) {r['content']}"
            for i, r in enumerate(records, start=1)
        )

        system_prompt: str = (
            "You are a memory-consolidation engine, like a brain during sleep. You are given "
            f"the full set of stored conclusions about {subject}. Distill them into a smaller, "
            "cleaner set of durable knowledge. Respond with ONLY a JSON object, no prose, no "
            "code fences.\n\n"
            "Schema:\n"
            "{\n"
            '  "conclusions": [{"kind": "observation|deductive|inductive", "content": "...", "premises": ["..."]}]\n'
            "}\n\n"
            "Rules:\n"
            "- Merge duplicates and near-duplicates into a single clear statement.\n"
            "- Drop anything outdated, contradicted by a newer item, trivial, or ephemeral.\n"
            "- You may synthesize a higher-order conclusion from several related items (kind 'inductive').\n"
            "- Keep only durable, high-signal knowledge. Prefer fewer, stronger statements.\n"
            "- Do not invent facts that are not supported by the input.\n"
            "- 'premises' may be empty; add short supporting premises for deductive/inductive items."
        )
        history: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Stored conclusions about {subject}:\n{listing}\n\nProduce the distilled set."},
        ]

        try:
            message: Any = self._provider.completions(model=self._model, history=history, think=False)
            raw: str = message.content or ""
        except Exception as e:
            logger.error("Consolidation reasoning call failed for '%s': %s", peer, e)
            return []

        parsed: dict[str, Any] | None = self._parse_json(raw)
        if parsed is None:
            logger.warning("Consolidation output unparseable for '%s'", peer)
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
            distilled.append({"kind": kind, "content": content, "premises": premises})
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
