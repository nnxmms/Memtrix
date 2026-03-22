#!/usr/bin/python3

import os
import re
import requests
from typing import Any

from src.tools.base import BaseTool
from src.tools.utils import confirm_with_user, validate_url_not_internal

# Maximum file size: 50 MB
MAX_FILE_SIZE: int = 50 * 1024 * 1024

# Downloads directory name
DOWNLOADS_DIR: str = "downloads"

# Only allow http/https URLs with a clean path
_URL_PATTERN: re.Pattern[str] = re.compile(
    r"^https?://[a-zA-Z0-9._\-]+(?::[0-9]+)?/[a-zA-Z0-9._\-/%+~@:]+$"
)


class DownloadFileTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        """
        This is the DownloadFileTool which downloads a file from a URL into the workspace.
        """
        self._workspace_dir: str = workspace_dir
        self._downloads_dir: str = os.path.join(workspace_dir, DOWNLOADS_DIR)
        super().__init__(
            name="download_file",
            description=(
                "Download a file from a URL and save it to the downloads/ directory in the workspace. "
                "Supports any file type (PDF, YAML, images, etc.). Only HTTPS/HTTP URLs are allowed. "
                "Maximum file size: 50 MB."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the file to download, e.g. https://example.com/report.pdf"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename to save as. Defaults to the filename from the URL."
                    }
                },
                "required": ["url"]
            }
        )

    def execute(self, **kwargs: Any) -> str:
        """
        This function downloads a file from a URL and saves it to the downloads/ directory.
        """
        url: str = kwargs.get("url", "").strip()
        filename: str = kwargs.get("filename", "").strip()

        if not url:
            return "Error: url cannot be empty."

        # Only allow http/https
        if not url.startswith(("http://", "https://")):
            return "Error: only http:// and https:// URLs are supported."

        # Validate URL format
        if not _URL_PATTERN.match(url):
            return "Error: invalid URL format."

        # Block internal/private network addresses (SSRF protection)
        ssrf_error: str | None = validate_url_not_internal(url)
        if ssrf_error:
            return ssrf_error

        # Derive filename from URL if not provided
        if not filename:
            filename: str = url.rstrip("/").rsplit(sep="/", maxsplit=1)[-1]
            # Strip query parameters if present
            if "?" in filename:
                filename: str = filename.split(sep="?")[0]
            if not filename:
                return "Error: could not determine filename from URL. Please provide a filename."

        # Sanitize filename — basename only, no path traversal
        filename: str = os.path.basename(filename)
        if not filename:
            return "Error: invalid filename."

        # Ensure downloads directory exists
        os.makedirs(name=self._downloads_dir, exist_ok=True)

        filepath: str = os.path.join(self._downloads_dir, filename)

        # Prevent path traversal (defense in depth after basename)
        if not os.path.realpath(filepath).startswith(os.path.realpath(self._downloads_dir)):
            return "Error: path must be within the downloads directory."

        if os.path.exists(path=filepath):
            return f"Error: '{filename}' already exists in downloads/. Use a different filename."

        # Human-in-the-loop: ask user for download confirmation
        if not confirm_with_user(kwargs, message=f"⚠️ Memtrix wants to download a file:\n\n  URL: {url}\n  Save as: downloads/{filename}\n\nAllow this download? (yes/no)"):
            return "Download denied by user."

        try:
            response: requests.Response = requests.get(
                url=url,
                timeout=60,
                stream=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                },
            )
            response.raise_for_status()
        except requests.RequestException as e:
            return f"Error: failed to download — {e}"

        # Check content length if available
        content_length: str | None = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            return f"Error: file too large ({int(content_length)} bytes). Maximum is {MAX_FILE_SIZE} bytes."

        # Stream download with size check
        downloaded: int = 0
        with open(file=filepath, mode="wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                downloaded += len(chunk)
                if downloaded > MAX_FILE_SIZE:
                    f.close()
                    os.remove(path=filepath)
                    return f"Error: file too large (exceeded {MAX_FILE_SIZE} bytes). Download aborted."
                f.write(chunk)

        return f"Downloaded {filename} ({downloaded} bytes) to downloads/{filename}"
