#!/usr/bin/python3

import base64
import logging
import os
import re
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

# Image file extensions recognised for vision attachment.
IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# Maximum bytes for a single image before it is skipped (providers downscale on
# their side; this only guards against pathologically large uploads inflating the
# request and token cost).
MAX_IMAGE_BYTES: int = 10 * 1024 * 1024

# Maximum number of images carried into a single completion request. When more are
# present the oldest are dropped so the most recent context is preserved.
MAX_IMAGES: int = 4

# Directories whose files are treated as user-supplied media eligible for vision.
_MEDIA_DIRS: tuple[str, ...] = ("attachments", "downloads")

# Matches a workspace-relative media path (attachments/<file> or downloads/<file>)
# embedded anywhere in a message, e.g. "[File received: attachments/photo.png]".
_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"(?:attachments|downloads)/[^\s\"'\]\)]+",
)

# MIME type per extension for OpenAI-style data URLs.
_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def is_image(path: str) -> bool:
    """
    This function reports whether a path points to a supported image by extension.
    """
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def mime_for(path: str) -> str:
    """
    This function returns the MIME type for an image path, defaulting to PNG.
    """
    return _MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "image/png")


def encode_b64(filepath: str) -> str | None:
    """
    This function reads an image file and returns its base64-encoded contents, or
    None when the file is missing, unreadable, or exceeds the size cap.
    """
    try:
        if os.path.getsize(filepath) > MAX_IMAGE_BYTES:
            logger.warning("Skipping oversized image (> %d bytes): %s", MAX_IMAGE_BYTES, filepath)
            return None
        with open(file=filepath, mode="rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except OSError as e:
        logger.warning("Could not read image '%s': %s", filepath, e)
        return None


def extract_attachment_images(text: str, workspace_dir: str) -> list[str]:
    """
    This function scans a message for workspace-relative media paths (attachments/ or
    downloads/) that point to existing image files, returning their relative paths.
    The result is de-duplicated, ordered by first appearance, and capped at MAX_IMAGES
    (keeping the most recent references).
    """
    found: list[str] = []
    seen: set[str] = set()
    for match in _PATH_PATTERN.findall(text or ""):
        relpath: str = match
        if relpath in seen or not is_image(relpath):
            continue
        filepath: str = os.path.join(workspace_dir, relpath)
        # Guard against path traversal escaping the workspace.
        if not os.path.realpath(filepath).startswith(os.path.realpath(workspace_dir)):
            continue
        if not os.path.isfile(filepath):
            continue
        seen.add(relpath)
        found.append(relpath)

    if len(found) > MAX_IMAGES:
        found = found[-MAX_IMAGES:]
    return found


def _collect_image_paths(history: list[dict]) -> list[str]:
    """
    This function gathers the image paths referenced across the history (via each
    message's "images" key), de-duplicated and capped at MAX_IMAGES keeping the most
    recent references.
    """
    paths: list[str] = []
    for msg in history:
        for rel in msg.get("images", []) or []:
            if rel not in paths:
                paths.append(rel)
    if len(paths) > MAX_IMAGES:
        paths = paths[-MAX_IMAGES:]
    return paths


def expand_image_messages(history: list[dict], workspace_dir: str, style: str) -> list[dict]:
    """
    This function rewrites user messages that carry an "images" key (workspace-relative
    image paths) into the multimodal format expected by the target provider, encoding
    the image bytes at send time. Messages without images pass through unchanged and the
    transient "images" key is always stripped from the outgoing payload.

    style="ollama": keep content as a string and attach a native images=[base64...] key.
    style="openai": replace content with a [{type:text}, {type:image_url}, ...] list using
    data: URLs.

    A global cap of MAX_IMAGES is enforced across the whole history so older images are
    dropped rather than the request growing unbounded.
    """
    keep: set[str] = set(_collect_image_paths(history))
    cache: dict[str, str | None] = {}

    def _b64(rel: str) -> str | None:
        if rel not in cache:
            cache[rel] = encode_b64(os.path.join(workspace_dir, rel))
        return cache[rel]

    expanded: list[dict] = []
    for msg in history:
        rels: list[str] = msg.get("images") or []
        if not rels:
            expanded.append(msg)
            continue

        # Resolve only the retained, successfully encoded images for this message.
        encoded: list[tuple[str, str]] = []
        for rel in rels:
            if rel not in keep:
                continue
            data: str | None = _b64(rel)
            if data is not None:
                encoded.append((rel, data))

        out: dict[str, Any] = {k: v for k, v in msg.items() if k != "images"}
        if not encoded:
            expanded.append(out)
            continue

        if style == "ollama":
            out["images"] = [data for _, data in encoded]
        else:
            text: str = out.get("content") or ""
            content: list[dict[str, Any]] = []
            if text:
                content.append({"type": "text", "text": text})
            for rel, data in encoded:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_for(rel)};base64,{data}"},
                })
            out["content"] = content
        expanded.append(out)

    return expanded
