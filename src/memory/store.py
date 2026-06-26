#!/usr/bin/python3

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import chromadb
from filelock import FileLock

from src.core.config import CONFIG_PATH
from src.memory.index import LocalEmbeddingFunction

logger: logging.Logger = logging.getLogger(__name__)

# Valid peers and conclusion kinds
PEERS: set[str] = {"user"}
KINDS: set[str] = {"observation", "deductive", "inductive"}

# Valid confidence levels for a conclusion, ordered weakest -> strongest. A
# conclusion's confidence governs how it is ranked at recall time and how
# aggressively the consolidation pass is allowed to prune it.
CONFIDENCE_LEVELS: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_CONFIDENCE: str = "medium"

# Numeric weight per confidence level, used to rank recall candidates so that
# stronger, better-supported memories surface ahead of weak guesses.
CONFIDENCE_WEIGHT: dict[str, float] = {"low": 0.5, "medium": 1.0, "high": 1.5}

# Peer card files (finite, always-injected summaries)
PEER_CARD_FILES: dict[str, str] = {"user": "USER.md"}

# Workspace subdirectory holding per-entity profile cards (people, projects, places
# the user talks about). Each card is a compact, deriver-curated Markdown file like
# USER.md but scoped to one entity and injected only when that entity is relevant.
ENTITY_CARD_DIR: str = "people"


def slugify(name: str) -> str:
    """
    This function converts an entity name into a stable, filesystem-safe slug used as
    the entity's identity key and card filename (e.g. "Jenna Smith" -> "jenna-smith").
    """
    text: str = (name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64]

# Two normalized embeddings are considered duplicates below this L2 distance
# (l2^2 = 2(1 - cosine); 0.35 ≈ cosine similarity > 0.93)
DEDUP_L2_THRESHOLD: float = 0.35

# Consolidation decay policy: a derived conclusion is eligible for pruning only
# when it is stale (last reinforced longer ago than this), was never reinforced
# (seen exactly once), and is low confidence. Manual, reinforced, and high/medium
# confidence conclusions are never dropped by decay alone.
DERIVED_STALE_SECONDS: float = 30.0 * 24.0 * 3600.0


def _normalize_confidence(value: Any) -> str:
    """
    This function coerces an arbitrary confidence value into one of the valid
    levels, defaulting to DEFAULT_CONFIDENCE for anything unrecognized.
    """
    text: str = str(value or "").strip().lower()
    return text if text in CONFIDENCE_LEVELS else DEFAULT_CONFIDENCE


def resolve_memory_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    This function returns the memory configuration merged with safe defaults so
    that installs without a "memory" section keep working unchanged.
    """
    defaults: dict[str, Any] = {
        "backend": "native",          # native | off
        "recall_mode": "hybrid",      # hybrid | context | tools | off
        "write_frequency": "async",   # async | turn
        "reasoning_level": "low",     # minimal | low | medium | high | max
        "reasoning_model": None,      # model instance name, or None for the main model
        "batch_tokens": 1000,
        "peer_card_max_chars": 1500,
        "inject_top_k": 5,
        "consolidation": True,                # daily memory-distillation pass
        "consolidation_interval_hours": 24,   # how often distillation runs
        "consolidation_min_items": 12,        # skip distillation below this many derived items
        "entity_memory": True,                # learn about people/projects/places the user mentions
        "entity_card_max_chars": 800,         # budget for each per-entity profile card
        "entity_promote_threshold": 2,        # facts needed before an entity gets a curated card
        "event_lookahead_days": 7,            # how far ahead upcoming events are surfaced
        "event_followup_days": 2,             # window for a one-time post-event follow-up
        "event_retention_days": 30,           # prune non-recurring past events older than this
    }
    user_cfg: dict[str, Any] = config.get("memory", {}) or {}
    merged: dict[str, Any] = {**defaults, **user_cfg}
    merged["enabled"] = merged.get("backend") == "native" and merged.get("recall_mode") != "off"
    return merged


# Number of reasoning items to request per kind, scaled by reasoning level
REASONING_LEVEL_ITEMS: dict[str, int] = {
    "minimal": 2,
    "low": 4,
    "medium": 6,
    "high": 8,
    "max": 12,
}


def _make_chroma_client(persist_dir: str) -> chromadb.ClientAPI:
    """
    This function returns a ChromaDB client. When the CHROMA_URL environment
    variable is set (e.g. http://chroma:8000), it connects to the shared ChromaDB
    server via HttpClient so the agent and the web control panel can both read and
    write the same store without SQLite single-writer corruption. Otherwise it
    falls back to a process-local PersistentClient at persist_dir.
    """
    chroma_url: str | None = os.environ.get("CHROMA_URL")
    if chroma_url:
        parsed = urlparse(chroma_url)
        host: str = parsed.hostname or "chroma"
        port: int = parsed.port or 8000
        logger.info("Connecting to shared ChromaDB server at %s:%d", host, port)
        return chromadb.HttpClient(
            host=host,
            port=port,
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
    return chromadb.PersistentClient(
        path=persist_dir,
        settings=chromadb.Settings(anonymized_telemetry=False, is_persistent=True),
    )


class RepresentationStore:

    _instances: dict[str, "RepresentationStore"] = {}
    _lock: threading.Lock = threading.Lock()
    _TRUNCATION_MARKER: str = "..."

    @classmethod
    def get_instance(cls, workspace_dir: str, collection_name: str = "representations") -> "RepresentationStore":
        """
        This function returns the RepresentationStore for a workspace, creating it once.
        """
        with cls._lock:
            if workspace_dir not in cls._instances:
                cls._instances[workspace_dir] = cls(workspace_dir=workspace_dir, collection_name=collection_name)
            return cls._instances[workspace_dir]

    def __init__(self, workspace_dir: str, collection_name: str = "representations") -> None:
        """
        This is the RepresentationStore which holds vector-indexed conclusions about
        the user and manages the finite user profile card (USER.md).
        """
        self._workspace_dir: str = workspace_dir
        self._write_lock: threading.Lock = threading.Lock()

        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        # Reuse the shared embedding model (singleton, loaded once)
        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction.get_instance(model_dir=model_dir)

        # Persist in a dedicated subdirectory; sub-agents get their own collection
        if collection_name == "representations":
            persist_dir: str = os.path.join(data_dir, "representations")
        else:
            persist_dir = os.path.join(data_dir, "representations", collection_name)
        self._client: chromadb.ClientAPI = _make_chroma_client(persist_dir=persist_dir)
        self._collection: chromadb.Collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_fn,
        )
        logger.info("Representation store ready (collection=%s, records=%d)", collection_name, self._collection.count())

    def count(self) -> int:
        """
        This function returns the number of stored conclusions.
        """
        return self._collection.count()

    def add_conclusions(self, peer: str, records: list[dict[str, Any]]) -> int:
        """
        This function stores a batch of conclusions for a peer, skipping near-duplicates
        (bumping their seen counter instead). Returns the number of new records added.
        Each record is {"kind": str, "content": str, "premises": list[str],
        "confidence": str}. The confidence is optional and defaults to medium.
        """
        if peer not in PEERS:
            return 0

        added: int = 0
        with self._write_lock:
            for record in records:
                content: str = (record.get("content") or "").strip()
                kind: str = record.get("kind", "observation")
                if not content or kind not in KINDS:
                    continue

                if self._bump_if_duplicate(peer=peer, content=content):
                    continue

                self._collection.add(
                    ids=[str(uuid.uuid4())],
                    documents=[content],
                    metadatas=[{
                        "peer": peer,
                        "kind": kind,
                        "premises": json.dumps(record.get("premises", [])),
                        "ts": time.time(),
                        "times_seen": 1,
                        "source": "derived",
                        "confidence": _normalize_confidence(record.get("confidence")),
                        "entity": "",
                        "entity_name": "",
                        "entity_type": "",
                        "relation": "",
                    }],
                )
                added += 1

        if added:
            logger.info("Stored %d new conclusion(s) for peer '%s'", added, peer)
        return added

    def add_entity_facts(self, name: str, entity_type: str, relation: str,
                         records: list[dict[str, Any]]) -> int:
        """
        This function stores durable facts about a third-party entity the user talks
        about (a person, project, place, or organization), tagged with the entity's
        slug so they can be grouped into a per-entity profile card and surfaced when
        that entity is relevant. Facts live in the same collection as user
        conclusions (peer="user") but carry an "entity" slug; an empty entity slug
        denotes a fact about the user themselves. Near-duplicates within the same
        entity bump their seen counter instead of being re-added. Returns the number
        of new facts stored.
        """
        slug: str = slugify(name)
        if not slug:
            return 0

        added: int = 0
        with self._write_lock:
            for record in records:
                content: str = (record.get("content") or "").strip()
                kind: str = record.get("kind", "observation")
                if not content or kind not in KINDS:
                    continue

                if self._bump_if_duplicate(peer="user", content=content, entity=slug):
                    continue

                self._collection.add(
                    ids=[str(uuid.uuid4())],
                    documents=[content],
                    metadatas=[{
                        "peer": "user",
                        "kind": kind,
                        "premises": json.dumps(record.get("premises", [])),
                        "ts": time.time(),
                        "times_seen": 1,
                        "source": "derived",
                        "confidence": _normalize_confidence(record.get("confidence")),
                        "entity": slug,
                        "entity_name": (name or "").strip(),
                        "entity_type": (entity_type or "").strip().lower(),
                        "relation": (relation or "").strip().lower(),
                    }],
                )
                added += 1

        if added:
            logger.info("Stored %d new fact(s) for entity '%s'", added, slug)
        return added

    def _bump_if_duplicate(self, peer: str, content: str, entity: str = "") -> bool:
        """
        This function checks whether a near-identical conclusion already exists for the
        peer and the same entity scope and, if so, increments its seen counter. Returns
        True when a duplicate was found. The entity scope ("" for facts about the user
        themselves, otherwise an entity slug) keeps facts about different people from
        colliding when their wording is similar.
        """
        if self._collection.count() == 0:
            return False

        results: dict[str, Any] = self._collection.query(
            query_texts=[content],
            n_results=3,
            where={"peer": peer},
        )
        ids: list[list[str]] = results.get("ids", [[]])
        distances: list[list[float]] = results.get("distances", [[]])
        if not ids or not ids[0]:
            return False

        for idx, existing_id in enumerate(ids[0]):
            if distances[0][idx] > DEDUP_L2_THRESHOLD:
                break
            metadata: dict[str, Any] = results["metadatas"][0][idx]
            if (metadata.get("entity", "") or "") != entity:
                continue
            metadata["times_seen"] = int(metadata.get("times_seen", 1)) + 1
            metadata["ts"] = time.time()
            # Independent re-derivation of the same conclusion is corroborating
            # evidence, so a reinforced memory is promoted at least to medium
            # confidence and never decays as a one-off.
            current: str = _normalize_confidence(metadata.get("confidence"))
            if current == "low":
                metadata["confidence"] = "medium"
            self._collection.update(ids=[existing_id], metadatas=[metadata])
            return True
        return False

    def search(self, query: str, peer: str | None = None, kinds: list[str] | None = None,
               n_results: int = 5) -> list[dict[str, Any]]:
        """
        This function returns the conclusions most relevant to a query, optionally
        filtered by peer and kinds.
        """
        total: int = self._collection.count()
        if total == 0:
            return []

        where: dict[str, Any] | None = None
        conditions: list[dict[str, Any]] = []
        if peer in PEERS:
            conditions.append({"peer": peer})
        if kinds:
            valid: list[str] = [k for k in kinds if k in KINDS]
            if valid:
                conditions.append({"kind": {"$in": valid}})
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        results: dict[str, Any] = self._collection.query(
            query_texts=[query],
            n_results=min(n_results, total),
            where=where,
        )

        matches: list[dict[str, Any]] = []
        ids: list[list[str]] = results.get("ids", [[]])
        if not ids or not ids[0]:
            return []
        for i in range(len(ids[0])):
            meta: dict[str, Any] = results["metadatas"][0][i]
            matches.append({
                "id": ids[0][i],
                "content": results["documents"][0][i],
                "peer": meta.get("peer", ""),
                "kind": meta.get("kind", ""),
                "distance": results["distances"][0][i],
                "source": meta.get("source", "derived"),
                "confidence": _normalize_confidence(meta.get("confidence")),
                "entity": meta.get("entity", "") or "",
                "entity_name": meta.get("entity_name", "") or "",
            })
        return matches

    def all_for_peer(self, peer: str, limit: int = 100, entity: str = "") -> list[dict[str, Any]]:
        """
        This function returns stored conclusions for a peer, strongest first. Records
        are ranked by a blend of confidence and reinforcement so the highest-signal
        conclusions lead the card-curation input, with recency breaking ties. The
        entity scope ("" returns facts about the user themselves; an entity slug
        returns facts about that entity) keeps the user's own card and per-entity
        cards from cross-contaminating.
        """
        if peer not in PEERS or self._collection.count() == 0:
            return []

        results: dict[str, Any] = self._collection.get(where={"peer": peer})
        documents: list[str] = results.get("documents", []) or []
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []

        records: list[dict[str, Any]] = [
            {
                "content": content,
                "kind": meta.get("kind", ""),
                "times_seen": int(meta.get("times_seen", 1)),
                "ts": float(meta.get("ts", 0.0)),
                "confidence": _normalize_confidence(meta.get("confidence")),
            }
            for content, meta in zip(documents, metadatas)
            if (meta.get("entity", "") or "") == entity
        ]
        records.sort(
            key=lambda r: (
                CONFIDENCE_WEIGHT.get(r["confidence"], 1.0) * r["times_seen"],
                r["ts"],
            ),
            reverse=True,
        )
        return records[:limit]

    def list_entities(self, min_facts: int = 1) -> list[dict[str, Any]]:
        """
        This function returns the known entities (people, projects, places the user
        talks about) with a fact count, display name, type and relation, sorted by
        fact count then recency. Used to decide which entities are salient enough to
        get a curated profile card and to power the web admin.
        """
        if self._collection.count() == 0:
            return []

        results: dict[str, Any] = self._collection.get(where={"peer": "user"})
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []

        by_slug: dict[str, dict[str, Any]] = {}
        for meta in metadatas:
            slug: str = meta.get("entity", "") or ""
            if not slug:
                continue
            entry: dict[str, Any] = by_slug.setdefault(slug, {
                "slug": slug,
                "name": meta.get("entity_name", "") or slug,
                "type": meta.get("entity_type", "") or "",
                "relation": meta.get("relation", "") or "",
                "facts": 0,
                "ts": 0.0,
            })
            entry["facts"] += 1
            ts: float = float(meta.get("ts", 0.0))
            if ts > entry["ts"]:
                entry["ts"] = ts
            # Prefer a non-empty name/type/relation if a later record has one
            if not entry["name"] and meta.get("entity_name"):
                entry["name"] = meta.get("entity_name")
            if not entry["type"] and meta.get("entity_type"):
                entry["type"] = meta.get("entity_type")
            if not entry["relation"] and meta.get("relation"):
                entry["relation"] = meta.get("relation")

        entities: list[dict[str, Any]] = [e for e in by_slug.values() if e["facts"] >= min_facts]
        entities.sort(key=lambda e: (e["facts"], e["ts"]), reverse=True)
        return entities

    def has_medium_or_higher_fact(self, slug: str) -> bool:
        """
        This function reports whether an entity has at least one medium- or
        high-confidence fact, used as an early-promotion signal for card curation.
        """
        if not slug or self._collection.count() == 0:
            return False
        results: dict[str, Any] = self._collection.get(where={"peer": "user"})
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []
        for meta in metadatas:
            if (meta.get("entity", "") or "") != slug:
                continue
            if _normalize_confidence(meta.get("confidence")) in ("medium", "high"):
                return True
        return False

    def read_peer_card(self, peer: str) -> str:
        """
        This function reads the finite peer-card file for a peer (USER.md).
        """
        filename: str | None = PEER_CARD_FILES.get(peer)
        if not filename:
            return ""
        path: str = os.path.join(self._workspace_dir, filename)
        if not os.path.isfile(path):
            return ""
        with open(file=path, mode="r") as f:
            return f.read().strip()

    def write_peer_card(self, peer: str, text: str, max_chars: int = 1500) -> None:
        """
        This function writes the finite peer-card file for a peer, enforcing a hard
        character budget so the card stays compact. The write is guarded by a
        cross-process file lock so the deriver and the web panel never clobber each
        other's edits.
        """
        filename: str | None = PEER_CARD_FILES.get(peer)
        if not filename:
            return
        raw_text: str = text.strip()
        trimmed, strategy = self._truncate_peer_card(text=raw_text, max_chars=max_chars)
        if strategy != "none":
            logger.info(
                "Peer card '%s' truncated via %s (%d -> %d chars, limit=%d)",
                peer,
                strategy,
                len(raw_text),
                len(trimmed),
                max_chars,
            )
        path: str = os.path.join(self._workspace_dir, filename)
        with self._write_lock, FileLock(path + ".lock", timeout=15):
            with open(file=path, mode="w") as f:
                f.write(trimmed + "\n")

    def _truncate_peer_card(self, text: str, max_chars: int) -> tuple[str, str]:
        """
        This function enforces the peer-card character budget while preferring
        clean semantic boundaries instead of slicing mid-word or mid-bullet.
        Returns (trimmed_text, strategy).
        """
        if max_chars <= 0:
            return ("", "empty-budget")

        normalized: str = text.strip()
        if len(normalized) <= max_chars:
            return (normalized, "none")

        # Reserve room for a continuation marker whenever possible.
        marker: str = self._TRUNCATION_MARKER if max_chars > len(self._TRUNCATION_MARKER) else ""
        budget: int = max_chars - len(marker)
        if budget <= 0:
            return (normalized[:max_chars].rstrip(), "hard-cut")

        candidate: str = normalized[:budget].rstrip()

        # 1) Prefer a complete line/bullet boundary.
        last_newline: int = candidate.rfind("\n")
        if last_newline > 0:
            line_cut: str = candidate[:last_newline].rstrip()
            if line_cut:
                return (line_cut + marker, "line-boundary")

        # 2) Prefer ending on sentence punctuation.
        sentence_ends: list[int] = [m.end() for m in re.finditer(r"[.!?](?:\s|$)", candidate)]
        if sentence_ends:
            sent_cut: str = candidate[:sentence_ends[-1]].rstrip()
            if sent_cut:
                return (sent_cut + marker, "sentence-boundary")

        # 3) Prefer a word boundary.
        last_space: int = candidate.rfind(" ")
        if last_space > 0:
            word_cut: str = candidate[:last_space].rstrip()
            if word_cut:
                return (word_cut + marker, "word-boundary")

        # Final fallback: hard cut at budget.
        return (candidate + marker, "hard-cut")

    # ------------------------------------------------------------- entity cards

    def _entity_card_path(self, slug: str) -> str:
        """
        This function returns the on-disk path of an entity's profile card.
        """
        return os.path.join(self._workspace_dir, ENTITY_CARD_DIR, f"{slug}.md")

    def read_entity_card(self, slug: str) -> str:
        """
        This function reads the curated profile card for an entity, or "" when none
        exists yet.
        """
        if not slug:
            return ""
        path: str = self._entity_card_path(slug=slug)
        if not os.path.isfile(path):
            return ""
        with open(file=path, mode="r") as f:
            return f.read().strip()

    def write_entity_card(self, slug: str, text: str, max_chars: int = 800) -> None:
        """
        This function writes an entity's profile card under the people/ directory,
        enforcing a hard character budget at clean boundaries. Guarded by a
        cross-process file lock so the deriver and web panel never clobber each other.
        """
        if not slug:
            return
        trimmed, strategy = self._truncate_peer_card(text=text.strip(), max_chars=max_chars)
        if strategy != "none":
            logger.info("Entity card '%s' truncated via %s (limit=%d)", slug, strategy, max_chars)
        path: str = self._entity_card_path(slug=slug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._write_lock, FileLock(path + ".lock", timeout=15):
            with open(file=path, mode="w") as f:
                f.write(trimmed + "\n")

    def delete_entity(self, slug: str) -> int:
        """
        This function removes every stored fact about an entity and deletes its
        profile card. Returns the number of facts removed.
        """
        if not slug or self._collection.count() == 0:
            return 0
        with self._write_lock:
            results: dict[str, Any] = self._collection.get(where={"peer": "user"})
            ids: list[str] = results.get("ids", []) or []
            metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []
            target_ids: list[str] = [
                record_id
                for record_id, meta in zip(ids, metadatas)
                if (meta.get("entity", "") or "") == slug
            ]
            if target_ids:
                self._collection.delete(ids=target_ids)
            path: str = self._entity_card_path(slug=slug)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as e:
                logger.warning("Could not delete entity card '%s': %s", slug, e)
        logger.info("Deleted entity '%s' (%d fact(s))", slug, len(target_ids))
        return len(target_ids)

    # ----------------------------------------------------------------- admin API

    def list_conclusions(self, peer: str | None = None, kinds: list[str] | None = None,
                         limit: int = 100, offset: int = 0,
                         entity: str | None = None) -> list[dict[str, Any]]:
        """
        This function returns stored conclusions (including their ids and full
        metadata) for administration, optionally filtered by peer and kinds, sorted
        most-recent first. The entity filter ("" returns facts about the user
        themselves, a slug returns facts about that entity, None applies no entity
        filter) is applied in Python so legacy records lacking the key are handled.
        Used by the web control panel.
        """
        where: dict[str, Any] | None = self._build_where(peer=peer, kinds=kinds)
        results: dict[str, Any] = self._collection.get(where=where)
        ids: list[str] = results.get("ids", []) or []
        documents: list[str] = results.get("documents", []) or []
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []

        records: list[dict[str, Any]] = []
        for record_id, content, meta in zip(ids, documents, metadatas):
            if entity is not None and (meta.get("entity", "") or "") != entity:
                continue
            records.append(self._to_record(record_id=record_id, content=content, meta=meta))
        records.sort(key=lambda r: r["ts"], reverse=True)
        return records[offset:offset + limit]

    def get_conclusion(self, record_id: str) -> dict[str, Any] | None:
        """
        This function returns a single conclusion by id, or None when missing.
        """
        results: dict[str, Any] = self._collection.get(ids=[record_id])
        ids: list[str] = results.get("ids", []) or []
        if not ids:
            return None
        return self._to_record(
            record_id=ids[0],
            content=(results.get("documents") or [""])[0],
            meta=(results.get("metadatas") or [{}])[0],
        )

    def delete_conclusion(self, record_id: str) -> bool:
        """
        This function deletes a single conclusion by id. Returns True when a record
        was present and removed.
        """
        with self._write_lock:
            if self.get_conclusion(record_id=record_id) is None:
                return False
            self._collection.delete(ids=[record_id])
        logger.info("Deleted conclusion %s", record_id)
        return True

    def update_conclusion(self, record_id: str, content: str | None = None,
                          kind: str | None = None, premises: list[str] | None = None) -> bool:
        """
        This function updates a conclusion's content (re-embedding it), kind and/or
        premises. Returns True when the record exists and was updated.
        """
        with self._write_lock:
            existing: dict[str, Any] | None = self.get_conclusion(record_id=record_id)
            if existing is None:
                return False

            meta: dict[str, Any] = {
                "peer": existing["peer"],
                "kind": kind if kind in KINDS else existing["kind"],
                "premises": json.dumps(premises if premises is not None else existing["premises"]),
                "ts": time.time(),
                "times_seen": int(existing.get("times_seen", 1)),
                "source": existing.get("source", "derived"),
                "confidence": _normalize_confidence(existing.get("confidence")),
            }
            if content is not None and content.strip():
                # Updating the document re-computes the embedding
                self._collection.update(ids=[record_id], documents=[content.strip()], metadatas=[meta])
            else:
                self._collection.update(ids=[record_id], metadatas=[meta])
        logger.info("Updated conclusion %s", record_id)
        return True

    def add_manual_conclusion(self, peer: str, kind: str, content: str,
                              premises: list[str] | None = None,
                              confidence: str = "high") -> str | None:
        """
        This function adds an operator- or agent-authored conclusion, bypassing
        deduplication, and tags it with source="manual" so the daily consolidation
        pass never prunes or rewrites it. Such facts are high confidence by default
        because they were explicitly committed rather than inferred. Returns the new
        record id or None on invalid input.
        """
        content = (content or "").strip()
        if peer not in PEERS or kind not in KINDS or not content:
            return None
        record_id: str = str(uuid.uuid4())
        with self._write_lock:
            self._collection.add(
                ids=[record_id],
                documents=[content],
                metadatas=[{
                    "peer": peer,
                    "kind": kind,
                    "premises": json.dumps(premises or []),
                    "ts": time.time(),
                    "times_seen": 1,
                    "source": "manual",
                    "confidence": _normalize_confidence(confidence),
                }],
            )
        logger.info("Added manual conclusion %s for peer '%s'", record_id, peer)
        return record_id

    def delete_all_for_peer(self, peer: str) -> int:
        """
        This function deletes every conclusion stored for a peer. Returns the number
        of records removed.
        """
        if peer not in PEERS:
            return 0
        with self._write_lock:
            results: dict[str, Any] = self._collection.get(where={"peer": peer})
            ids: list[str] = results.get("ids", []) or []
            if ids:
                self._collection.delete(ids=ids)
        logger.info("Wiped %d conclusion(s) for peer '%s'", len(ids), peer)
        return len(ids)

    def export(self, peer: str | None = None) -> list[dict[str, Any]]:
        """
        This function exports conclusions (with ids and metadata) for backup or
        transfer, optionally limited to a single peer.
        """
        where: dict[str, Any] | None = self._build_where(peer=peer, kinds=None)
        results: dict[str, Any] = self._collection.get(where=where)
        ids: list[str] = results.get("ids", []) or []
        documents: list[str] = results.get("documents", []) or []
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []
        return [
            self._to_record(record_id=record_id, content=content, meta=meta)
            for record_id, content, meta in zip(ids, documents, metadatas)
        ]

    def import_records(self, records: list[dict[str, Any]]) -> int:
        """
        This function imports previously exported conclusions, generating new ids and
        skipping invalid entries. Returns the number imported.
        """
        imported: int = 0
        with self._write_lock:
            for record in records:
                peer: str = record.get("peer", "")
                kind: str = record.get("kind", "")
                content: str = (record.get("content") or "").strip()
                if peer not in PEERS or kind not in KINDS or not content:
                    continue
                premises: Any = record.get("premises", [])
                if not isinstance(premises, list):
                    premises = []
                self._collection.add(
                    ids=[str(uuid.uuid4())],
                    documents=[content],
                    metadatas=[{
                        "peer": peer,
                        "kind": kind,
                        "premises": json.dumps(premises),
                        "ts": float(record.get("ts", time.time())) or time.time(),
                        "times_seen": int(record.get("times_seen", 1)),
                        "source": record.get("source", "derived"),
                        "confidence": _normalize_confidence(record.get("confidence")),
                    }],
                )
                imported += 1
        logger.info("Imported %d conclusion(s)", imported)
        return imported

    def replace_derived_conclusions(self, peer: str, records: list[dict[str, Any]]) -> tuple[int, int]:
        """
        This function atomically replaces a peer's derived conclusions with a distilled
        set produced by the daily consolidation pass. Operator-authored conclusions
        (source="manual") are preserved untouched. Returns (removed, added).

        The caller must guarantee records is non-empty so a failed distillation can
        never wipe the peer's memory.
        """
        if peer not in PEERS or not records:
            return (0, 0)

        with self._write_lock:
            existing: dict[str, Any] = self._collection.get(where={"peer": peer})
            ids: list[str] = existing.get("ids", []) or []
            metadatas: list[dict[str, Any]] = existing.get("metadatas", []) or []
            # Only the user's own derived conclusions are replaced; manual ones and
            # entity facts (which have their own per-entity curation) are kept as-is.
            # Older records migrated from before the source field default to "derived".
            derived_ids: list[str] = [
                record_id
                for record_id, meta in zip(ids, metadatas)
                if (meta or {}).get("source", "derived") != "manual"
                and not ((meta or {}).get("entity", "") or "")
            ]

            added: int = 0
            new_ids: list[str] = []
            new_documents: list[str] = []
            new_metadatas: list[dict[str, Any]] = []
            now: float = time.time()
            for record in records:
                content: str = (record.get("content") or "").strip()
                kind: str = record.get("kind", "observation")
                if not content or kind not in KINDS:
                    continue
                premises: Any = record.get("premises", [])
                if not isinstance(premises, list):
                    premises = []
                new_ids.append(str(uuid.uuid4()))
                new_documents.append(content)
                new_metadatas.append({
                    "peer": peer,
                    "kind": kind,
                    "premises": json.dumps(premises),
                    "ts": now,
                    "times_seen": 1,
                    "source": "derived",
                    "confidence": _normalize_confidence(record.get("confidence")),
                    "entity": "",
                    "entity_name": "",
                    "entity_type": "",
                    "relation": "",
                })
                added += 1

            # Refuse to delete if the distilled set turned out empty after validation
            if added == 0:
                return (0, 0)

            if derived_ids:
                self._collection.delete(ids=derived_ids)
            self._collection.add(ids=new_ids, documents=new_documents, metadatas=new_metadatas)

        logger.info(
            "Consolidated peer '%s': removed %d derived, stored %d distilled",
            peer, len(derived_ids), added,
        )
        return (len(derived_ids), added)

    def prune_stale_derived(self, peer: str, stale_seconds: float = DERIVED_STALE_SECONDS) -> int:
        """
        This function removes low-value derived conclusions that have aged out: a
        record is pruned only when it is derived (never manual), low confidence, was
        never reinforced (seen exactly once), and was last touched longer ago than
        stale_seconds. Reinforced, high/medium confidence, and operator-authored
        memories are always kept. Returns the number of records removed.
        """
        if peer not in PEERS or self._collection.count() == 0:
            return 0

        cutoff: float = time.time() - max(0.0, stale_seconds)
        with self._write_lock:
            existing: dict[str, Any] = self._collection.get(where={"peer": peer})
            ids: list[str] = existing.get("ids", []) or []
            metadatas: list[dict[str, Any]] = existing.get("metadatas", []) or []
            stale_ids: list[str] = [
                record_id
                for record_id, meta in zip(ids, metadatas)
                if (meta or {}).get("source", "derived") != "manual"
                and _normalize_confidence((meta or {}).get("confidence")) == "low"
                and int((meta or {}).get("times_seen", 1)) <= 1
                and float((meta or {}).get("ts", 0.0)) < cutoff
            ]
            if stale_ids:
                self._collection.delete(ids=stale_ids)

        if stale_ids:
            logger.info("Pruned %d stale derived conclusion(s) for peer '%s'", len(stale_ids), peer)
        return len(stale_ids)

    def _build_where(self, peer: str | None, kinds: list[str] | None) -> dict[str, Any] | None:
        """
        This function builds a Chroma where filter from a peer and/or kinds.
        """
        conditions: list[dict[str, Any]] = []
        if peer in PEERS:
            conditions.append({"peer": peer})
        if kinds:
            valid: list[str] = [k for k in kinds if k in KINDS]
            if valid:
                conditions.append({"kind": {"$in": valid}})
        if len(conditions) == 1:
            return conditions[0]
        if len(conditions) > 1:
            return {"$and": conditions}
        return None

    @staticmethod
    def _to_record(record_id: str, content: str, meta: dict[str, Any]) -> dict[str, Any]:
        """
        This function converts a stored row into a uniform admin record dict.
        """
        try:
            premises: list[str] = json.loads(meta.get("premises", "[]"))
            if not isinstance(premises, list):
                premises = []
        except (json.JSONDecodeError, TypeError):
            premises = []
        return {
            "id": record_id,
            "content": content,
            "peer": meta.get("peer", ""),
            "kind": meta.get("kind", ""),
            "premises": premises,
            "times_seen": int(meta.get("times_seen", 1)),
            "ts": float(meta.get("ts", 0.0)),
            "source": meta.get("source", "derived"),
            "confidence": _normalize_confidence(meta.get("confidence")),
            "entity": meta.get("entity", "") or "",
            "entity_name": meta.get("entity_name", "") or "",
            "entity_type": meta.get("entity_type", "") or "",
            "relation": meta.get("relation", "") or "",
        }
