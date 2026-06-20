#!/usr/bin/python3

from typing import Any

from src.indexing.docs import DocsIndex
from src.providers.base import BaseProvider
from src.tools.base import BaseTool


class AskDocsTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the AskDocsTool which answers natural-language questions about
        Memtrix by synthesizing an answer grounded in its own documentation.
        """
        self._workspace_dir: str = workspace_dir
        self._index: DocsIndex | None = None
        self._provider: BaseProvider | None = None
        self._model: str = ""
        super().__init__(
            name="ask_docs",
            description="Ask a natural-language question about how Memtrix works and get a synthesized answer grounded in its own documentation, with sources. Use for questions like 'how do I add a sub-agent?' or 'what does recall_mode do?'. Use search_docs instead when you only need raw excerpts.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to answer from the documentation, e.g. 'How does the reasoning-memory deriver work?'."
                    }
                },
                "required": ["question"]
            }
        )

    def set_docs_index(self, index: DocsIndex) -> None:
        """
        This function injects the documentation index dependency.
        """
        self._index = index

    def set_dialectic(self, provider: BaseProvider, model: str) -> None:
        """
        This function injects the LLM provider and model used to synthesize answers.
        """
        self._provider = provider
        self._model = model

    def execute(self, **kwargs: Any) -> str:
        """
        This function retrieves relevant documentation and synthesizes a direct answer.
        """
        if self._provider is None:
            return "Docs question answering is not available."

        question: str = kwargs.get("question", "")
        if not question:
            return "Error: question cannot be empty."

        index: DocsIndex = self._index or DocsIndex.get_instance()
        matches: list[dict[str, Any]] = index.search(query=question, n_results=8)

        if not matches:
            return "The documentation does not cover that yet."

        evidence: str = "\n\n".join(
            f"[{m['section_title'] or m['page_title']}] ({m['anchor']})\n{m['snippet']}"
            for m in matches
        )
        system_prompt: str = (
            "You answer a question about the Memtrix system using only the provided "
            "documentation excerpts. Be direct and concise. Cite the relevant section "
            "anchors you used. If the documentation does not support an answer, say so "
            "plainly. Do not invent facts."
        )
        user_prompt: str = (
            f"Documentation excerpts:\n{evidence}\n\n"
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
            return f"Error answering from documentation: {e}"

        return answer or "I couldn't form an answer from the documentation."
