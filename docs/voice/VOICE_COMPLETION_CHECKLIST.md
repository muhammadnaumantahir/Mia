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

## MIA-VOICE-01 update (2026-11-21, agent A4)

- [x] Offline wake-word core: detector contract, opt-in/time-bounded/immediately-stoppable session, false-activation guards, no audio retention, fail-closed scoring (`src/mia/voice/wake.py`; 51 tests in `tests/test_voice_wake.py`, run locally, plus a 9-mutant check that all were caught)
- [x] openWakeWord adapter, owner-supplied model only, no implicit download; exercised once against the real engine in a scratch environment (not committed, not a false-accept measurement)
- [x] Truthful `offline_wake_word` capability report in `/api/voice/status` (`operational` stays false until a transport exists)
- [x] License/platform audit recorded in `docs/voice/WAKE_WORD_PLAN.md` (models are CC BY-NC-SA 4.0; Linux + Python 3.13 install needs `--no-deps`)
- [ ] Audio transport + UI for offline wake word (MIA-VOICE-02)
- [ ] Urdu / Roman Urdu stop and end commands (today `END_PHRASES` / `STOP_SPEAKING_PHRASES` in `assistant.html` are English-only) (MIA-VOICE-03)
- [ ] OWNER_VERIFICATION_REQUIRED: real-microphone detection, false-accept/miss rates, Windows install (section G)
