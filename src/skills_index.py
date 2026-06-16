#!/usr/bin/python3

import logging
import os

logger: logging.Logger = logging.getLogger(__name__)

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


class SkillsCatalog:

    _instances: dict[str, "SkillsCatalog"] = {}

    @classmethod
    def get_instance(cls, workspace_dir: str) -> "SkillsCatalog":
        """
        This function returns the SkillsCatalog instance for a given workspace directory.
        Each agent gets its own isolated set of skills.
        """
        if workspace_dir not in cls._instances:
            cls._instances[workspace_dir] = cls(workspace_dir=workspace_dir)
        return cls._instances[workspace_dir]

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SkillsCatalog which reads an agent's skill files
        (workspace/skills/<name>/SKILL.md) directly from disk. Following the Agent
        Skills progressive-disclosure model, the agent always sees every skill's
        name and description (discovery) and loads a skill's full instructions on
        demand (activation) — there is no embedding or semantic search involved.
        """
        self._skills_dir: str = os.path.join(workspace_dir, "skills")
        logger.info("Skills catalog ready (skills=%d)", len(self._iter_skill_files()))

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

    def list_skills(self) -> list[dict[str, str]]:
        """
        This function returns every skill on disk with its name and description.
        The name falls back to the directory name when the frontmatter omits it.
        """
        skills: list[dict[str, str]] = []
        for dir_name, path in self._iter_skill_files():
            try:
                with open(file=path, mode="r", encoding="utf-8") as f:
                    content: str = f.read()
            except OSError as e:
                logger.warning("Could not read skill '%s': %s", dir_name, e)
                continue
            _, description, _ = parse_skill(content=content)
            skills.append({"name": dir_name, "description": description})
        return skills

    def get_skill(self, name: str) -> dict[str, object] | None:
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

