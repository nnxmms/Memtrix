#!/usr/bin/python3

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

# Prompt-injection classifier ids on the HuggingFace Hub, keyed by the short name
# used in config. The default model is ProtectAI's DeBERTa-v3 detector, which is
# openly licensed (no HuggingFace token or gated-model acceptance required).
MODEL_IDS: dict[str, str] = {
    "deberta": "protectai/deberta-v3-base-prompt-injection-v2",
}

# Default model when the configured one is unknown.
DEFAULT_MODEL: str = "deberta"

# Cap classifier CPU parallelism so screening cannot peg every core and starve the
# asyncio event loop or the agent's handler thread. Override with MEMTRIX_GUARD_THREADS.
_guard_override: str = os.environ.get("MEMTRIX_GUARD_THREADS", "").strip()
GUARD_THREADS: int = (
    int(_guard_override)
    if _guard_override.isdigit() and int(_guard_override) > 0
    else max(1, (os.cpu_count() or 2) - 1)
)

# Characters per screening window. The classifier has a 512-token context;
# ~2000 characters is a safe upper bound that the tokenizer truncates to fit.
WINDOW_CHARS: int = 2000

# Step between successive windows (slight overlap so an injection straddling a
# window boundary is still seen whole in at least one window).
WINDOW_STEP: int = 1800


@dataclass
class ScanResult:
    """Outcome of screening a piece of text for prompt injection."""
    flagged: bool
    score: float


class PromptGuard:

    _instance: "PromptGuard | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls, model_dir: str, config: dict[str, Any]) -> "PromptGuard":
        """
        This function returns the singleton PromptGuard instance. The underlying
        classifier is loaded lazily on first use (see warm_up / _ensure_model), so
        obtaining the instance never blocks startup.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(model_dir=model_dir, config=config)
        return cls._instance

    def __init__(self, model_dir: str, config: dict[str, Any]) -> None:
        """
        This is the PromptGuard which screens untrusted text for prompt-injection and
        jailbreak attempts using a local sequence-classification model. Construction
        is intentionally cheap: the model is loaded lazily on the first scan (or via
        warm_up) rather than here, so creating the instance never blocks startup.
        """
        # Redirect HuggingFace caches to the writable data volume before the model is
        # ever loaded — same convention as the local embedding model.
        os.environ.setdefault("HF_HOME", model_dir)
        os.environ.setdefault("TRANSFORMERS_CACHE", model_dir)

        # Accept either a friendly short name (looked up in MODEL_IDS) or a full
        # HuggingFace repo id (anything containing a '/').
        name: str = str(config.get("model", DEFAULT_MODEL))
        self._model_id: str = name if "/" in name else MODEL_IDS.get(name, MODEL_IDS[DEFAULT_MODEL])
        self._threshold: float = float(config.get("threshold", 0.5))
        self._max_chars: int = int(config.get("max_chars", 20000))

        self._model_dir: str = model_dir
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._model_lock: threading.Lock = threading.Lock()
        self._load_failed: bool = False

    def warm_up(self) -> bool:
        """
        This function loads the classifier if it is not loaded yet. It is safe to call
        from a background thread to pre-load the model off the startup path. Returns
        True when the model is ready, False when loading failed (screening then
        degrades according to the caller's fail-open/closed policy).
        """
        try:
            self._ensure_model()
            return True
        except Exception as e:
            logger.error("Prompt Guard warm-up failed: %s", e, exc_info=True)
            return False

    def _ensure_model(self) -> Any:
        """
        This function returns the loaded classifier, building it on first use under a
        lock so that exactly one load happens even under concurrent access. Once a load
        has failed (e.g. the gated model could not be downloaded), subsequent calls
        fail fast instead of repeatedly hitting the network.
        """
        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is not None:
                return self._model
            if self._load_failed:
                raise RuntimeError("Prompt Guard model previously failed to load")

            try:
                model_dir: str = self._model_dir

                # Use the local cache only when the model is already downloaded — this
                # avoids slow HuggingFace Hub network calls on every startup.
                model_cached: bool = any(
                    entry.startswith("models--")
                    and entry.replace("models--", "").replace("--", "/") == self._model_id
                    for entry in os.listdir(model_dir)
                    if os.path.isdir(os.path.join(model_dir, entry))
                ) if os.path.isdir(model_dir) else False

                if model_cached:
                    logger.info("Loading Prompt Guard model from local cache")

                # Bound intra-op parallelism so screening cannot peg every core.
                import torch
                torch.set_num_threads(GUARD_THREADS)

                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                tokenizer: Any = AutoTokenizer.from_pretrained(
                    self._model_id,
                    cache_dir=model_dir,
                    local_files_only=model_cached,
                )
                model: Any = AutoModelForSequenceClassification.from_pretrained(
                    self._model_id,
                    cache_dir=model_dir,
                    local_files_only=model_cached,
                )
                model.eval()
                logger.info("Prompt Guard model loaded (%s)", self._model_id)

                self._tokenizer = tokenizer
                self._model = model
                return self._model
            except Exception:
                # Remember the failure so the next call does not retry the (slow,
                # likely-still-failing) download on every untrusted tool result.
                self._load_failed = True
                raise

    def scan(self, text: str, threshold: float | None = None) -> ScanResult:
        """
        This function screens text for prompt injection, returning whether it crosses
        the malicious-probability threshold and the highest score observed. Long text
        is screened in overlapping windows (the model has a 512-token context) and the
        maximum window score is reported, so an injection anywhere in the content is
        caught. Raises when the classifier cannot be loaded — callers decide whether to
        fail open or closed.
        """
        if not text or not text.strip():
            return ScanResult(flagged=False, score=0.0)

        cutoff: float = self._threshold if threshold is None else threshold
        text = text[: self._max_chars]

        model: Any = self._ensure_model()
        import torch

        max_score: float = 0.0
        start: int = 0
        length: int = len(text)
        while start < length:
            chunk: str = text[start : start + WINDOW_CHARS]
            if chunk.strip():
                inputs: Any = self._tokenizer(
                    chunk,
                    return_tensors="pt",
                    truncation=True,
                    max_length=512,
                )
                with torch.no_grad():
                    logits: Any = model(**inputs).logits
                # Binary classifier: the last label is the malicious/injection class.
                score: float = float(torch.softmax(logits, dim=-1)[0][-1])
                if score > max_score:
                    max_score = score
                if max_score >= cutoff:
                    break
            if start + WINDOW_CHARS >= length:
                break
            start += WINDOW_STEP

        return ScanResult(flagged=max_score >= cutoff, score=max_score)
