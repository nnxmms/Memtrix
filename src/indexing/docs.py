#!/usr/bin/python3

import hashlib
import logging
import os
import threading
import time
from typing import Any

import chromadb
from bs4 import BeautifulSoup, Tag

from src.core.config import CONFIG_PATH
from src.memory.index import LocalEmbeddingFunction
from src.memory.store import _make_chroma_client

logger: logging.Logger = logging.getLogger(__name__)

# Collection holding the parsed documentation sections
COLLECTION_NAME: str = "documentation"

# Periodic sync interval in seconds (matches the daily-memory index)
SYNC_INTERVAL: int = 300

# Bundled documentation source (website/docs.html copied into src/static at build
# time). This module lives in src/indexing/, so resolve up one level to src/static.
DOCS_PATH: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "docs.html")

# Headings that start a new searchable section within a documentation page
SECTION_TAGS: set[str] = {"h2", "h3"}

# Structural elements that carry no useful prose for retrieval
SKIP_CLASSES: set[str] = {"doc-breadcrumb", "nav-cards", "nav-card"}


class DocsIndex:

    _instance: "DocsIndex | None" = None

    @classmethod
    def get_instance(cls) -> "DocsIndex":
        """
        This function returns the singleton DocsIndex instance.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        """
        This is the DocsIndex class which manages vector search over the bundled
        Memtrix documentation (website/docs.html) so the agent can research itself.
        """
        # Model cache directory (inside mounted data volume)
        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        # Local embedding function (singleton — shared with the memory index)
        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction.get_instance(
            model_dir=model_dir
        )

        # Shared ChromaDB client (HttpClient when CHROMA_URL is set, else local)
        persist_dir: str = os.path.join(data_dir, "docs_index")
        self._client: chromadb.ClientAPI = _make_chroma_client(persist_dir=persist_dir)
        self._collection: chromadb.Collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedding_fn,
        )

        # Indexing runs on the background sync thread (see start_periodic_sync) so
        # that model loading and embedding never block startup.
        self._sync_started: bool = False
        logger.info("Docs index created; indexing in background")

    @staticmethod
    def _hash_content(content: str) -> str:
        """
        This function returns the MD5 hex digest of a string.
        """
        return hashlib.md5(content.encode()).hexdigest()

    def _read_docs(self) -> str | None:
        """
        This function reads the bundled documentation file, or returns None if it
        is not present (e.g. running from a source tree without the build step).
        """
        if not os.path.isfile(DOCS_PATH):
            logger.warning("Documentation file not found at %s; docs tools will be empty", DOCS_PATH)
            return None
        with open(file=DOCS_PATH, mode="r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _element_text(element: Tag) -> str:
        """
        This function extracts readable text from a parsed HTML element, collapsing
        whitespace and preserving code blocks as plain text.
        """
        return element.get_text(separator=" ", strip=True)

    def _parse(self, html: str) -> list[dict[str, Any]]:
        """
        This function parses the documentation HTML into searchable section chunks.
        Each chunk corresponds to a heading and the prose that follows it within a
        documentation page.
        """
        soup: BeautifulSoup = BeautifulSoup(html, "html.parser")
        chunks: list[dict[str, Any]] = []

        for page in soup.select("div.doc-page"):
            page_id: str = (page.get("id") or "").replace("page-", "") or "page"
            h1: Tag | None = page.find("h1")
            page_title: str = self._element_text(h1) if isinstance(h1, Tag) else page_id

            # Section state: heading title, anchor id, and accumulated text parts
            section_title: str = page_title
            section_id: str = page_id
            parts: list[str] = []
            seq: int = 0

            def flush() -> None:
                nonlocal seq
                body: str = " ".join(p for p in parts if p).strip()
                if not body:
                    return
                anchor: str = f"docs.html#{section_id}" if section_id else "docs.html"
                heading: str = f"{page_title} — {section_title}" if section_title != page_title else page_title
                document: str = f"{heading}\n\n{body}"
                # Unique chunk id: section ids can repeat (intro prose and headings
                # without an id both fall back to page_id), so disambiguate by sequence.
                chunk_id: str = f"{page_id}::{section_id}::{seq}"
                seq += 1
                chunks.append({
                    "id": chunk_id,
                    "document": document,
                    "metadata": {
                        "page_id": page_id,
                        "page_title": page_title,
                        "section_id": section_id,
                        "section_title": section_title,
                        "anchor": anchor,
                        "kind": "section",
                    },
                })

            for child in page.find_all(recursive=False):
                if not isinstance(child, Tag):
                    continue

                classes: list[str] = child.get("class") or []
                if any(c in SKIP_CLASSES for c in classes):
                    continue

                name: str = child.name or ""

                if name == "h1":
                    continue

                if name in SECTION_TAGS:
                    # Start a new section: flush the previous one first
                    flush()
                    parts = []
                    section_title = self._element_text(child) or page_title
                    child_id: str = child.get("id") or ""
                    section_id = child_id or page_id
                    continue

                text: str = self._element_text(child)
                if text:
                    parts.append(text)

            # Flush the final section of the page
            flush()

        return chunks

    def _reindex_if_changed(self) -> None:
        """
        This function rebuilds the documentation index when the source file has
        changed since the last run, and is a no-op when it is already current.
        """
        html: str | None = self._read_docs()
        if html is None:
            return

        content_hash: str = self._hash_content(content=html)
        existing_meta: dict[str, Any] = self._collection.metadata or {}
        if existing_meta.get("content_hash") == content_hash and self._collection.count() > 0:
            return

        chunks: list[dict[str, Any]] = self._parse(html=html)
        if not chunks:
            logger.warning("No documentation sections were parsed from %s", DOCS_PATH)
            return

        # Recreate the collection so stale sections are removed cleanly
        try:
            self._client.delete_collection(name=COLLECTION_NAME)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"content_hash": content_hash},
        )

        self._collection.add(
            ids=[c["id"] for c in chunks],
            documents=[c["document"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
        )
        logger.info("Indexed %d documentation sections", len(chunks))

    def start_periodic_sync(self) -> None:
        """
        This function performs the initial documentation index and then periodically
        rebuilds it when the bundled source changes, all on a background thread so
        that startup is never blocked. It is a no-op if already started.
        """
        if self._sync_started:
            return
        self._sync_started = True

        def _sync_loop() -> None:
            try:
                self._reindex_if_changed()
                logger.info("Docs index ready (sections=%d)", self._collection.count())
            except Exception as e:
                logger.error("Initial docs index error: %s", e, exc_info=True)

            while True:
                time.sleep(SYNC_INTERVAL)
                try:
                    self._reindex_if_changed()
                except Exception as e:
                    logger.error("Docs sync error: %s", e, exc_info=True)

        thread: threading.Thread = threading.Thread(target=_sync_loop, daemon=True, name="docs-sync")
        thread.start()

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        This function searches the documentation index and returns matching sections.
        """
        total: int = self._collection.count()
        if total == 0:
            return []

        n: int = min(n_results, total)
        results: dict[str, Any] = self._collection.query(
            query_texts=[query],
            n_results=n,
        )

        matches: list[dict[str, Any]] = []
        for i in range(len(results["ids"][0])):
            metadata: dict[str, Any] = results["metadatas"][0][i]
            matches.append({
                "page_title": metadata.get("page_title", ""),
                "section_title": metadata.get("section_title", ""),
                "anchor": metadata.get("anchor", ""),
                "snippet": results["documents"][0][i],
                "distance": results["distances"][0][i],
            })

        return matches
