#!/usr/bin/python3

from typing import Any

from src.providers.base import BaseProvider
from src.memory.store import RepresentationStore
from src.tools.base import BaseTool


class MemoryContextTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the MemoryContextTool which answers natural-language questions about
        the user or the agent by synthesizing an answer from reasoned memory.
        """
        self._workspace_dir: str = workspace_dir
        self._store: RepresentationStore | None = None
        self._provider: BaseProvider | None = None
        self._model: str = ""
        super().__init__(
            name="memory_context",
            description="Ask a natural-language question about the user and get a synthesized answer grounded in your reasoned memory. Use for nuanced questions like 'what tone does the user prefer?'.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to answer from memory, e.g. 'What are the user's working hours?'."
                    }
                },
                "required": ["question"]
            }
        )

    def set_representation(self, store: RepresentationStore) -> None:
        """
        This function injects the representation store dependency.
        """
        self._store = store

    def set_dialectic(self, provider: BaseProvider, model: str) -> None:
        """
        This function injects the LLM provider and model used to synthesize answers.
        """
        self._provider = provider
        self._model = model

    def execute(self, **kwargs: Any) -> str:
        """
        This function retrieves relevant conclusions and synthesizes a direct answer.
        """
        if self._store is None or self._provider is None:
            return "Memory context is not available."

        question: str = kwargs.get("question", "")
        if not question:
            return "Error: question cannot be empty."

        matches: list[dict[str, Any]] = self._store.search(query=question, n_results=10)
        user_card: str = self._store.read_peer_card(peer="user")

        if not matches and not user_card:
            return "I don't have anything in memory that answers that yet."

        evidence: str = "\n".join(
            f"- ({m['kind']}, {m.get('confidence', 'medium')} confidence) {m['content']}"
            for m in matches
        )
        system_prompt: str = (
            "You answer a question using ONLY the provided memory about the user. Be direct "
            "and concise. Weigh higher-confidence evidence more heavily, "
            "and if items conflict, prefer the stronger one and note the uncertainty. If the "
            "memory does not support an answer, say so plainly. Never invent facts or infer "
            "beyond what the memory states."
        )
        user_prompt: str = (
            f"User profile:\n{user_card or '(none)'}\n\n"
            f"Relevant conclusions:\n{evidence or '(none)'}\n\n"
            f"Question: {question}"
        )

        try:
            message: Any = self._provider.completions(
                model=self._model,
                history=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                think=False,
            )
            answer: str = (message.content or "").strip()
        except Exception as e:
            return f"Error answering from memory: {e}"

        return answer or "I couldn't form an answer from memory."
