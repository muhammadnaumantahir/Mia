# Voice feature completion checklist

Audit of `main` at commit 51a6242. Status is updated as milestones land; a box is ticked only when it is
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
- [ ] No way to interrupt Mia while speaking (mic is off during playback)
- [ ] "Stop speaking" phrase not supported; stop phrases only match as the whole utterance
- [ ] No push-to-talk inside the voice session
- [ ] No speech speed / volume controls (local rate hard-coded to 175, browser rate 1)
- [ ] Long replies are spoken as one blob; browser TTS is cut off by a 45 s timeout
- [ ] No distinct listening / processing / speaking / stopped state UI, no mute control
- [ ] No microphone device selection
- [ ] Hands-free mode has no local/offline option (only single-clip Local record)
- [ ] Tests are static string checks only; no real-browser lifecycle tests
- [ ] Wake word: evaluated, not enabled (see VOICE.md); decision to be recorded

## Milestones
1. Audit checklist (this file)
2. State machine, interrupt, stop-speaking, chunked TTS, speed/volume, mute, state UI
3. Playwright lifecycle tests with a mocked recogniser and synthesiser
4. Push-to-talk, device selection, local (offline) hands-free option
5. Docs, regression run, final report
