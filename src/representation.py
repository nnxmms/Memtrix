#!/usr/bin/python3

import json
import logging
import os
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import chromadb
from filelock import FileLock

from src.config import CONFIG_PATH
from src.memory_index import LocalEmbeddingFunction

logger: logging.Logger = logging.getLogger(__name__)

# Valid peers and conclusion kinds
PEERS: set[str] = {"user", "agent"}
KINDS: set[str] = {"observation", "deductive", "inductive"}

# Valid provenance values for a stored conclusion
SOURCES: set[str] = {"derived", "manual"}

# Peer card files (finite, always-injected summaries)
PEER_CARD_FILES: dict[str, str] = {"user": "USER.md", "agent": "MEMORY.md"}

# Two normalized embeddings are considered duplicates below this L2 distance
# (l2^2 = 2(1 - cosine); 0.35 ≈ cosine similarity > 0.93)
DEDUP_L2_THRESHOLD: float = 0.35


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
        "dual_peer": True,
        "inject_top_k": 5,
        "consolidation": True,                # daily memory-distillation pass
        "consolidation_interval_hours": 24,   # how often distillation runs
        "consolidation_min_items": 12,        # skip distillation below this many derived items
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
        each peer (the user and the agent itself) and manages the finite peer-card files.
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
        Each record is {"kind": str, "content": str, "premises": list[str]}.
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
                    }],
                )
                added += 1

        if added:
            logger.info("Stored %d new conclusion(s) for peer '%s'", added, peer)
        return added

    def _bump_if_duplicate(self, peer: str, content: str) -> bool:
        """
        This function checks whether a near-identical conclusion already exists for the
        peer and, if so, increments its seen counter. Returns True when a duplicate was found.
        """
        if self._collection.count() == 0:
            return False

        results: dict[str, Any] = self._collection.query(
            query_texts=[content],
            n_results=1,
            where={"peer": peer},
        )
        ids: list[list[str]] = results.get("ids", [[]])
        distances: list[list[float]] = results.get("distances", [[]])
        if not ids or not ids[0]:
            return False

        if distances[0][0] <= DEDUP_L2_THRESHOLD:
            existing_id: str = ids[0][0]
            metadata: dict[str, Any] = results["metadatas"][0][0]
            metadata["times_seen"] = int(metadata.get("times_seen", 1)) + 1
            metadata["ts"] = time.time()
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
            })
        return matches

    def all_for_peer(self, peer: str, limit: int = 100) -> list[dict[str, Any]]:
        """
        This function returns stored conclusions for a peer, most-seen first.
        """
        if peer not in PEERS or self._collection.count() == 0:
            return []

        results: dict[str, Any] = self._collection.get(where={"peer": peer})
        documents: list[str] = results.get("documents", []) or []
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []

        records: list[dict[str, Any]] = []
        for content, meta in zip(documents, metadatas):
            records.append({
                "content": content,
                "kind": meta.get("kind", ""),
                "times_seen": int(meta.get("times_seen", 1)),
                "ts": float(meta.get("ts", 0.0)),
            })
        records.sort(key=lambda r: (r["times_seen"], r["ts"]), reverse=True)
        return records[:limit]

    def read_peer_card(self, peer: str) -> str:
        """
        This function reads the finite peer-card file for a peer (USER.md or MEMORY.md).
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
        trimmed: str = text.strip()
        if len(trimmed) > max_chars:
            trimmed = trimmed[:max_chars].rstrip()
        path: str = os.path.join(self._workspace_dir, filename)
        with self._write_lock, FileLock(path + ".lock", timeout=15):
            with open(file=path, mode="w") as f:
                f.write(trimmed + "\n")

    # ----------------------------------------------------------------- admin API

    def list_conclusions(self, peer: str | None = None, kinds: list[str] | None = None,
                         limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """
        This function returns stored conclusions (including their ids and full
        metadata) for administration, optionally filtered by peer and kinds, sorted
        most-recent first. Used by the web control panel.
        """
        where: dict[str, Any] | None = self._build_where(peer=peer, kinds=kinds)
        results: dict[str, Any] = self._collection.get(where=where)
        ids: list[str] = results.get("ids", []) or []
        documents: list[str] = results.get("documents", []) or []
        metadatas: list[dict[str, Any]] = results.get("metadatas", []) or []

        records: list[dict[str, Any]] = []
        for record_id, content, meta in zip(ids, documents, metadatas):
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
            }
            if content is not None and content.strip():
                # Updating the document re-computes the embedding
                self._collection.update(ids=[record_id], documents=[content.strip()], metadatas=[meta])
            else:
                self._collection.update(ids=[record_id], metadatas=[meta])
        logger.info("Updated conclusion %s", record_id)
        return True

    def add_manual_conclusion(self, peer: str, kind: str, content: str,
                              premises: list[str] | None = None) -> str | None:
        """
        This function adds an operator-authored conclusion, bypassing deduplication,
        and tags it with source="manual". Returns the new record id or None on
        invalid input.
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
            # Only derived conclusions are replaced; manual ones are kept as-is. Older
            # records migrated from before the source field default to "derived".
            derived_ids: list[str] = [
                record_id
                for record_id, meta in zip(ids, metadatas)
                if (meta or {}).get("source", "derived") != "manual"
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
        }
