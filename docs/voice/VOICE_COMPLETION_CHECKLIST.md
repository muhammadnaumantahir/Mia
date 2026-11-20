# Voice feature completion checklist

Audit baseline: `main` at commit d8d00fc (conversation-first roadmap). Status is updated as milestones land; a box is ticked only when it is
implemented and covered by a test that was actually run.

## Already working (audited, not rebuilt)
- [x] Hands-free continuous browser recognition (Web Speech), explicit start, End conversation button
- [x] English / Urdu (Pakistan) recognition language selector
- [x] Interim + final transcript preview, auto-submit after silence
- [x] Voice turns use the normal chat pipeline (`submitPrompt`), memory references stay visible
- [x] Local single-clip record -> Faster-Whisper `/api/voice/transcribe`
- [x] Local pyttsx3 TTS (`/api/voice/speak`) with browser TTS fallback, voice preview
- [x] Stop phrases (exact match only): stop listening, stop voice, goodbye mia, sleep mia
- [x] Privacy disclosure for browser recognition; raw audio deleted after local transcription

## Gaps found
- [x] Interrupt Mia while speaking: Interrupt button, Esc key, optional opt-in voice barge-in (tested)
- [x] "Stop speaking" supported; stop phrases match short utterances, long questions are not treated as commands (tested)
- [x] Push-to-talk mode inside the voice session (button hold and Space key, tested)
- [x] Speech speed and volume controls for browser and local voices (tested for browser voices)
- [x] Long replies are spoken sentence-chunk by sentence-chunk with a spoken-length cap (tested)
- [x] Distinct ready/listening/thinking/speaking/muted/stopped/error states with animated orb, mic badge, mute control (tested)
- [x] Microphone device selection for the local engine and Local record (browser engine cannot choose a device; documented; tested)
- [x] Local Faster-Whisper engine works hands-free and push-to-talk (mic capture, utterance detection, WAV upload tested with a fake microphone; real Whisper not run here)
- [x] 34 real-Chromium lifecycle tests with a fake recogniser/synthesiser against the real app (tests/test_voice_browser.py)
- [ ] Opt-in “Hey Mia” browser wake phrase mode has been implemented on the current development branch; it uses Web Speech recognition (not offline local wake-word detection). Regression tests were added but have not been run; real microphone/privacy/stop behavior remains OWNER_VERIFICATION_REQUIRED.

## Milestones
1. Audit checklist (this file)
2. State machine, interrupt, stop-speaking, chunked TTS, speed/volume, mute, state UI
3. Playwright lifecycle tests with a mocked recogniser and synthesiser
4. Push-to-talk, device selection, local (offline) hands-free option
5. Docs, regression run, final report

## Bugs found and fixed by the new tests
- A late `onstart` event from a recogniser that had already been stopped could flip a stopped session back to
  "Listening" and show the microphone as on.
- The restart-loop guard needed to trigger after 5 starts in 6 s with no speech, not after 8 s.


## Current development update (2026-11-20)

- [ ] Hey Mia wake phrase: code and Playwright regression test added; tests have not been run. Complete section F in `OWNER_VERIFICATION.md` before calling it verified.
- [ ] English, Urdu and Roman Urdu memory-recall evaluation fixture added in `tests/fixtures/conversation_eval.json`; deterministic test runner added, not yet run. This validates context assembly, not live model translation quality.
