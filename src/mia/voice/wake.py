"""Offline wake-word core: detector contract, lifecycle and privacy guards.

This module is deliberately transport-free. It knows nothing about HTTP,
WebSockets or browsers; a transport (see docs/voice/WAKE_WORD_PLAN.md) feeds it
16 kHz mono signed 16-bit PCM and receives at most a :class:`WakeEvent`.

Privacy and safety properties enforced here (and covered by tests):

* **Off by default, explicit opt-in.** Nothing is constructed or loaded until
  :meth:`WakeWordSession.arm` is called with ``owner_opt_in=True``.
* **Immediate stop.** :meth:`WakeWordSession.stop` closes the detector, drops
  any buffered audio and makes every later frame a no-op, even if a late frame
  from the transport is still in flight.
* **No audio retention.** Audio is only held in a re-framing buffer that is
  smaller than one 80 ms frame, and that buffer is cleared on stop, on trigger
  and during cooldown. Events and status never contain audio.
* **Bounded listening.** An armed session disarms itself after
  ``max_armed_seconds`` so it cannot silently outlive the owner's attention.
* **False-activation control.** A score threshold, a consecutive-frame
  "patience", a cooldown after each activation and an activation-storm guard
  that disarms the session instead of waking Mia repeatedly.
* **Fail closed.** A detector error stops the session; a non-finite score is
  treated as "no wake word".

The optional :class:`OpenWakeWordDetector` adapter wraps the Apache-2.0
openWakeWord library. It never downloads models: the owner must supply the
model file explicitly (the upstream pre-trained models are CC BY-NC-SA 4.0).
"""

from __future__ import annotations

import importlib.util
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2
FRAME_SAMPLES = 1_280  # 80 ms, the frame size openWakeWord recommends
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH_BYTES
# A transport must send small chunks; this bounds memory and per-call CPU.
MAX_CHUNK_BYTES = 32 * FRAME_BYTES  # ~2.56 s of audio

MODEL_PATH_ENV = "MIA_WAKE_MODEL_PATH"
VAD_THRESHOLD_ENV = "MIA_WAKE_VAD_THRESHOLD"

INSTALL_HINT = (
    "Offline wake-word detection needs the openWakeWord engine. On Windows run "
    'python -m pip install -e ".[voice-wake]". On Linux with Python 3.13 the '
    "extra cannot resolve (upstream pins tflite-runtime, which has no Python 3.13 "
    "distribution); install onnxruntime, numpy, scipy, scikit-learn, tqdm and "
    "requests, then pip install --no-deps openwakeword."
)


class WakeWordError(RuntimeError):
    """Base class for wake-word failures."""


class WakeWordUnavailable(WakeWordError):
    """The engine, model or configuration needed to listen is missing."""


class WakeWordNotPermitted(PermissionError):
    """Listening was requested without explicit owner opt-in."""


class WakeWordDetector(Protocol):
    """Replaceable detector contract.

    ``score`` receives exactly one 80 ms frame of 16 kHz mono int16 little-endian
    PCM and returns a confidence in ``[0, 1]``. Implementations must not retain
    or persist audio.
    """

    name: str

    def score(self, frame: bytes) -> float: ...

    def reset(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class WakeWordConfig:
    label: str = "Hey Mia"  # display label only; the loaded model defines the phrase
    threshold: float = 0.5
    patience: int = 2  # consecutive frames at/above threshold (2 x 80 ms)
    cooldown_seconds: float = 2.0
    max_armed_seconds: float = 4 * 3600.0
    max_activations_per_minute: int = 5

    def __post_init__(self) -> None:
        if not (0.0 < self.threshold <= 1.0):
            raise ValueError("threshold must be in (0, 1]")
        if not (1 <= self.patience <= 25):
            raise ValueError("patience must be between 1 and 25 frames")
        if not (0.0 <= self.cooldown_seconds <= 60.0):
            raise ValueError("cooldown_seconds must be between 0 and 60")
        # No "unlimited" option: listening is always time-bounded.
        if not (1.0 <= self.max_armed_seconds <= 24 * 3600.0):
            raise ValueError("max_armed_seconds must be between 1 second and 24 hours")
        if not (1 <= self.max_activations_per_minute <= 60):
            raise ValueError("max_activations_per_minute must be between 1 and 60")
        if not self.label.strip() or len(self.label) > 64:
            raise ValueError("label must be 1-64 characters")


@dataclass(frozen=True)
class WakeEvent:
    """A wake-word activation. Deliberately carries no audio."""

    label: str
    score: float
    at: float


class WakeWordSession:
    """Opt-in, time-bounded, immediately stoppable wake-word listener."""

    OFF = "off"
    ARMED = "armed"
    STOPPED = "stopped"

    def __init__(
        self,
        detector_factory: Callable[[], WakeWordDetector],
        config: WakeWordConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = detector_factory
        self.config = config or WakeWordConfig()
        self._clock = clock
        self._lock = threading.RLock()
        self._state = self.OFF
        self._detector: WakeWordDetector | None = None
        self._detector_name: str | None = None
        self._buffer = bytearray()
        self._streak = 0
        self._armed_at = 0.0
        self._cooldown_until = 0.0
        self._recent: deque[float] = deque()
        self._activations = 0
        self._dropped_chunks = 0
        self._stop_reason: str | None = None
        self._stop_error: str | None = None

    # ------------------------------------------------------------------ control

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def arm(self, *, owner_opt_in: bool) -> dict[str, Any]:
        """Start listening. Requires an explicit, affirmative owner opt-in."""
        if owner_opt_in is not True:
            raise WakeWordNotPermitted(
                "Wake-word listening starts only after an explicit owner opt-in."
            )
        with self._lock:
            if self._state == self.ARMED:
                return self._status_locked()
            try:
                detector = self._factory()
            except WakeWordUnavailable:
                raise
            except Exception as exc:  # a broken engine must not look like a crash
                raise WakeWordUnavailable(f"Wake-word engine failed to start: {exc}") from exc
            self._detector = detector
            self._detector_name = getattr(detector, "name", type(detector).__name__)
            self._buffer.clear()
            self._streak = 0
            self._recent.clear()
            self._activations = 0
            self._dropped_chunks = 0
            self._stop_reason = None
            self._stop_error = None
            self._armed_at = self._clock()
            self._cooldown_until = 0.0
            self._state = self.ARMED
            return self._status_locked()

    def stop(self, reason: str = "owner") -> dict[str, Any]:
        """Stop immediately. Idempotent; safe to call from any thread."""
        with self._lock:
            self._stop_locked(reason)
            return self._status_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def expire_if_due(self) -> bool:
        """Disarm if the armed time limit has passed, even if no audio arrives.

        A transport calls this on a timer so the limit does not depend on the
        microphone still sending frames. Returns True if the session stopped.
        """
        with self._lock:
            if (
                self._state == self.ARMED
                and self._clock() - self._armed_at >= self.config.max_armed_seconds
            ):
                self._stop_locked("max_armed_duration")
                return True
            return False

    # --------------------------------------------------------------------- audio

    def feed(self, chunk: bytes) -> WakeEvent | None:
        """Score a chunk of PCM audio; return a :class:`WakeEvent` on activation.

        Frames received while not armed (including late frames after ``stop``)
        are dropped without being scored or buffered.
        """
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("audio chunk must be bytes-like")
        if len(chunk) > MAX_CHUNK_BYTES:
            raise ValueError(f"audio chunk exceeds {MAX_CHUNK_BYTES} bytes")
        if len(chunk) % SAMPLE_WIDTH_BYTES:
            raise ValueError("audio chunk must contain whole 16-bit samples")

        with self._lock:
            if self._state != self.ARMED or self._detector is None:
                self._dropped_chunks += 1
                return None
            now = self._clock()
            if now - self._armed_at >= self.config.max_armed_seconds:
                self._stop_locked("max_armed_duration")
                return None
            if now < self._cooldown_until:
                self._buffer.clear()
                return None

            self._buffer.extend(chunk)
            while len(self._buffer) >= FRAME_BYTES:
                frame = bytes(self._buffer[:FRAME_BYTES])
                del self._buffer[:FRAME_BYTES]
                try:
                    score = self._safe_score(self._detector.score(frame))
                except Exception as exc:
                    self._stop_locked("detector_error", error=str(exc))
                    return None
                if score >= self.config.threshold:
                    self._streak += 1
                else:
                    self._streak = 0
                if self._streak >= self.config.patience:
                    return self._activate_locked(score, now)
            return None

    # ----------------------------------------------------------------- internals

    @staticmethod
    def _safe_score(value: Any) -> float:
        score = float(value)
        if math.isnan(score):
            return 0.0  # fail closed: an unusable score is "no wake word"
        return min(1.0, max(0.0, score))

    def _activate_locked(self, score: float, now: float) -> WakeEvent | None:
        assert self._detector is not None
        self._buffer.clear()
        self._streak = 0
        try:
            self._detector.reset()  # forget the audio that produced this activation
        except Exception as exc:
            self._stop_locked("detector_error", error=str(exc))
            return None
        self._cooldown_until = now + self.config.cooldown_seconds
        self._recent.append(now)
        while self._recent and now - self._recent[0] > 60.0:
            self._recent.popleft()
        if len(self._recent) > self.config.max_activations_per_minute:
            # Likely a false-activation source (TV, music). Stop instead of
            # waking Mia over and over; the owner can re-arm deliberately.
            self._stop_locked("too_many_activations")
            return None
        self._activations += 1
        return WakeEvent(label=self.config.label, score=score, at=now)

    def _stop_locked(self, reason: str, *, error: str | None = None) -> None:
        if self._state == self.ARMED:
            detector, self._detector = self._detector, None
            self._state = self.STOPPED
            self._stop_reason = reason
            self._stop_error = error
            if detector is not None:
                try:
                    detector.reset()
                except Exception:
                    pass
                try:
                    detector.close()
                except Exception as exc:
                    self._stop_error = self._stop_error or f"close failed: {exc}"
        self._buffer.clear()
        self._streak = 0

    def _status_locked(self) -> dict[str, Any]:
        armed = self._state == self.ARMED
        now = self._clock()
        return {
            "state": self._state,
            # The single source of truth for the visible "listening" indicator.
            "listening": armed,
            "label": self.config.label,
            "detector": self._detector_name if armed else None,
            "armed_for_seconds": round(now - self._armed_at, 3) if armed else 0.0,
            "max_armed_seconds": self.config.max_armed_seconds,
            "in_cooldown": armed and now < self._cooldown_until,
            "threshold": self.config.threshold,
            "patience": self.config.patience,
            "activations": self._activations,
            "dropped_chunks": self._dropped_chunks,
            "stop_reason": self._stop_reason,
            "stop_error": self._stop_error,
            "audio_retained": False,
        }


# --------------------------------------------------------------------- adapter


class OpenWakeWordDetector:
    """Adapter around openWakeWord (Apache-2.0 code; see module docstring).

    The owner supplies the model file. This adapter never calls the library's
    model downloader, so no weights are fetched implicitly.
    """

    name = "openwakeword"

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        *,
        vad_threshold: float = 0.5,
        inference_framework: str = "onnx",
        model_factory: Callable[..., Any] | None = None,
        array_factory: Callable[[bytes], Any] | None = None,
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise WakeWordUnavailable(
                f"Wake-word model file not found: {path.name or str(path)}. "
                f"Set {MODEL_PATH_ENV} to a model you are licensed to use."
            )
        if not (0.0 <= vad_threshold <= 1.0):
            raise ValueError("vad_threshold must be in [0, 1]")
        if model_factory is None:
            try:
                from openwakeword.model import Model as model_factory  # type: ignore
            except ImportError as exc:
                raise WakeWordUnavailable(INSTALL_HINT) from exc
        self._array_factory = array_factory or _numpy_int16
        self._key = path.stem  # openWakeWord keys predictions by model file stem
        try:
            self._model = model_factory(
                wakeword_models=[str(path)],
                inference_framework=inference_framework,
                vad_threshold=vad_threshold,
            )
        except Exception as exc:
            raise WakeWordUnavailable(f"openWakeWord could not load the model: {exc}") from exc

    def score(self, frame: bytes) -> float:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"frame must be exactly {FRAME_BYTES} bytes")
        prediction = self._model.predict(self._array_factory(frame))
        if self._key in prediction:
            return float(prediction[self._key])
        return float(max(prediction.values(), default=0.0))

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()

    def close(self) -> None:
        self._model = None  # drop the model and its internal audio buffers


def _numpy_int16(frame: bytes) -> Any:
    import numpy

    return numpy.frombuffer(frame, dtype=numpy.int16)


def build_default_detector(environ: Mapping[str, str] | None = None) -> WakeWordDetector:
    """Detector factory used by the default session (reads owner configuration)."""
    env = os.environ if environ is None else environ
    model_path = env.get(MODEL_PATH_ENV, "").strip()
    if not model_path:
        raise WakeWordUnavailable(
            f"No wake-word model configured. Set {MODEL_PATH_ENV} to an openWakeWord "
            "model file (.onnx). Mia does not download wake-word models."
        )
    raw_vad = env.get(VAD_THRESHOLD_ENV, "").strip()
    try:
        vad = float(raw_vad) if raw_vad else 0.5
    except ValueError as exc:
        raise WakeWordUnavailable(f"{VAD_THRESHOLD_ENV} must be a number") from exc
    return OpenWakeWordDetector(model_path, vad_threshold=vad)


def offline_wake_word_status(
    environ: Mapping[str, str] | None = None,
    *,
    transport_available: bool = False,
) -> dict[str, Any]:
    """Truthful, side-effect-free capability report (never imports the engine).

    ``operational`` is True only when the engine, a model *and* an audio
    transport all exist. Registration of any one piece is not capability.
    """
    env = os.environ if environ is None else environ

    def has(module: str) -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    engine_installed = has("openwakeword") and has("onnxruntime")
    model_value = env.get(MODEL_PATH_ENV, "").strip()
    model_configured = bool(model_value) and Path(model_value).is_file()
    if not engine_installed:
        reason = "engine_not_installed"
    elif not model_configured:
        reason = "model_not_configured"
    elif not transport_available:
        reason = "audio_transport_not_available"
    else:
        reason = None
    return {
        "engine": "openwakeword",
        "engine_installed": engine_installed,
        "model_configured": model_configured,
        "audio_transport_available": bool(transport_available),
        "operational": reason is None,
        "reason": reason,
        "always_listening": False,
        "opt_in_required": True,
    }
