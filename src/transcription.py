#!/usr/bin/python3

import logging
import threading
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)


class LocalSpeechToText:
    """
    Local speech-to-text wrapper with lazy model initialization.
    """

    def __init__(self, model_name: str = "base") -> None:
        self._model_name: str = model_name
        self._model: Any = None
        self._lock: threading.Lock = threading.Lock()

    def _ensure_model(self) -> Any:
        """
        Load the local faster-whisper model once, on first use.
        """
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except Exception as exc:
                raise RuntimeError(
                    "Local STT requires faster-whisper. Install dependencies and restart."
                ) from exc

            logger.info("Loading local STT model '%s'", self._model_name)
            self._model = WhisperModel(self._model_name)
            logger.info("Local STT model ready (%s)", self._model_name)
            return self._model

    def transcribe(self, file_path: str, language: str | None = None) -> dict[str, Any]:
        """
        Transcribe one local audio file and return a structured result.
        """
        try:
            model: Any = self._ensure_model()
            segments, info = model.transcribe(
                audio=file_path,
                task="transcribe",
                language=language or None,
                vad_filter=True,
            )
            text: str = " ".join((segment.text or "").strip() for segment in segments).strip()
            detected_language: str = str(getattr(info, "language", "") or "")
            return {
                "ok": bool(text),
                "text": text,
                "language": detected_language,
                "backend": "local",
                "error": "" if text else "No speech detected.",
            }
        except Exception as exc:
            logger.warning("Local STT transcription failed: %s", exc)
            return {
                "ok": False,
                "text": "",
                "language": "",
                "backend": "local",
                "error": str(exc),
            }
