#!/usr/bin/python3

import json
import logging
import os
import threading
import time
import uuid
from typing import Any

import chromadb

from src.config import CONFIG_PATH
from src.memory_index import LocalEmbeddingFunction

logger: logging.Logger = logging.getLogger(__name__)

# Valid peers and conclusion kinds
PEERS: set[str] = {"user", "agent"}
KINDS: set[str] = {"observation", "deductive", "inductive"}

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
        self._client: chromadb.ClientAPI = chromadb.PersistentClient(
            path=persist_dir,
            settings=chromadb.Settings(anonymized_telemetry=False, is_persistent=True),
        )
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
                "content": results["documents"][0][i],
                "peer": meta.get("peer", ""),
                "kind": meta.get("kind", ""),
                "distance": results["distances"][0][i],
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
        character budget so the card stays compact.
        """
        filename: str | None = PEER_CARD_FILES.get(peer)
        if not filename:
            return
        trimmed: str = text.strip()
        if len(trimmed) > max_chars:
            trimmed = trimmed[:max_chars].rstrip()
        path: str = os.path.join(self._workspace_dir, filename)
        with self._write_lock:
            with open(file=path, mode="w") as f:
                f.write(trimmed + "\n")
