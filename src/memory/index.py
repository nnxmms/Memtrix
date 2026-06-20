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


class MemoryIndex:

    _instances: dict[str, "MemoryIndex"] = {}

    @classmethod
    def get_instance(cls, workspace_dir: str, collection_name: str = "daily_memories") -> "MemoryIndex":
        """
        This function returns the MemoryIndex instance for a given workspace directory.
        Creates a new instance if one doesn't exist yet.
        """
        if workspace_dir not in cls._instances:
            cls._instances[workspace_dir] = cls(workspace_dir=workspace_dir, collection_name=collection_name)
        return cls._instances[workspace_dir]

    def __init__(self, workspace_dir: str, collection_name: str = "daily_memories") -> None:
        """
        This is the MemoryIndex class which manages vector search over daily memory files.
        """
        # Model cache directory (inside mounted data volume)
        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        # Local embedding function (singleton — loaded once, shared across all agents)
        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction.get_instance(
            model_dir=model_dir
        )

        # ChromaDB persistent client — sub-agents get a subdirectory
        if collection_name == "daily_memories":
            persist_dir: str = os.path.join(data_dir, "memory_index")
        else:
            persist_dir: str = os.path.join(data_dir, "memory_index", collection_name)
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

        # Memory directory
        self._memory_dir: str = os.path.join(workspace_dir, "memory")

        # Content hashes to detect changes (filename -> md5 hex), persisted next to
        # the collection so a restart can skip re-embedding unchanged files.
        self._hash_path: str = os.path.join(persist_dir, ".file-hashes.json")
        self._hashes: dict[str, str] = {}

        # Indexing runs on the background sync thread (see start_periodic_sync) so
        # that model loading and embedding never block startup.
        self._collection_name: str = collection_name
        self._sync_started: bool = False
        logger.info("Memory index created (collection=%s); indexing in background", collection_name)

    @staticmethod
    def _hash_content(content: str) -> str:
        """
        This function returns the MD5 hex digest of a string.
        """
        return hashlib.md5(content.encode()).hexdigest()

    def _load_hashes(self) -> dict[str, str]:
        """
        This function loads the persisted filename -> content-hash cache, returning
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
        This function atomically persists the filename -> content-hash cache so the
        next startup can skip re-embedding unchanged files.
        """
        try:
            tmp_path: str = self._hash_path + ".tmp"
            with open(file=tmp_path, mode="w", encoding="utf-8") as f:
                json.dump(self._hashes, f)
            os.replace(src=tmp_path, dst=self._hash_path)
        except OSError as e:
            logger.warning("Could not persist memory hash cache: %s", e)

    def _reindex_all(self) -> None:
        """
        This function performs the initial index on startup. It loads the persisted
        hash cache first, so only files that are new or changed since the last run
        are embedded — unchanged files already in the collection are skipped.
        """
        self._hashes = self._load_hashes()
        self._scan_and_index()

    def sync_changed(self) -> None:
        """
        This function checks for new, modified, or deleted memory files and updates
        the index incrementally.
        """
        self._scan_and_index()

    def _scan_and_index(self) -> None:
        """
        This function reconciles the vector store with the memory directory: it
        embeds new or changed files in a single batched upsert, drops entries for
        files that no longer exist, and persists the hash cache when anything moved.
        A file is re-embedded only when its content hash changed or it is missing
        from the collection (guarding against collection/cache divergence).
        """
        if not os.path.isdir(s=self._memory_dir):
            return

        existing_ids: set[str] = set(self._collection.get(include=[]).get("ids", []) or [])

        on_disk: set[str] = set()
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []

        for filename in sorted(os.listdir(self._memory_dir)):
            if not filename.endswith(".md"):
                continue
            on_disk.add(filename)

            path: str = os.path.join(self._memory_dir, filename)
            try:
                with open(file=path, mode="r", encoding="utf-8") as f:
                    content: str = f.read()
            except OSError as e:
                logger.warning("Skipping unreadable memory file %s: %s", filename, e)
                continue

            if not content.strip():
                continue

            content_hash: str = self._hash_content(content=content)
            if self._hashes.get(filename) == content_hash and filename in existing_ids:
                continue

            self._hashes[filename] = content_hash
            ids.append(filename)
            documents.append(content)
            metadatas.append({"date": filename.replace(".md", ""), "filename": filename})

        # One batched upsert lets ChromaDB embed every changed file in a single pass.
        if ids:
            self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

        # Drop index entries and cached hashes for files that no longer exist.
        stale_ids: list[str] = [doc_id for doc_id in existing_ids if doc_id not in on_disk]
        if stale_ids:
            self._collection.delete(ids=stale_ids)
        for cached in [name for name in self._hashes if name not in on_disk]:
            del self._hashes[cached]

        if ids or stale_ids:
            self._save_hashes()

    def start_periodic_sync(self) -> None:
        """
        This function performs the initial full index and then periodically syncs
        changed memory files, all on a background thread so that startup is never
        blocked by model loading or embedding. It is a no-op if already started.
        """
        if self._sync_started:
            return
        self._sync_started = True

        def _sync_loop() -> None:
            try:
                self._reindex_all()
                logger.info(
                    "Memory index ready (collection=%s, docs=%d)",
                    self._collection_name, self._collection.count(),
                )
            except Exception as e:
                logger.error("Initial memory index error: %s", e, exc_info=True)

            while True:
                time.sleep(SYNC_INTERVAL)
                try:
                    self.sync_changed()
                except Exception as e:
                    logger.error("Memory sync error: %s", e, exc_info=True)

        thread: threading.Thread = threading.Thread(target=_sync_loop, daemon=True, name="memory-sync")
        thread.start()

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        This function searches the memory index and returns matching results.
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
                "filename": meta["filename"],
                "date": meta["date"],
                "snippet": document[:300],
                "distance": distance,
            }
            for meta, document, distance in zip(
                results["metadatas"][0], results["documents"][0], results["distances"][0]
            )
        ]
