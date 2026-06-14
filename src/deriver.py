#!/usr/bin/python3

import json
import logging
import re
import threading
import time
from typing import Any

from src.providers.base import BaseProvider
from src.representation import REASONING_LEVEL_ITEMS, RepresentationStore

logger: logging.Logger = logging.getLogger(__name__)

# Map an assistant/user role to the peer whose representation it informs
ROLE_TO_PEER: dict[str, str] = {"user": "user", "assistant": "agent"}

# Idle flush interval — stragglers below the token threshold are flushed after this
IDLE_FLUSH_SECONDS: float = 90.0

# Re-curate the finite peer card after this many reasoning passes for a peer
CARD_REFRESH_EVERY: int = 3


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

        # Per-peer pending message queues and token counters
        self._pending: dict[str, list[dict[str, str]]] = {}
        self._pending_tokens: dict[str, int] = {}
        self._flush_counts: dict[str, int] = {}
        self._lock: threading.Lock = threading.Lock()
        self._wake: threading.Event = threading.Event()
        self._stop: bool = False
        self._thread: threading.Thread | None = None

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
        flushed; otherwise only peers above the token threshold are flushed.
        """
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
        file, reconciling redundancy within the character budget.
        """
        records: list[dict[str, Any]] = self._store.all_for_peer(peer=peer, limit=40)
        if not records:
            return

        existing: str = self._store.read_peer_card(peer=peer)
        bullet_points: str = "\n".join(f"- ({r['kind']}) {r['content']}" for r in records)
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
            self._store.write_peer_card(peer=peer, text=card, max_chars=self._max_chars)
            logger.info("Re-curated peer card for '%s' (%d chars)", peer, len(card))

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
