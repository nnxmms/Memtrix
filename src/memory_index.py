#!/usr/bin/python3

import hashlib
import os
import threading
import time
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

from src.config import CONFIG_PATH

# Model is downloaded once to data/models/ and reused across restarts
EMBEDDING_MODEL: str = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIM: int = 256  # Matryoshka truncation (768 -> 256) for faster inference

# Periodic sync interval in seconds
SYNC_INTERVAL: int = 300


class LocalEmbeddingFunction:

    def __init__(self, model_dir: str) -> None:
        """
        This is the LocalEmbeddingFunction which generates embeddings using a local model.
        """
        # Redirect all HuggingFace caches to the writable data volume
        os.environ["HF_HOME"] = model_dir
        os.environ["TRANSFORMERS_CACHE"] = model_dir

        self._model: SentenceTransformer = SentenceTransformer(
            model_name_or_path=EMBEDDING_MODEL,
            cache_folder=model_dir,
            trust_remote_code=True,
            truncate_dim=EMBEDDING_DIM
        )

    @staticmethod
    def name() -> str:
        """
        This function returns the embedding function name for ChromaDB.
        """
        return "local"

    def __call__(self, input: list[str]) -> list[list[float]]:
        """
        This function generates embeddings for a list of texts.
        """
        prefixed: list[str] = [f"search_document: {text}" for text in input]
        embeddings = self._model.encode(sentences=prefixed, normalize_embeddings=True, show_progress_bar=False)
        return embeddings.tolist()

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """
        This function generates embeddings for query texts.
        """
        prefixed: list[str] = [f"search_query: {text}" for text in input]
        embeddings = self._model.encode(sentences=prefixed, normalize_embeddings=True, show_progress_bar=False)
        return embeddings.tolist()


class MemoryIndex:

    _instance: "MemoryIndex | None" = None

    @classmethod
    def get_instance(cls, workspace_dir: str) -> "MemoryIndex":
        """
        This function returns the singleton MemoryIndex instance.
        """
        if cls._instance is None:
            cls._instance = cls(workspace_dir=workspace_dir)
        return cls._instance

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryIndex class which manages vector search over daily memory files.
        """
        # Model cache directory (inside mounted data volume)
        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        # Local embedding function
        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction(
            model_dir=model_dir
        )

        # ChromaDB persistent client
        persist_dir: str = os.path.join(data_dir, "memory_index")
        self._client: chromadb.ClientAPI = chromadb.PersistentClient(
            path=persist_dir,
            settings=chromadb.Settings(
                anonymized_telemetry=False,
                is_persistent=True
            )
        )
        self._collection: chromadb.Collection = self._client.get_or_create_collection(
            name="daily_memories",
            embedding_function=self._embedding_fn
        )

        # Memory directory
        self._memory_dir: str = os.path.join(workspace_dir, "memory")

        # Content hashes to detect changes (filename -> md5 hex)
        self._hashes: dict[str, str] = {}

        # Full reindex on startup
        self._reindex_all()

    @staticmethod
    def _hash_content(content: str) -> str:
        """
        This function returns the MD5 hex digest of a string.
        """
        return hashlib.md5(content.encode()).hexdigest()

    def _reindex_all(self) -> None:
        """
        This function reindexes all memory files on startup.
        """
        if not os.path.isdir(s=self._memory_dir):
            return

        for filename in sorted(os.listdir(self._memory_dir)):
            if not filename.endswith(".md"):
                continue

            path: str = os.path.join(self._memory_dir, filename)
            with open(file=path, mode="r") as f:
                content: str = f.read()

            if content.strip():
                self._hashes[filename] = self._hash_content(content=content)
                self._index_memory(filename=filename, content=content)

    def sync_changed(self) -> None:
        """
        This function checks for new or modified memory files and reindexes them.
        """
        if not os.path.isdir(s=self._memory_dir):
            return

        for filename in sorted(os.listdir(self._memory_dir)):
            if not filename.endswith(".md"):
                continue

            path: str = os.path.join(self._memory_dir, filename)
            with open(file=path, mode="r") as f:
                content: str = f.read()

            if not content.strip():
                continue

            content_hash: str = self._hash_content(content=content)
            if self._hashes.get(filename) == content_hash:
                continue

            self._hashes[filename] = content_hash
            self._index_memory(filename=filename, content=content)

    def _index_memory(self, filename: str, content: str) -> None:
        """
        This function indexes or updates a memory file in the vector store.
        """
        date_str: str = filename.replace(".md", "")
        self._collection.upsert(
            ids=[filename],
            documents=[content],
            metadatas=[{"date": date_str, "filename": filename}]
        )

    def start_periodic_sync(self) -> None:
        """
        This function starts a background thread that periodically syncs changed memory files.
        """
        def _sync_loop() -> None:
            while True:
                time.sleep(SYNC_INTERVAL)
                try:
                    self.sync_changed()
                except Exception as e:
                    print(f"Memory sync error: {e}")

        thread: threading.Thread = threading.Thread(target=_sync_loop, daemon=True)
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

        matches: list[dict[str, Any]] = []
        for i in range(len(results["ids"][0])):
            matches.append({
                "filename": results["metadatas"][0][i]["filename"],
                "date": results["metadatas"][0][i]["date"],
                "snippet": results["documents"][0][i][:300],
                "distance": results["distances"][0][i]
            })

        return matches
