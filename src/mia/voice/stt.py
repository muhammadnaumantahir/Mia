"""Lazy, resource-aware speech-to-text adapters."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Protocol


class SpeechToTextProvider(Protocol):
    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "recording.webm",
        language: str | None = None,
    ) -> dict[str, str | float | None]:
        """Convert one uploaded audio clip into text and language metadata."""
        ...


class FasterWhisperTranscriber:
    """Local CPU-int8 speech recognition using Faster-Whisper.

    The model is loaded lazily on the first request. Model weights are stored
    under Mia's data directory, not inside the source repository.
    """

    def __init__(
        self,
        *,
        model_size: str | None = None,
        data_dir: str | Path = "./data",
        cpu_threads: int | None = None,
    ) -> None:
        self.model_size = model_size or os.getenv("MIA_STT_MODEL", "base")
        self.data_dir = Path(data_dir)
        self.cpu_threads = cpu_threads or max(
            1, min(2, os.cpu_count() or 1)
        )
        self._model = None
        self._model_lock = Lock()

    def _get_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        from faster_whisper import WhisperModel
                    except ImportError as exc:
                        raise RuntimeError(
                            'Install Mia with the "voice" extra to enable speech recognition.'
                        ) from exc

                    model_dir = self.data_dir / "models" / "faster-whisper"
                    model_dir.mkdir(parents=True, exist_ok=True)
                    self._model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self.cpu_threads,
                        num_workers=1,
                        download_root=str(model_dir),
                    )
        return self._model

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "recording.webm",
        language: str | None = None,
    ) -> dict[str, str | float | None]:
        if not audio_bytes:
            raise ValueError("Audio data is empty")
        # Keep a suffix so PyAV can infer the browser-recorded container.
        suffix = Path(filename).suffix.lower()
        if suffix not in {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}:
            suffix = ".webm"

        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=suffix,
            prefix="mia-voice-",
            delete=False,
        ) as audio_file:
            audio_file.write(audio_bytes)
            audio_path = Path(audio_file.name)

        try:
            model = self._get_model()
            segments, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            # Faster-Whisper yields lazily. Consume the generator before
            # deleting the temporary audio file.
            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()
            return {
                "text": text,
                "language": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
            }
        finally:
            audio_path.unlink(missing_ok=True)
