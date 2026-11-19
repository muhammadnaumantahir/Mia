# Mia voice architecture

## Interaction modes

Mia supports three distinct voice paths. Voice is opt-in; text chat always remains available.

### Hands-free browser voice conversation

The Assistant page offers **Talk with Mia**:

1. The owner presses **Start voice** and grants microphone permission if the browser asks.
2. The browser's Web Speech SpeechRecognition interface listens continuously during this explicitly started session. English and Urdu (Pakistan) recognition language choices are exposed.
3. Mia shows interim speech text while listening. A completed utterance is automatically submitted after a short silence; the owner does not need to press Send.
4. Mia routes the transcript through the normal chat pipeline, including the current conversation, available model routing, governance and memory retrieval.
5. Mia speaks the reply through the selected browser or local operating-system voice. It waits for speech playback to finish, then resumes listening.
6. The owner can press **End conversation** or say **“stop listening”**, **“stop voice”**, **“goodbye Mia”** or **“sleep Mia”**.

This is an active conversation session, not a wake word and not always-on listening. The browser's speech recognition implementation is not consistently available across all browsers. Some implementations use a server-based recognition service, can require network access, and may send microphone audio to that service outside Mia. The UI discloses this before the session starts. Do not use this mode for confidential or sensitive speech unless the browser/service data handling is acceptable.

### Local push-to-talk transcription

The separate **Local record** button remains available as a fallback:

1. The user records one short clip.
2. Mia uploads that clip to its local API.
3. Faster-Whisper transcribes it locally on CPU when the optional voice extra and model weights are installed.
4. The transcript is placed in the composer for review/editing before sending.

The first Faster-Whisper use downloads the selected model into MIA_DATA_DIR/models/faster-whisper. The default is multilingual base, CPU int8, one worker and up to two CPU threads. The target CPU is older than current upstream benchmark machines; measure real latency on the target Windows laptop. If it is too slow, test MIA_STT_MODEL=tiny and compare accuracy before the demo. The transcript—not the raw audio—is then handled by normal chat and provider data-egress rules.

### Spoken replies

- Browser Web Speech synthesis is the no-install fallback.
- Optional tts extra (pip install -e ".[tts]") adds offline pyttsx3 synthesis using installed OS voices (SAPI5 on Windows).
- The voice selector reports real local voice IDs, names, languages and metadata. A local voice is labelled female only when the system reports female.
- In Voice Conversation Mode, replies are spoken regardless of the ordinary **Speak** checkbox, then listening resumes. In text mode, the checkbox controls optional speech.
- If the selected local voice is unavailable or synthesis fails, the UI falls back to browser speech synthesis when the browser supports it.

## Speech-to-text

- Local candidate: Faster-Whisper (MIT), pinned audit commit in UPSTREAM_PINS.md.
- Default local model: base, CPU, int8, 2 threads, 1 worker.
- Use a multilingual model so English, Urdu and mixed-language speech can be recognized; auto language detection is the default for local transcription.
- The hands-free browser mode uses the selected recognition language (en-US or ur-PK); browser language availability depends on the installed browser/service.
- Limit clip size and duration; reject empty or unsupported uploads.
- Record model and language metadata, but do not retain raw audio by default.

## Text-to-speech

Optional local TTS candidates:
- pyttsx3 (MPL-2.0): uses Windows SAPI and installed voices offline. A compatible installed voice must be selected and tested on the target device.
- Kokoro-82M (Apache-2.0 model): evaluate separately because the Python package brings a larger PyTorch/Transformers stack and voice/language quality needs testing.
- Piper (GPL-3.0): do not bundle without a deliberate licensing decision; review each voice model separately.

## Wake word and always-on mode

Wake-word detection is not enabled. Mia does not listen before the owner starts Voice Conversation Mode or presses Local record. openWakeWord remains a future adapter candidate; recheck maintenance, model licenses and false-positive rates before adopting it. A true wake-word mode requires a separate opt-in, visible listening indicator and a one-action mute.

## Privacy and control

- Microphone use begins only after an explicit user action.
- The active voice session has a visible status and an End conversation control.
- Browser hands-free recognition may use a remote browser speech service; it is not described as local/offline. Check the browser's service settings and do not speak secrets in this mode.
- Local push-to-talk audio is sent to the Mia API on loopback and processed by Faster-Whisper; raw audio is deleted after transcription.
- A local transcript remains editable before it is sent to the AI model.
- Cloud inference data-egress rules still apply to text sent to the model; private retrieved memory remains local unless remote context is explicitly permitted.
- Speech text and raw audio are not persisted in Observatory events.
- Stopping Voice Conversation Mode stops recognition and cancels active speech playback.
