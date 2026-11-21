"""Deterministic tests for the offline wake-word core (no audio hardware, no engine)."""

import sys
import threading
import types
from array import array

import pytest

from mia.voice import wake
from mia.voice.wake import (
    FRAME_BYTES,
    MAX_CHUNK_BYTES,
    OpenWakeWordDetector,
    WakeWordConfig,
    WakeWordNotPermitted,
    WakeWordSession,
    WakeWordUnavailable,
    build_default_detector,
    offline_wake_word_status,
)

FRAME = b"\x01\x00" * (FRAME_BYTES // 2)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ScriptedDetector:
    """Returns scripted scores; records every interaction."""

    name = "scripted"

    def __init__(self, scores=(), default=0.0):
        self.scores = list(scores)
        self.default = default
        self.frames = []
        self.resets = 0
        self.closed = 0

    def score(self, frame):
        self.frames.append(frame)
        return self.scores.pop(0) if self.scores else self.default

    def reset(self):
        self.resets += 1

    def close(self):
        self.closed += 1


def make_session(detector=None, **config):
    clock = Clock()
    detector = detector or ScriptedDetector()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return detector

    session = WakeWordSession(factory, WakeWordConfig(**config), clock=clock)
    return session, detector, clock, calls


# ------------------------------------------------------------- opt-in & lifecycle


def test_session_is_off_by_default_and_builds_nothing_until_armed():
    session, _, _, calls = make_session()
    assert session.state == "off"
    assert session.status()["listening"] is False
    assert calls["n"] == 0
    assert session.feed(FRAME) is None  # frames while off are dropped, not scored
    assert calls["n"] == 0


@pytest.mark.parametrize("value", [False, None, 1, "yes", 0])
def test_arm_requires_explicit_opt_in(value):
    session, _, _, calls = make_session()
    with pytest.raises(WakeWordNotPermitted):
        session.arm(owner_opt_in=value)
    assert session.state == "off"
    assert calls["n"] == 0


def test_arm_reports_listening_and_never_claims_audio_retention():
    session, _, _, _ = make_session()
    status = session.arm(owner_opt_in=True)
    assert status["state"] == "armed"
    assert status["listening"] is True
    assert status["detector"] == "scripted"
    assert status["audio_retained"] is False
    # Arming again is a no-op that does not build a second detector.
    session.arm(owner_opt_in=True)


def test_factory_failure_leaves_session_off_with_clear_error():
    def broken():
        raise RuntimeError("onnx exploded")

    session = WakeWordSession(broken)
    with pytest.raises(WakeWordUnavailable, match="onnx exploded"):
        session.arm(owner_opt_in=True)
    assert session.state == "off"
    assert session.status()["listening"] is False


def test_unavailable_error_from_factory_is_passed_through_unchanged():
    def missing():
        raise WakeWordUnavailable("no model")

    session = WakeWordSession(missing)
    with pytest.raises(WakeWordUnavailable, match="^no model$"):
        session.arm(owner_opt_in=True)


# ------------------------------------------------------------------------ stopping


def test_stop_is_immediate_closes_detector_once_and_drops_late_frames():
    session, detector, _, _ = make_session(ScriptedDetector(default=0.9))
    session.arm(owner_opt_in=True)
    session.feed(b"\x01\x00" * 100)  # leaves a partial frame in the buffer
    status = session.stop()
    assert status["state"] == "stopped"
    assert status["listening"] is False
    assert status["stop_reason"] == "owner"
    assert detector.closed == 1
    scored_before = len(detector.frames)

    # A late frame from a transport that has not noticed the stop yet.
    assert session.feed(FRAME * 5) is None
    assert len(detector.frames) == scored_before  # never scored after stop
    assert session.status()["dropped_chunks"] >= 1

    session.stop()  # idempotent
    assert detector.closed == 1


def test_stop_clears_partial_audio_buffer_so_nothing_survives_a_rearm():
    detector = ScriptedDetector()
    session, _, _, _ = make_session(detector)
    session.arm(owner_opt_in=True)
    session.feed(b"\x02\x00" * 100)
    assert len(session._buffer) > 0
    session.stop()
    assert len(session._buffer) == 0
    session.arm(owner_opt_in=True)
    assert len(session._buffer) == 0


def test_rearm_after_stop_requires_opt_in_again_and_resets_counters():
    session, _, _, calls = make_session(ScriptedDetector(default=0.9), patience=1)
    session.arm(owner_opt_in=True)
    assert session.feed(FRAME) is not None
    session.stop()
    with pytest.raises(WakeWordNotPermitted):
        session.arm(owner_opt_in=False)
    status = session.arm(owner_opt_in=True)
    assert status["activations"] == 0
    assert calls["n"] == 2


def test_stop_from_another_thread_never_scores_after_stop_returns():
    detector = ScriptedDetector(default=0.0)
    session, _, _, _ = make_session(detector)
    session.arm(owner_opt_in=True)
    stop_done = threading.Event()
    scored_after_stop = []

    def feeder():
        while not stop_done.is_set():
            session.feed(FRAME)
        count_at_stop = len(detector.frames)
        for _ in range(50):
            session.feed(FRAME)
        scored_after_stop.append(len(detector.frames) - count_at_stop)

    thread = threading.Thread(target=feeder)
    thread.start()
    session.stop()
    stop_done.set()
    thread.join(timeout=5)
    assert scored_after_stop == [0]


# --------------------------------------------------------- framing and detection


def test_arbitrary_chunk_sizes_are_reframed_into_exact_80ms_frames():
    detector = ScriptedDetector()
    session, _, _, _ = make_session(detector)
    session.arm(owner_opt_in=True)
    payload = bytes(range(256)) * 25  # 6400 bytes = 2.5 frames
    for start in range(0, len(payload), 1000):
        session.feed(payload[start : start + 1000])
    assert [len(f) for f in detector.frames] == [FRAME_BYTES, FRAME_BYTES]
    assert b"".join(detector.frames) == payload[: 2 * FRAME_BYTES]
    assert len(session._buffer) < FRAME_BYTES  # remainder is always under one frame


def test_chunk_validation_bounds_memory_and_rejects_bad_input():
    session, _, _, _ = make_session()
    session.arm(owner_opt_in=True)
    with pytest.raises(ValueError):
        session.feed(b"\x00" * (MAX_CHUNK_BYTES + 2))
    with pytest.raises(ValueError):
        session.feed(b"\x00\x00\x00")  # odd byte count: not whole int16 samples
    with pytest.raises(TypeError):
        session.feed("not bytes")  # type: ignore[arg-type]


def test_activation_needs_consecutive_frames_at_or_above_threshold():
    detector = ScriptedDetector(scores=[0.9, 0.1, 0.9, 0.9], default=0.0)
    session, _, _, _ = make_session(detector, threshold=0.5, patience=2)
    session.arm(owner_opt_in=True)
    results = [session.feed(FRAME) for _ in range(4)]
    assert results[:3] == [None, None, None]  # a lone spike never triggers
    event = results[3]
    assert event is not None
    assert event.label == "Hey Mia"
    assert event.score == pytest.approx(0.9)
    assert not hasattr(event, "audio")
    assert session.status()["activations"] == 1


def test_activation_resets_detector_and_discards_remaining_audio():
    detector = ScriptedDetector(default=0.9)
    session, _, _, _ = make_session(detector, patience=1)
    session.arm(owner_opt_in=True)
    assert session.feed(FRAME * 3) is not None  # 3 frames in one chunk
    assert detector.resets == 1
    assert len(detector.frames) == 1  # frames after the trigger are not scored
    assert len(session._buffer) == 0


def test_cooldown_suppresses_retrigger_then_resumes():
    detector = ScriptedDetector(default=0.95)
    session, _, clock, _ = make_session(detector, patience=1, cooldown_seconds=2.0)
    session.arm(owner_opt_in=True)
    assert session.feed(FRAME) is not None
    assert session.status()["in_cooldown"] is True
    scored = len(detector.frames)
    clock.now += 1.0
    assert session.feed(FRAME) is None
    assert len(detector.frames) == scored  # cooldown frames are not even scored
    clock.now += 1.5
    assert session.feed(FRAME) is not None
    assert session.status()["activations"] == 2


@pytest.mark.parametrize(
    "bad,expected",
    [(float("nan"), None), (float("-inf"), None), (7.0, 1.0), (-3.0, None)],
)
def test_unusable_scores_fail_closed(bad, expected):
    detector = ScriptedDetector(scores=[bad], default=0.0)
    session, _, _, _ = make_session(detector, patience=1)
    session.arm(owner_opt_in=True)
    event = session.feed(FRAME)
    if expected is None:
        assert event is None
    else:
        assert event is not None and event.score == expected


def test_detector_error_stops_the_session_and_releases_the_detector():
    class Exploding(ScriptedDetector):
        def score(self, frame):
            raise RuntimeError("model crashed")

    detector = Exploding()
    session, _, _, _ = make_session(detector)
    session.arm(owner_opt_in=True)
    assert session.feed(FRAME) is None
    status = session.status()
    assert status["state"] == "stopped"
    assert status["stop_reason"] == "detector_error"
    assert "model crashed" in status["stop_error"]
    assert detector.closed == 1


def test_reset_failure_after_activation_stops_the_session():
    class BadReset(ScriptedDetector):
        def reset(self):
            super().reset()
            if self.resets == 1:
                raise RuntimeError("reset failed")

    session, _, _, _ = make_session(BadReset(default=0.9), patience=1)
    session.arm(owner_opt_in=True)
    assert session.feed(FRAME) is None
    assert session.status()["stop_reason"] == "detector_error"


# ------------------------------------------------------------------ bounded listening


def test_armed_session_disarms_itself_after_max_duration():
    detector = ScriptedDetector()
    session, _, clock, _ = make_session(detector, max_armed_seconds=60.0)
    session.arm(owner_opt_in=True)
    clock.now += 59.0
    session.feed(FRAME)
    assert session.state == "armed"
    clock.now += 2.0
    assert session.feed(FRAME) is None
    status = session.status()
    assert status["state"] == "stopped"
    assert status["stop_reason"] == "max_armed_duration"
    assert detector.closed == 1


def test_activation_storm_disarms_instead_of_waking_repeatedly():
    detector = ScriptedDetector(default=0.99)
    session, _, clock, _ = make_session(
        detector, patience=1, cooldown_seconds=0.0, max_activations_per_minute=3
    )
    session.arm(owner_opt_in=True)
    events = []
    for _ in range(3):
        events.append(session.feed(FRAME))
        clock.now += 1.0
    assert all(e is not None for e in events)
    assert session.feed(FRAME) is None  # fourth within a minute: storm guard
    status = session.status()
    assert status["state"] == "stopped"
    assert status["stop_reason"] == "too_many_activations"
    assert detector.closed == 1


def test_activations_outside_the_one_minute_window_do_not_count_toward_the_storm():
    detector = ScriptedDetector(default=0.99)
    session, _, clock, _ = make_session(
        detector, patience=1, cooldown_seconds=0.0, max_activations_per_minute=2
    )
    session.arm(owner_opt_in=True)
    for _ in range(6):
        assert session.feed(FRAME) is not None
        clock.now += 40.0
    assert session.state == "armed"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"threshold": 0.0},
        {"threshold": 1.5},
        {"patience": 0},
        {"patience": 26},
        {"cooldown_seconds": -1},
        {"cooldown_seconds": 61},
        {"max_armed_seconds": 0},
        {"max_armed_seconds": 24 * 3600 + 1},
        {"max_activations_per_minute": 0},
        {"label": "  "},
        {"label": "x" * 65},
    ],
)
def test_config_rejects_unsafe_values_including_unlimited_listening(kwargs):
    with pytest.raises(ValueError):
        WakeWordConfig(**kwargs)


# ------------------------------------------------------------ openWakeWord adapter


class FakeModel:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.predicted = []
        self.resets = 0
        FakeModel.instances.append(self)

    def predict(self, frame):
        self.predicted.append(frame)
        return {"hey_mia_v1": 0.75, "other": 0.1}

    def reset(self):
        self.resets += 1


def to_list(frame):
    return list(array("h", frame))


@pytest.fixture()
def model_file(tmp_path):
    path = tmp_path / "hey_mia_v1.onnx"
    path.write_bytes(b"not a real model")
    return path


def make_adapter(model_file, **kwargs):
    FakeModel.instances.clear()
    return OpenWakeWordDetector(
        model_file, model_factory=FakeModel, array_factory=to_list, **kwargs
    )


def test_adapter_passes_int16_samples_and_selects_the_configured_model_key(model_file):
    detector = make_adapter(model_file)
    assert detector.score(FRAME) == pytest.approx(0.75)
    model = FakeModel.instances[0]
    assert model.kwargs["wakeword_models"] == [str(model_file)]
    assert model.kwargs["inference_framework"] == "onnx"  # the Windows-supported runtime
    assert model.kwargs["vad_threshold"] == 0.5
    assert model.predicted[0] == [1] * (FRAME_BYTES // 2)


def test_adapter_rejects_wrong_frame_size_and_supports_reset_close(model_file):
    detector = make_adapter(model_file)
    with pytest.raises(ValueError):
        detector.score(FRAME[:-2])
    detector.reset()
    assert FakeModel.instances[0].resets == 1
    detector.close()
    assert detector._model is None


def test_adapter_requires_an_existing_owner_supplied_model_file(tmp_path):
    with pytest.raises(WakeWordUnavailable, match="not found"):
        OpenWakeWordDetector(tmp_path / "missing.onnx", model_factory=FakeModel)


def test_adapter_without_the_engine_gives_actionable_install_guidance(model_file, monkeypatch):
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    monkeypatch.setitem(sys.modules, "openwakeword.model", None)
    with pytest.raises(WakeWordUnavailable, match="voice-wake"):
        OpenWakeWordDetector(model_file)


def test_adapter_never_invokes_the_library_model_downloader(model_file, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("Mia must not download wake-word models implicitly")

    package = types.ModuleType("openwakeword")
    package.__path__ = []  # mark as a package
    package.utils = types.SimpleNamespace(download_models=boom)
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)
    FakeModel.instances.clear()
    detector = OpenWakeWordDetector(model_file, array_factory=to_list)
    detector.score(FRAME)  # would raise if anything tried to download


def test_adapter_wraps_model_load_failures(model_file):
    class Broken:
        def __init__(self, **_kwargs):
            raise OSError("corrupt model")

    with pytest.raises(WakeWordUnavailable, match="corrupt model"):
        OpenWakeWordDetector(model_file, model_factory=Broken)


def test_adapter_vad_threshold_is_validated(model_file):
    with pytest.raises(ValueError):
        OpenWakeWordDetector(model_file, vad_threshold=2.0, model_factory=FakeModel)


def test_session_with_adapter_end_to_end(model_file):
    detector = make_adapter(model_file)
    session = WakeWordSession(lambda: detector, WakeWordConfig(patience=2, threshold=0.7))
    session.arm(owner_opt_in=True)
    assert session.feed(FRAME) is None
    event = session.feed(FRAME)
    assert event is not None and event.score == pytest.approx(0.75)
    session.stop()
    assert detector._model is None  # engine released on stop


# --------------------------------------------------- default factory and status


def test_default_factory_requires_explicit_model_configuration():
    with pytest.raises(WakeWordUnavailable, match="MIA_WAKE_MODEL_PATH"):
        build_default_detector({})


def test_default_factory_rejects_a_non_numeric_vad_threshold(model_file):
    with pytest.raises(WakeWordUnavailable, match="MIA_WAKE_VAD_THRESHOLD"):
        build_default_detector(
            {"MIA_WAKE_MODEL_PATH": str(model_file), "MIA_WAKE_VAD_THRESHOLD": "loud"}
        )


def _patch_find_spec(monkeypatch, present):
    real = wake.importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name in {"openwakeword", "onnxruntime"}:
            return object() if present else None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(wake.importlib.util, "find_spec", fake)


def test_status_reports_engine_missing_without_importing_it(monkeypatch):
    _patch_find_spec(monkeypatch, present=False)
    status = offline_wake_word_status({})
    assert status["operational"] is False
    assert status["reason"] == "engine_not_installed"
    assert status["always_listening"] is False
    assert status["opt_in_required"] is True


def test_status_is_not_operational_until_engine_model_and_transport_all_exist(
    monkeypatch, model_file
):
    _patch_find_spec(monkeypatch, present=True)
    assert offline_wake_word_status({})["reason"] == "model_not_configured"
    env = {"MIA_WAKE_MODEL_PATH": str(model_file)}
    status = offline_wake_word_status(env)
    assert status["model_configured"] is True
    assert status["reason"] == "audio_transport_not_available"
    assert status["operational"] is False  # engine + model alone is not capability
    ready = offline_wake_word_status(env, transport_available=True)
    assert ready["operational"] is True and ready["reason"] is None


def test_status_treats_a_missing_model_path_as_not_configured(monkeypatch, tmp_path):
    _patch_find_spec(monkeypatch, present=True)
    status = offline_wake_word_status({"MIA_WAKE_MODEL_PATH": str(tmp_path / "gone.onnx")})
    assert status["model_configured"] is False
    assert status["reason"] == "model_not_configured"
