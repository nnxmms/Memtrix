#!/usr/bin/python3

import os
import re
from typing import Any

from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user

# Skill name rules: lowercase letters, digits and hyphens; safe as a directory name
NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_NAME_LEN: int = 64
MAX_DESCRIPTION_LEN: int = 1024

# Supported actions
ACTIONS: set[str] = {"create", "view", "list", "edit", "patch", "delete"}


class SkillManageTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the SkillManageTool which lets the agent create, inspect, improve and
        remove its own skills. Skills are reusable task workflows stored as
        workspace/skills/<name>/SKILL.md and surfaced automatically when relevant.
        """
        self._workspace_dir: str = workspace_dir
        self._catalog: Any | None = None
        super().__init__(
            name="skill_manage",
            description=(
                "Create and manage your own skills — reusable task workflows you write for yourself so you "
                "handle similar tasks better next time. After completing a task that was non-trivial (took "
                "5+ tool calls, required recovering from errors, involved a user correction, or followed a "
                "non-obvious workflow), use action 'create' to capture the approach as concise, generalized "
                "steps. If an existing skill proved suboptimal, improve it with 'patch' or 'edit'. Actions: "
                "'create' (name, description, instructions), 'view' (name) loads a skill's full instructions, "
                "'list' shows all skills, 'edit' (name, optional description/instructions) replaces content, "
                "'patch' (name, old, new) makes a targeted edit, 'delete' (name) removes a skill."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": sorted(ACTIONS),
                        "description": "The operation to perform on a skill."
                    },
                    "name": {
                        "type": "string",
                        "description": "Skill name: lowercase letters, digits and hyphens (e.g. 'security-audit'). Required for all actions except 'list'."
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line description stating WHAT the skill does and WHEN to use it. Required for 'create'."
                    },
                    "instructions": {
                        "type": "string",
                        "description": "The skill body: concise, generalized step-by-step instructions in markdown. Required for 'create'."
                    },
                    "old": {
                        "type": "string",
                        "description": "For 'patch': the exact existing text to replace (must appear exactly once in the skill)."
                    },
                    "new": {
                        "type": "string",
                        "description": "For 'patch': the replacement text."
                    }
                },
                "required": ["action"]
            }
        )

    def set_skills_catalog(self, catalog: Any) -> None:
        """
        This function injects the SkillsCatalog dependency for this agent's workspace.
        """
        self._catalog = catalog

    def execute(self, **kwargs: Any) -> str:
        """
        This function dispatches a skill management action.
        """
        if self._catalog is None:
            return "Error: skills are not enabled."

        action: str = str(kwargs.get("action", "")).strip().lower()
        if action not in ACTIONS:
            return f"Error: unknown action '{action}'. Valid actions: {', '.join(sorted(ACTIONS))}."

        if action == "list":
            return self._list()
        if action == "create":
            return self._create(kwargs=kwargs)
        if action == "view":
            return self._view(kwargs=kwargs)
        if action == "edit":
            return self._edit(kwargs=kwargs)
        if action == "patch":
            return self._patch(kwargs=kwargs)
        if action == "delete":
            return self._delete(kwargs=kwargs)
        return f"Error: unknown action '{action}'."

    def _validate_name(self, name: str) -> str | None:
        """
        This function validates a skill name and returns an error message, or None.
        """
        if not name:
            return "Error: 'name' is required."
        if len(name) > MAX_NAME_LEN:
            return f"Error: 'name' must be at most {MAX_NAME_LEN} characters."
        if not NAME_PATTERN.match(name):
            return "Error: 'name' may only contain lowercase letters, digits and hyphens, and must start with a letter or digit."
        # Defense in depth against path traversal even though the pattern forbids it
        skill_dir: str = os.path.join(self._catalog.skills_dir, name)
        if not os.path.realpath(skill_dir).startswith(os.path.realpath(self._catalog.skills_dir) + os.sep):
            return "Error: invalid skill name."
        return None

    @staticmethod
    def _render(name: str, description: str, instructions: str) -> str:
        """
        This function renders a SKILL.md file from its parts.
        """
        return f"---\nname: {name}\ndescription: {description}\n---\n\n{instructions.strip()}\n"

    def _write(self, name: str, description: str, instructions: str) -> None:
        """
        This function writes a skill's SKILL.md to disk.
        """
        path: str = self._catalog.skill_path(name=name)
        os.makedirs(name=os.path.dirname(path), exist_ok=True)
        with open(file=path, mode="w", encoding="utf-8") as f:
            f.write(self._render(name=name, description=description, instructions=instructions))

    def _list(self) -> str:
        """
        This function lists all skills with their descriptions.
        """
        skills: list[dict[str, str]] = self._catalog.list_skills()
        if not skills:
            return "No skills yet. Use action 'create' to capture a reusable workflow."
        lines: list[str] = [f"Skills ({len(skills)}):"]
        for skill in skills:
            lines.append(f"- {skill['name']}: {skill['description']}")
        return "\n".join(lines)

    def _create(self, kwargs: dict[str, Any]) -> str:
        """
        This function creates a new skill.
        """
        name: str = str(kwargs.get("name", "")).strip()
        description: str = str(kwargs.get("description", "")).strip()
        instructions: str = str(kwargs.get("instructions", "")).strip()

        name_error: str | None = self._validate_name(name=name)
        if name_error:
            return name_error
        if not description:
            return "Error: 'description' is required for create."
        if len(description) > MAX_DESCRIPTION_LEN:
            return f"Error: 'description' must be at most {MAX_DESCRIPTION_LEN} characters."
        if not instructions:
            return "Error: 'instructions' is required for create."

        # Confirm overwrite of an existing skill
        if os.path.isfile(self._catalog.skill_path(name=name)):
            if not confirm_with_user(kwargs, message=f"⚠️ Memtrix wants to overwrite an existing skill '{name}'. Allow this? (yes/no)"):
                return "Skill overwrite denied by user."

        self._write(name=name, description=description, instructions=instructions)
        return f"Created skill '{name}'."

    def _view(self, kwargs: dict[str, Any]) -> str:
        """
        This function returns a skill's full instructions and bundled reference files.
        """
        name: str = str(kwargs.get("name", "")).strip()
        if not name:
            return "Error: 'name' is required for view."

        skill: dict[str, Any] | None = self._catalog.get_skill(name=name)
        if skill is None:
            return f"Error: skill '{name}' not found."

        lines: list[str] = [
            f"Skill: {skill['name']}",
            f"Description: {skill['description']}",
            "",
            skill["body"],
        ]
        if skill["references"]:
            lines.append("")
            lines.append("Bundled reference files (read with read_file):")
            for ref in skill["references"]:
                lines.append(f"- {ref}")
        return "\n".join(lines)

    def _edit(self, kwargs: dict[str, Any]) -> str:
        """
        This function replaces a skill's description and/or instructions.
        """
        name: str = str(kwargs.get("name", "")).strip()
        if not name:
            return "Error: 'name' is required for edit."

        skill: dict[str, Any] | None = self._catalog.get_skill(name=name)
        if skill is None:
            return f"Error: skill '{name}' not found."

        description: str = str(kwargs.get("description", skill["description"])).strip() or skill["description"]
        instructions: str = str(kwargs.get("instructions", skill["body"])).strip() or skill["body"]
        if len(description) > MAX_DESCRIPTION_LEN:
            return f"Error: 'description' must be at most {MAX_DESCRIPTION_LEN} characters."

        self._write(name=name, description=description, instructions=instructions)
        return f"Updated skill '{name}'."

    def _patch(self, kwargs: dict[str, Any]) -> str:
        """
        This function makes a targeted replacement within a skill's instructions.
        """
        name: str = str(kwargs.get("name", "")).strip()
        old: str = str(kwargs.get("old", ""))
        new: str = str(kwargs.get("new", ""))
        if not name:
            return "Error: 'name' is required for patch."
        if not old:
            return "Error: 'old' is required for patch."

        skill: dict[str, Any] | None = self._catalog.get_skill(name=name)
        if skill is None:
            return f"Error: skill '{name}' not found."

        body: str = skill["body"]
        occurrences: int = body.count(old)
        if occurrences == 0:
            return "Error: 'old' text was not found in the skill."
        if occurrences > 1:
            return f"Error: 'old' text appears {occurrences} times; make it more specific so it matches exactly once."

        self._write(name=name, description=skill["description"], instructions=body.replace(old, new, 1))
        return f"Patched skill '{name}'."

    def _delete(self, kwargs: dict[str, Any]) -> str:
        """
        This function permanently deletes a skill.
        """
        name: str = str(kwargs.get("name", "")).strip()
        if not name:
            return "Error: 'name' is required for delete."

        path: str = self._catalog.skill_path(name=name)
        if not os.path.isfile(path):
            return f"Error: skill '{name}' not found."

        if not confirm_with_user(kwargs, message=f"⚠️ Memtrix wants to permanently delete the skill '{name}'. Allow this? (yes/no)"):
            return "Skill deletion denied by user."

        skill_dir: str = os.path.join(self._catalog.skills_dir, name)
        # Remove the SKILL.md and any bundled files, then the directory
        for root, dirs, files in os.walk(skill_dir, topdown=False):
            for filename in files:
                os.remove(os.path.join(root, filename))
            for dirname in dirs:
                os.rmdir(os.path.join(root, dirname))
        if os.path.isdir(skill_dir):
            os.rmdir(skill_dir)

        return f"Deleted skill '{name}'."
