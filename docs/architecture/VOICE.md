# Mia voice architecture

## Interaction model

The text interface remains the primary fallback. Voice is an optional interaction channel:
1. Browser asks for microphone access only after the user presses the microphone button.
2. The recording is shown as active and can be cancelled.
3. The browser uploads the short recording to the local Mia API.
4. Faster-Whisper transcribes locally on CPU.
5. The user can review/edit the transcription before sending it to the model.
6. Mia speaks the response using a selectable voice exposed by the local browser/OS speech engine.

No microphone is left listening in the foundation build. Wake-word always-on mode is a later, separately gated feature.

## Speech-to-text

- Candidate: Faster-Whisper (MIT), pinned audit commit in UPSTREAM_PINS.md.
- Default target model: `base`, CPU, int8, 2 threads, 1 worker.
- Model download is on first use and stored beneath `MIA_DATA_DIR/models/faster-whisper`.
- Use a multilingual model so English, Urdu and mixed-language speech can be recognized; auto language detection is the default.
- Allow an explicit language hint; do not hardcode English for Roman Urdu users.
- Limit clip size and duration; reject empty or unsupported uploads.
- Record the selected model and language metadata, but do not retain raw audio by default.

The target CPU is older than the CPU used in the upstream benchmark. Run actual latency and accuracy tests on the target Windows computer before deciding whether `base` or `tiny` should be the default. Quantized CPU inference is the expected path; no GPU is assumed.

## Text-to-speech

Browser Web Speech remains the zero-install fallback. An optional `tts` extra (`pip install -e ".[tts]"`) adds offline pyttsx3 synthesis using the operating-system voice engine (SAPI5 on Windows). The local voice endpoint returns the actual installed voice ID, display name, language and reported gender; the UI labels a voice female only when the system metadata says `female`. Speech text is not persisted in Observatory events and generated WAV files are held only in memory by the API response. Synthesis is serialized and its temporary WAV file is deleted after reading. No voice weights are downloaded.

Optional local TTS candidates:
- pyttsx3 (MPL-2.0): uses Windows SAPI and installed voices offline. A compatible installed female voice must be selected and tested on the target device.
- Kokoro-82M (Apache-2.0 model): evaluate separately because the Python package brings a larger PyTorch/Transformers stack and voice/language quality needs testing.
- Piper (GPL-3.0): do not bundle without a deliberate licensing decision; review each voice model separately.

## Wake word

Wake-word detection is not enabled in this release. openWakeWord is a future adapter candidate, but its inspected project has not had recent source commits compared with other candidates. Recheck maintenance, model licenses and the false-positive rate before adopting. Default to push-to-talk unless a wake-word model demonstrates acceptable behavior.

## Privacy and control

- Microphone is off until the user presses the control.
- Visible recording state and cancel/stop are mandatory.
- Raw audio is deleted after transcription.
- No audio storage or upload to cloud providers by default.
- A transcript is editable before it is sent.
- Cloud inference data-egress rules still apply to the resulting transcript.
- Wake-word background listening requires separate opt-in, a visible status and a one-action mute.
