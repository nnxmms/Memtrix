#!/usr/bin/python3

from typing import Any

from src.tools.base import BaseTool


class ReactTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the ReactTool which reacts to the user's message with an emoji.
        """
        super().__init__(
            name="react_to_message",
            description=(
                "React to the user's message with an emoji. "
                "Use this to acknowledge, agree, express emotion, or add a quick non-verbal response. "
                "Only works on Matrix — not available on CLI or internal channels."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "emoji": {
                        "type": "string",
                        "description": "A single emoji to react with, e.g. '👍', '❤️', '😂', '🔥', '👀'."
                    }
                },
                "required": ["emoji"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function sends an emoji reaction to the current user message.
        """
        react = kwargs.get("_react")
        emoji: str = kwargs.get("emoji", "").strip()

        if not emoji:
            return "Error: emoji cannot be empty."

        if not react:
            return "Reactions are not available on this channel."

        try:
            react(emoji)
        except Exception as e:
            return f"Error: failed to react — {e}"

        return f"Reacted with {emoji}"
