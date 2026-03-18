#!/usr/bin/python3

import requests
from typing import Any

from bs4 import BeautifulSoup

from src.tools.base import BaseTool

# Maximum characters to return from a fetched page
MAX_CONTENT_LENGTH: int = 4000


class FetchURLTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the FetchURLTool which fetches a web page and extracts its readable text content.
        """
        super().__init__(
            name="fetch_url",
            description="Fetch a web page and extract its readable text content. Use this when the user shares a link or when you need to read a specific page from search results.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch."
                    }
                },
                "required": ["url"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function fetches a URL and returns the extracted text content.
        """
        url: str = kwargs.get("url", "")
        if not url:
            return "Error: URL cannot be empty."

        # Only allow http/https
        if not url.startswith(("http://", "https://")):
            return "Error: only http:// and https:// URLs are supported."

        try:
            response: requests.Response = requests.get(
                url=url,
                timeout=15,
                headers={"User-Agent": "Memtrix/1.0"}
            )
            response.raise_for_status()
        except requests.RequestException as e:
            return f"Error: failed to fetch URL — {e}"

        # Parse HTML and extract text
        soup: BeautifulSoup = BeautifulSoup(markup=response.text, features="html.parser")

        # Remove script and style elements
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        # Extract text
        text: str = soup.get_text(separator="\n", strip=True)

        # Trim to max length
        if len(text) > MAX_CONTENT_LENGTH:
            text: Any | str = text[:MAX_CONTENT_LENGTH] + "\n\n[… content truncated]"

        return text if text else "No readable content found on this page."
