#!/usr/bin/python3

import hashlib
import logging
import os
import threading
import time
from typing import Any

import chromadb

from src.config import CONFIG_PATH
from src.memory_index import LocalEmbeddingFunction

logger: logging.Logger = logging.getLogger(__name__)

# Periodic sync interval in seconds (matches the memory and docs indexes)
SYNC_INTERVAL: int = 300

# Skill management tool file, gated behind the skills feature
SKILL_TOOL_FILES: set[str] = {"skill_manage_tool.py"}

# Frontmatter fence used in SKILL.md files
FRONTMATTER_FENCE: str = "---"


def parse_skill(content: str) -> tuple[str, str, str]:
    """
    This function parses a SKILL.md file into (name, description, body). The file
    starts with a simple frontmatter block delimited by --- fences containing
    `name:` and `description:` keys, followed by the markdown instructions body.
    Returns empty strings for any part that cannot be found.
    """
    name: str = ""
    description: str = ""
    body: str = content.strip()

    stripped: str = content.lstrip()
    if not stripped.startswith(FRONTMATTER_FENCE):
        return name, description, body

    # Split off the frontmatter block between the first two fences
    rest: str = stripped[len(FRONTMATTER_FENCE):]
    end: int = rest.find("\n" + FRONTMATTER_FENCE)
    if end == -1:
        return name, description, body

    frontmatter: str = rest[:end]
    body = rest[end + len("\n" + FRONTMATTER_FENCE):].lstrip("\n").strip()

    for line in frontmatter.splitlines():
        line = line.strip()
        if line.lower().startswith("name:"):
            name = line[len("name:"):].strip()
        elif line.lower().startswith("description:"):
            description = line[len("description:"):].strip()

    return name, description, body


class SkillsIndex:

    _instances: dict[str, "SkillsIndex"] = {}

    @classmethod
    def get_instance(cls, workspace_dir: str, collection_name: str = "skills") -> "SkillsIndex":
        """
        This function returns the SkillsIndex instance for a given workspace directory.
        Each agent gets its own isolated skills store.
        """
        if workspace_dir not in cls._instances:
            cls._instances[workspace_dir] = cls(workspace_dir=workspace_dir, collection_name=collection_name)
        return cls._instances[workspace_dir]

    def __init__(self, workspace_dir: str, collection_name: str = "skills") -> None:
        """
        This is the SkillsIndex class which manages vector search over an agent's
        skill files (workspace/skills/<name>/SKILL.md) so relevant skills can be
        surfaced for the current task.
        """
        # Model cache directory (inside mounted data volume)
        data_dir: str = os.path.dirname(CONFIG_PATH)
        model_dir: str = os.path.join(data_dir, "models")

        # Local embedding function (singleton — shared with the memory index)
        self._embedding_fn: LocalEmbeddingFunction = LocalEmbeddingFunction.get_instance(
            model_dir=model_dir
        )

        # ChromaDB persistent client — sub-agents get a subdirectory
        if collection_name == "skills":
            persist_dir: str = os.path.join(data_dir, "skills_index")
        else:
            persist_dir: str = os.path.join(data_dir, "skills_index", collection_name)
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

        # Skills directory
        self._skills_dir: str = os.path.join(workspace_dir, "skills")

        # Content hashes to detect changes (skill name -> md5 hex)
        self._hashes: dict[str, str] = {}

        # Full reindex on startup
        self._reindex_all()
        logger.info("Skills index ready (collection=%s, skills=%d)", collection_name, self._collection.count())

    @property
    def skills_dir(self) -> str:
        """
        This function returns the absolute path to the agent's skills directory.
        """
        return self._skills_dir

    def skill_path(self, name: str) -> str:
        """
        This function returns the absolute path to a skill's SKILL.md file.
        """
        return os.path.join(self._skills_dir, name, "SKILL.md")

    @staticmethod
    def _hash_content(content: str) -> str:
        """
        This function returns the MD5 hex digest of a string.
        """
        return hashlib.md5(content.encode()).hexdigest()

    def _iter_skill_files(self) -> list[tuple[str, str]]:
        """
        This function returns (skill_name, skill_md_path) pairs for every skill
        directory that contains a SKILL.md file.
        """
        if not os.path.isdir(s=self._skills_dir):
            return []

        pairs: list[tuple[str, str]] = []
        for entry in sorted(os.listdir(self._skills_dir)):
            skill_md: str = os.path.join(self._skills_dir, entry, "SKILL.md")
            if os.path.isfile(skill_md):
                pairs.append((entry, skill_md))
        return pairs

    def _reindex_all(self) -> None:
        """
        This function reindexes all skill files on startup.
        """
        for name, path in self._iter_skill_files():
            with open(file=path, mode="r", encoding="utf-8") as f:
                content: str = f.read()
            if content.strip():
                self._hashes[name] = self._hash_content(content=content)
                self._index_skill_content(name=name, content=content)

    def sync_changed(self) -> None:
        """
        This function checks for new, modified, or removed skill files and updates
        the index accordingly. It catches hand-edited skills that bypass the tool.
        """
        present: set[str] = set()
        for name, path in self._iter_skill_files():
            present.add(name)
            with open(file=path, mode="r", encoding="utf-8") as f:
                content: str = f.read()
            if not content.strip():
                continue
            content_hash: str = self._hash_content(content=content)
            if self._hashes.get(name) == content_hash:
                continue
            self._hashes[name] = content_hash
            self._index_skill_content(name=name, content=content)

        # Drop skills whose directories no longer exist
        for name in list(self._hashes.keys()):
            if name not in present:
                self._hashes.pop(name, None)
                try:
                    self._collection.delete(ids=[name])
                except Exception:
                    pass

    def _index_skill_content(self, name: str, content: str) -> None:
        """
        This function indexes or updates a skill from its SKILL.md content.
        """
        _, description, _ = parse_skill(content=content)
        self._upsert(name=name, description=description)

    def _upsert(self, name: str, description: str) -> None:
        """
        This function writes a skill record into the vector store. The embedded text
        combines the skill name and description so retrieval matches on intent.
        """
        document: str = f"{name}\n{description}".strip()
        self._collection.upsert(
            ids=[name],
            documents=[document],
            metadatas=[{"name": name, "description": description}]
        )

    def upsert_skill(self, name: str, description: str) -> None:
        """
        This function immediately (re)indexes a single skill, used by the skill_manage
        tool after a create/edit/patch so the change is searchable right away.
        """
        self._hashes.pop(name, None)
        self._upsert(name=name, description=description)
        # Refresh the stored hash so the periodic sync does not redo this work
        path: str = self.skill_path(name=name)
        if os.path.isfile(path):
            with open(file=path, mode="r", encoding="utf-8") as f:
                self._hashes[name] = self._hash_content(content=f.read())

    def remove_skill(self, name: str) -> None:
        """
        This function removes a skill from the vector store.
        """
        self._hashes.pop(name, None)
        try:
            self._collection.delete(ids=[name])
        except Exception:
            pass

    def start_periodic_sync(self) -> None:
        """
        This function starts a background thread that periodically syncs changed skills.
        """
        def _sync_loop() -> None:
            while True:
                time.sleep(SYNC_INTERVAL)
                try:
                    self.sync_changed()
                except Exception as e:
                    logger.error("Skills sync error: %s", e, exc_info=True)

        thread: threading.Thread = threading.Thread(target=_sync_loop, daemon=True)
        thread.start()

    def search(self, query: str, n_results: int = 2, max_distance: float | None = None) -> list[dict[str, Any]]:
        """
        This function searches the skills index and returns matching skills, optionally
        filtered to those within max_distance (lower distance = more relevant).
        """
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
            distance: float = results["distances"][0][i]
            if max_distance is not None and distance > max_distance:
                continue
            metadata: dict[str, Any] = results["metadatas"][0][i]
            matches.append({
                "name": metadata.get("name", results["ids"][0][i]),
                "description": metadata.get("description", ""),
                "distance": distance,
            })

        return matches

    def list_skills(self) -> list[dict[str, str]]:
        """
        This function returns every skill on disk with its name and description.
        """
        skills: list[dict[str, str]] = []
        for name, path in self._iter_skill_files():
            with open(file=path, mode="r", encoding="utf-8") as f:
                content: str = f.read()
            _, description, _ = parse_skill(content=content)
            skills.append({"name": name, "description": description})
        return skills

    def get_skill(self, name: str) -> dict[str, Any] | None:
        """
        This function returns a skill's full content and any bundled reference files,
        or None if the skill does not exist.
        """
        path: str = self.skill_path(name=name)
        if not os.path.isfile(path):
            return None

        with open(file=path, mode="r", encoding="utf-8") as f:
            content: str = f.read()
        _, description, body = parse_skill(content=content)

        # List bundled reference files alongside SKILL.md
        skill_dir: str = os.path.join(self._skills_dir, name)
        references: list[str] = []
        for root, _, files in os.walk(skill_dir):
            for filename in sorted(files):
                if filename == "SKILL.md":
                    continue
                full: str = os.path.join(root, filename)
                references.append(os.path.relpath(full, self._skills_dir))

        return {
            "name": name,
            "description": description,
            "body": body,
            "references": references,
        }
