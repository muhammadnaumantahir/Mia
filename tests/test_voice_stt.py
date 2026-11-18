from mia.voice.stt import FasterWhisperTranscriber


def test_transcriber_rejects_empty_audio_without_loading_model(tmp_path):
    transcriber = FasterWhisperTranscriber(data_dir=tmp_path)
    try:
        transcriber.transcribe(b"")
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("Empty audio must be rejected")


def test_transcriber_rejects_unsupported_file_suffix_safely(tmp_path, monkeypatch):
    class FakeSegment:
        text = "  Hello Mia  "

    class FakeInfo:
        language = "en"
        language_probability = 0.98

    class FakeModel:
        def transcribe(self, path, **kwargs):
            assert path.endswith(".webm")
            assert kwargs["beam_size"] == 1
            assert kwargs["vad_filter"] is True
            return iter([FakeSegment()]), FakeInfo()

    transcriber = FasterWhisperTranscriber(data_dir=tmp_path)
    monkeypatch.setattr(transcriber, "_get_model", lambda: FakeModel())
    result = transcriber.transcribe(
        b"fake-audio",
        filename="capture.exe",
    )
    assert result["text"] == "Hello Mia"
    assert result["language"] == "en"
    assert result["language_probability"] == 0.98
