#!/usr/bin/python3

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

from src.core.config import CONFIG_PATH

logger: logging.Logger = logging.getLogger(__name__)

# Model is downloaded once to data/models/ and reused across restarts
EMBEDDING_MODEL: str = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIM: int = 256  # Matryoshka truncation (768 -> 256) for faster inference

# Periodic sync interval in seconds
SYNC_INTERVAL: int = 300

# Target size of a transcript chunk in characters (~800 tokens at ~4 chars/token).
# Consecutive turns are grouped up to this size so each embedded chunk carries
# enough surrounding context to be independently searchable.
CHUNK_TARGET_CHARS: int = 3200


class LocalEmbeddingFunction:

    _instance: "LocalEmbeddingFunction | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls, model_dir: str) -> "LocalEmbeddingFunction":
        """
        This function returns the singleton LocalEmbeddingFunction instance, shared
        across the memory, docs, and representation stores. The underlying model is
        loaded lazily on first use (see warm_up / _ensure_model), so obtaining the
        instance never blocks startup.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(model_dir=model_dir)
        return cls._instance

    def __init__(self, model_dir: str) -> None:
        """
        This is the LocalEmbeddingFunction which generates embeddings using a local
        model. Construction is intentionally cheap: the SentenceTransformer (which
        takes 40-60s to load) is built lazily on the first embedding call rather
        than here, so creating the index singletons never blocks startup.
        """
        # Redirect all HuggingFace caches to the writable data volume. This must be
        # set before the model is ever loaded, hence it happens at construction.
        os.environ["HF_HOME"] = model_dir
        os.environ["TRANSFORMERS_CACHE"] = model_dir

        self._model_dir: str = model_dir
        self._model: SentenceTransformer | None = None
        self._model_lock: threading.Lock = threading.Lock()

    def warm_up(self) -> None:
        """
        This function loads the embedding model if it is not loaded yet. It is safe
        to call from a background thread to pre-load the model off the startup path;
        concurrent callers wait for the single in-progress load.
        """
        self._ensure_model()

    def _ensure_model(self) -> SentenceTransformer:
        """
        This function returns the loaded model, building it on first use under a
        lock so that exactly one load happens even under concurrent access.
        """
        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is not None:
                return self._model

            model_dir: str = self._model_dir

            # Use the local cache only when the model is already downloaded — this
            # avoids slow HuggingFace Hub network calls on every startup.
            model_cached: bool = any(
                entry.startswith("models--")
                for entry in os.listdir(model_dir)
                if os.path.isdir(os.path.join(model_dir, entry))
            ) if os.path.isdir(model_dir) else False

            if model_cached:
                logger.info("Loading embedding model from local cache")

            model: SentenceTransformer = SentenceTransformer(
                model_name_or_path=EMBEDDING_MODEL,
                cache_folder=model_dir,
                trust_remote_code=True,
                local_files_only=model_cached,
                truncate_dim=EMBEDDING_DIM,
            )
            logger.info("Embedding model loaded (%s, dim=%d)", EMBEDDING_MODEL, EMBEDDING_DIM)

            self._model = model
            return self._model

    @staticmethod
    def name() -> str:
        """
        This function returns the embedding function name for ChromaDB.
        """
        return "local"

    def __call__(self, input: list[str]) -> Any:
        """
        This function generates embeddings for a list of documents being indexed. It
        returns numpy arrays (not Python lists) because ChromaDB's HttpClient
        serializes query embeddings via .tolist() and expects numpy arrays on the
        way out.
        """
        return self._encode(texts=input, prefix="search_document: ")

    def embed_query(self, input: list[str]) -> Any:
        """
        This function generates embeddings for search queries. ChromaDB 1.5+ calls
        this (not __call__) for the query side, and nomic-embed-text is trained with
        a distinct "search_query:" task prefix that improves retrieval quality, so
        queries are embedded separately from indexed documents.
        """
        return self._encode(texts=input, prefix="search_query: ")

    def _encode(self, texts: list[str], prefix: str) -> Any:
        """
        This function applies the task prefix and returns normalized embeddings as a
        list of numpy arrays.
        """
        model: SentenceTransformer = self._ensure_model()
        prefixed: list[str] = [f"{prefix}{text}" for text in texts]
        embeddings = model.encode(sentences=prefixed, normalize_embeddings=True, show_progress_bar=False)
        return list(embeddings)


class ConversationIndex:

    _instances: dict[str, "ConversationIndex"] = {}

    @classmethod
    def get_instance(
        cls,
        workspace_dir: str,
        sessions_dir: str | None = None,
        collection_name: str = "conversations",
    ) -> "ConversationIndex":
        """
        This function returns the ConversationIndex instance for a given workspace
        directory, creating one if it does not exist yet. Sub-agents pass their own
        sessions directory and a dedicated collection name so their conversation
        memory stays isolated from the main agent's.
        """
        if workspace_dir not in cls._instances:
            cls._instances[workspace_dir] = cls(
                workspace_dir=workspace_dir,
                sessions_dir=sessions_dir,
                collection_name=collection_name,
            )
        return cls._instances[workspace_dir]

    def __init__(
        self,
        workspace_dir: str,
        sessions_dir: str | None = None,
        collection_name: str = "conversations",
    ) -> None:
        """
        This is the ConversationIndex class which embeds raw conversation transcripts
        from stored sessions and makes them semantically searchable, so the agent can
        recall what was discussed in past conversations days or weeks later.
        """
        # Data volume root (holds models, sessions, and the vector index)
        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        # Local embedding function (singleton — loaded once, shared across all agents)
        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction.get_instance(
            model_dir=model_dir
        )

        # ChromaDB persistent client for the conversation transcript chunks —
        # sub-agents get an isolated subdirectory and collection.
        if collection_name == "conversations":
            persist_dir: str = os.path.join(data_dir, "conversation_index")
        else:
            persist_dir = os.path.join(data_dir, "conversation_index", collection_name)
        self._client: chromadb.ClientAPI = chromadb.PersistentClient(
            path=persist_dir,
            settings=chromadb.Settings(
                anonymized_telemetry=False,
                is_persistent=True
            )
        )
        self._collection: chromadb.Collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_fn
        )

        # Stored sessions live under the data volume (sessions/<date>/<uuid>.json)
        self._sessions_dir: str = sessions_dir or os.path.join(data_dir, "sessions")

        # Per-chunk content hashes (chunk id -> md5 hex), persisted next to the
        # collection so a restart can skip re-embedding unchanged transcript chunks.
        self._hash_path: str = os.path.join(persist_dir, ".chunk-hashes.json")
        self._hashes: dict[str, str] = {}

        # Indexing runs on the background sync thread (see start_periodic_sync) so
        # that model loading and embedding never block startup.
        self._sync_started: bool = False
        logger.info("Conversation index created; indexing in background")

    @staticmethod
    def _hash_content(content: str) -> str:
        """
        This function returns the MD5 hex digest of a string.
        """
        return hashlib.md5(content.encode()).hexdigest()

    def _load_hashes(self) -> dict[str, str]:
        """
        This function loads the persisted chunk-id -> content-hash cache, returning
        an empty mapping when it is missing or unreadable.
        """
        try:
            with open(file=self._hash_path, mode="r", encoding="utf-8") as f:
                data: Any = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def _save_hashes(self) -> None:
        """
        This function atomically persists the chunk-id -> content-hash cache so the
        next startup can skip re-embedding unchanged transcript chunks.
        """
        try:
            tmp_path: str = self._hash_path + ".tmp"
            with open(file=tmp_path, mode="w", encoding="utf-8") as f:
                json.dump(self._hashes, f)
            os.replace(src=tmp_path, dst=self._hash_path)
        except OSError as e:
            logger.warning("Could not persist conversation hash cache: %s", e)

    def _reindex_all(self) -> None:
        """
        This function performs the initial index on startup. It loads the persisted
        hash cache first, so only transcript chunks that are new or changed since the
        last run are embedded — unchanged chunks already in the collection are skipped.
        """
        self._hashes = self._load_hashes()
        self._scan_and_index()

    def sync_changed(self) -> None:
        """
        This function re-scans the stored sessions and incrementally updates the index
        with any new or changed transcript chunks.
        """
        self._scan_and_index()

    @staticmethod
    def _clean_user_text(content: str) -> str:
        """
        This function strips the leading "[Channel: ...]" routing header from a user
        message so only the human's actual words are embedded.
        """
        lines: list[str] = content.split("\n")
        if lines and lines[0].startswith("[Channel:"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def _extract_chunks(self, messages: list[dict[str, Any]]) -> list[str]:
        """
        This function turns a stored session into windowed transcript chunks. It keeps
        only human and assistant prose (dropping the system prompt and tool traffic),
        skips internal agent-to-agent sessions, and groups consecutive turns into
        chunks of roughly CHUNK_TARGET_CHARS so each embedded unit carries enough
        context to be independently searchable.
        """
        # Skip internal agent-to-agent sessions — only index human conversations.
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                if str(msg.get("content", "")).startswith("[Channel: Internal"):
                    return []
                break

        entries: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role: str = msg.get("role", "")
            content: Any = msg.get("content", "")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            if role == "user":
                text: str = self._clean_user_text(content=content)
                label: str = "User"
            else:
                text = content.strip()
                label = "Assistant"
            if not text:
                continue
            entries.append(f"{label}: {text}")

        chunks: list[str] = []
        current: list[str] = []
        size: int = 0
        for entry in entries:
            current.append(entry)
            size += len(entry)
            if size >= CHUNK_TARGET_CHARS:
                chunks.append("\n\n".join(current))
                current = []
                size = 0
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _scan_and_index(self) -> None:
        """
        This function reconciles the vector store with the stored sessions: it walks
        every saved conversation, splits it into windowed transcript chunks, embeds
        chunks that are new or changed in a single batched upsert, and drops chunks
        whose sessions shrank or disappeared. A chunk is re-embedded only when its
        text hash changed or it is missing from the collection.
        """
        if not os.path.isdir(s=self._sessions_dir):
            return

        existing_ids: set[str] = set(self._collection.get(include=[]).get("ids", []) or [])

        seen_ids: set[str] = set()
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []

        for date_dir in sorted(os.listdir(self._sessions_dir)):
            dir_path: str = os.path.join(self._sessions_dir, date_dir)
            if not os.path.isdir(dir_path):
                continue
            for filename in sorted(os.listdir(dir_path)):
                if not filename.endswith(".json"):
                    continue
                session_id: str = filename[: -len(".json")]
                path: str = os.path.join(dir_path, filename)
                try:
                    with open(file=path, mode="r", encoding="utf-8") as f:
                        messages: Any = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning("Skipping unreadable session %s: %s", filename, e)
                    continue
                if not isinstance(messages, list):
                    continue

                for chunk_index, chunk_text in enumerate(self._extract_chunks(messages=messages)):
                    chunk_id: str = f"{session_id}:{chunk_index}"
                    seen_ids.add(chunk_id)
                    content_hash: str = self._hash_content(content=chunk_text)
                    if self._hashes.get(chunk_id) == content_hash and chunk_id in existing_ids:
                        continue
                    self._hashes[chunk_id] = content_hash
                    ids.append(chunk_id)
                    documents.append(chunk_text)
                    metadatas.append({"date": date_dir, "session_id": session_id})

        # One batched upsert lets ChromaDB embed every changed chunk in a single pass.
        if ids:
            self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

        # Drop index entries and cached hashes for chunks that no longer exist.
        stale_ids: list[str] = [doc_id for doc_id in existing_ids if doc_id not in seen_ids]
        if stale_ids:
            self._collection.delete(ids=stale_ids)
        for cached in [cid for cid in self._hashes if cid not in seen_ids]:
            del self._hashes[cached]

        if ids or stale_ids:
            self._save_hashes()

    def start_periodic_sync(self) -> None:
        """
        This function performs the initial full index and then periodically syncs
        changed sessions, all on a background thread so that startup is never blocked
        by model loading or embedding. It is a no-op if already started.
        """
        if self._sync_started:
            return
        self._sync_started = True

        def _sync_loop() -> None:
            try:
                self._reindex_all()
                logger.info(
                    "Conversation index ready (chunks=%d)",
                    self._collection.count(),
                )
            except Exception as e:
                logger.error("Initial conversation index error: %s", e, exc_info=True)

            while True:
                time.sleep(SYNC_INTERVAL)
                try:
                    self.sync_changed()
                except Exception as e:
                    logger.error("Conversation sync error: %s", e, exc_info=True)

        thread: threading.Thread = threading.Thread(target=_sync_loop, daemon=True, name="conversation-sync")
        thread.start()

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        This function searches the conversation index and returns matching transcript
        chunks ranked by semantic similarity.
        """
        # Don't request more results than we have documents
        total: int = self._collection.count()
        if total == 0:
            return []

        n: int = min(n_results, total)

        results: dict[str, Any] = self._collection.query(
            query_texts=[query],
            n_results=n
        )

        return [
            {
                "session_id": meta["session_id"],
                "date": meta["date"],
                "snippet": document[:400],
                "distance": distance,
            }
            for meta, document, distance in zip(
                results["metadatas"][0], results["documents"][0], results["distances"][0]
            )
        ]
