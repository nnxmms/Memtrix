#!/usr/bin/python3

import json
import os
from typing import Any

import chromadb
from ollama import Client

from src.config import CONFIG_PATH


class OllamaEmbeddingFunction:

    def __init__(self, base_url: str, model: str) -> None:
        """
        This is the OllamaEmbeddingFunction which generates embeddings via Ollama.
        """
        self._client: Client = Client(host=base_url)
        self._model: str = model

    @staticmethod
    def name() -> str:
        """
        This function returns the embedding function name for ChromaDB.
        """
        return "ollama"

    def __call__(self, input: list[str]) -> list[list[float]]:
        """
        This function generates embeddings for a list of texts.
        """
        response: Any = self._client.embed(model=self._model, input=input)
        return response.embeddings

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """
        This function generates embeddings for query texts.
        """
        return self.__call__(input=input)


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
        # Read config for Ollama connection and embedding model
        with open(file=CONFIG_PATH, mode="r") as f:
            config: dict[str, Any] = json.load(fp=f)

        provider_name: str = config["main-agent"]["provider"]
        provider_config: dict[str, Any] = config["providers"][provider_name]
        base_url: str = provider_config["base_url"]
        embedding_model: str = config["main-agent"].get("embedding_model", "nomic-embed-text")

        # Embedding function via Ollama
        self._embedding_fn: OllamaEmbeddingFunction = OllamaEmbeddingFunction(
            base_url=base_url,
            model=embedding_model
        )

        # ChromaDB persistent client
        data_dir: str = os.path.dirname(CONFIG_PATH)
        persist_dir: str = os.path.join(data_dir, "memory_index")
        self._client: chromadb.ClientAPI = chromadb.PersistentClient(path=persist_dir)
        self._collection: chromadb.Collection = self._client.get_or_create_collection(
            name="daily_memories",
            embedding_function=self._embedding_fn
        )

        # Memory directory
        self._memory_dir: str = os.path.join(workspace_dir, "memory")

        # Sync any existing memory files not yet indexed
        self._sync_existing()

    def _sync_existing(self) -> None:
        """
        This function indexes any existing memory files that are not yet in the vector store.
        """
        if not os.path.isdir(s=self._memory_dir):
            return

        existing_ids: set[str] = set(self._collection.get()["ids"])

        for filename in sorted(os.listdir(self._memory_dir)):
            if not filename.endswith(".md"):
                continue
            if filename in existing_ids:
                continue

            path: str = os.path.join(self._memory_dir, filename)
            with open(file=path, mode="r") as f:
                content: str = f.read()

            if content.strip():
                self.index_memory(filename=filename, content=content)

    def index_memory(self, filename: str, content: str) -> None:
        """
        This function indexes or updates a memory file in the vector store.
        """
        date_str: str = filename.replace(".md", "")
        self._collection.upsert(
            ids=[filename],
            documents=[content],
            metadatas=[{"date": date_str, "filename": filename}]
        )

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
