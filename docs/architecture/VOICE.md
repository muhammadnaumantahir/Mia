# Mia voice architecture

Voice is opt-in and always layered on the normal chat pipeline. A voice turn is submitted through the same
`submitPrompt` -> `/api/gateway/chat` path as typed text, so model routing, governance, conversation history,
memory retrieval, tool use and approval cards are identical. Voice never approves anything: approval cards
must be clicked by the owner.

## Controls on the Assistant page ("Talk with Mia")

| Control | Behaviour |
| --- | --- |
| **Start voice / End conversation** | Starts or ends the session. Ending stops recognition, cancels speech and releases the microphone; a stopped session cannot restart itself. |
| **Engine** | *Browser speech service (online)* or *Local Faster-Whisper (offline)*. Locked while a session is active. |
| **Input** | *Hands-free* (continuous, auto-submit after silence) or *Push-to-talk* (microphone only open while the Hold-to-talk button or Space is held; submit on release). |
| **Language** | English (`en-US`), Urdu Pakistan (`ur-PK`), or Auto (local engine only; Whisper detects the language of each utterance, which is how mixed English/Urdu is handled). |
| **Microphone** | Selects the input device for the local engine and for Local record. The browser engine cannot choose a device and uses the browser/OS default. |
| **Mute mic** | Closes the microphone but keeps the session; unmute resumes. |
| **Interrupt (Esc)** | Appears while Mia speaks; stops playback immediately and returns to listening. |
| **Interrupt by voice (use headphones)** | Opt-in, off by default. Keeps the browser recogniser open while Mia speaks so that speaking over her stops her. Without headphones Mia's own voice can trigger it. |
| **Speed / Volume** | Apply to browser voices and, for speed, to local pyttsx3 voices (`rate = 175 x speed`). Local-voice volume uses the audio element volume. |
| **Test voice** | Speaks a short preview with the selected voice. |

### Session states

`Off -> Ready -> Listening -> Thinking -> Speaking -> Listening ...`, plus `Muted`, `Stopped` and `Error`. The orb
animates per state (respecting `prefers-reduced-motion`), a badge shows when the microphone is open and which
engine receives it, and failures are shown as text rather than silent.

### Turn taking

1. Mic opens (explicit Start, or holding the talk button in push-to-talk).
2. Interim and final text appear under the console ("Hearing: ...").
3. Hands-free submits after ~1.25 s of silence on the browser engine (~0.15 s after the local engine's own
   0.9 s silence detection). Push-to-talk submits on release.
4. The recogniser is stopped while Mia thinks and speaks (unless voice barge-in is enabled), so nothing said
   during that time can create a second submission.
5. Mia's reply is cleaned of markdown, split into sentence chunks of at most 220 characters and spoken in
   sequence (at most 6000 characters per reply; the rest stays on screen). A new reply or an interrupt always
   cancels the previous playback first, so audio cannot overlap.
6. Listening resumes automatically (hands-free) or returns to *Ready* (push-to-talk).

### Voice commands

Matched only in short utterances (the phrase plus at most three extra words), never sent to the model:

- End the session: "stop listening", "stop voice", "goodbye Mia", "sleep Mia", "end conversation".
- Stop talking but keep the session: "stop speaking", "stop talking", "be quiet".

A long question that merely contains these words is treated as a normal prompt.

### Safety rails

- A restart-loop guard ends the session if the recogniser starts 5 times in 6 s without hearing speech.
- Permission denied, missing device, blocked service, unsupported language, lost network (browser engine) and
  local-engine failures stop the session with a specific message.
- A late event from an already-stopped recogniser is ignored.
- Page unload ends the session and cancels speech.

## Speech input

### Browser engine (Web Speech `SpeechRecognition`)

Works in Chromium browsers (Chrome, Edge). Not offline: Chrome sends audio to a Google service, Edge to a
Microsoft service. The UI says so before the session starts. Do not use it for confidential speech. Urdu
quality and availability depend on the browser's service.

### Local engine (Faster-Whisper, offline)

- Install: `python -m pip install -e ".[voice]"`. Model weights download into `MIA_DATA_DIR/models/faster-whisper`
  on first transcription (not stored in Git). Defaults: multilingual `base`, CPU int8, 1 worker, up to 2 threads.
  Use `MIA_STT_MODEL=tiny` for speed or `small` for better Urdu.
- The page captures microphone audio (`getUserMedia`, device selectable), cuts it into utterances with a simple
  energy detector (0.9 s of silence ends an utterance, ignores clips under 250 ms of speech, 30 s maximum),
  converts each to 16 kHz mono WAV and POSTs it to `/api/voice/transcribe` on the loopback server. The
  transcript is returned and the audio is deleted; the microphone is released when the session ends or while
  Mia is thinking/speaking.
- Language is sent as `language=en` or `language=ur`; "Auto" omits it so Whisper detects it per utterance.
  Whisper language detection on very short clips can be wrong; prefer an explicit language when possible.
- `/api/voice/status` reports whether the extra is installed; the UI refuses to start the local engine and
  shows the install command when it is not.
- The separate **Local record** button remains: one clip, transcript placed in the composer for review.

## Speech output

- Optional `tts` extra (`python -m pip install -e ".[tts]"`) adds pyttsx3 synthesis using installed OS voices
  (SAPI5 on Windows). The selector lists real voice IDs, names and languages; "female" is shown only when the
  OS reports it.
- Browser `speechSynthesis` is the no-install fallback. If a local voice fails mid-reply, the remaining chunks
  continue on the browser voice.
- In a voice session replies are always spoken; in text mode the **Speak** checkbox controls it.
- Urdu speech output requires an installed Urdu voice; this repository does not ship or install voices.
  Availability on a given Windows/browser was not verified here.

## Wake phrase

An opt-in **Hey Mia** browser wake-phrase mode is implemented on the Assistant page.

- It is off by default and starts only after the owner clicks **Enable Hey Mia** and grants microphone permission.
- It uses the browser Web Speech recognition service (online), not a local keyword-spotting model.
- While armed, the page visibly shows the microphone as on and listens for the English phrase **Hey Mia**. Unaddressed speech is not submitted to chat.
- After the phrase is detected, Mia removes the wake phrase, submits any following words as the user's turn, switches to the selected conversation language, speaks the reply and re-arms for the next phrase.
- **Mute mic**, **Disable Hey Mia** and **End conversation** stop recognition immediately; a stopped session does not re-arm.
- The browser may process microphone audio outside Mia. Do not use this mode for confidential speech unless the browser provider's processing is acceptable.

This is a browser-page wake phrase, not offline wake-word detection and not an OS-level background listener. It requires the Assistant page to remain open and the browser speech service to be available. Local Faster-Whisper remains the offline transcription engine after an explicit voice session starts; it does not provide local wake-phrase detection. A true offline always-on detector remains future work and must be separately opt-in, visibly indicated, and immediately muteable.

## Privacy and control

- The microphone opens only after an explicit user action and the badge shows when it is open.
- Raw audio is never saved by default: browser audio is handled by the browser service; local audio is held in
  memory in the page, sent over loopback, transcribed and deleted. Observatory events record metadata only (no
  transcript text, no audio).
- Transcripts are handled like typed text by the chat pipeline and its data-egress rules; retrieved private
  memory stays local unless remote context is explicitly permitted.
- Voice cannot approve sensitive computer actions; approval cards require a click.
- Sessions stop on End conversation, a stop phrase, any fatal error, page unload and (browser engine) loss of
  network.

## Browser compatibility

| Browser | Browser engine | Local engine | Speech output |
| --- | --- | --- | --- |
| Chrome / Edge (desktop) | Supported (online) | Supported | Browser voices + local voices |
| Firefox | Not supported (no SpeechRecognition) | Supported | Browser voices + local voices |
| Safari | Partial/unreliable; not tested | Not tested | Browser voices |

Only Chromium was exercised by the automated tests.

## Automated tests

- `tests/test_voice_browser.py`: 34 real-Chromium tests of the page JavaScript against the real FastAPI app and
  gateway, using a controllable fake recogniser and synthesiser, plus Chromium's fake microphone for the local
  engine (transcription endpoint mocked).
- `tests/test_voice_api.py`, `test_voice_stt.py`, `test_voice_tts.py`: backend endpoints and adapters.
- `tests/test_voice_ui_contract.py`: static contract checks for voice controls, privacy disclosure and the explicit opt-in wake phrase.

These do not prove that a physical microphone, speaker, Windows SAPI voice or the Whisper model works on the
target laptop. See `docs/voice/OWNER_VERIFICATION.md`.
