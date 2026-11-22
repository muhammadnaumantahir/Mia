# Voice-driven computer actions: design note for MIA-COMP-10

**Author:** A4 (2026-11-22). **Status:** proposal only. MIA-COMP-10 is **not started**: it needs the central permission broker (MIA-COMP-04) and the approval/audit/undo UX (MIA-COMP-09), which are still OPEN. Nothing here is implemented or tested; it exists so those two tasks are built with voice in mind and so COMP-10 can start fast once they land.

## 1. What is true today (verified in source, 2026-11-22)

- Spoken and typed turns already use one pipeline (`submitPrompt` -> `Brain`; the UI now sends `interface: voice|text`, MIA-M1-09). Voice must stay a thin input/output layer over that pipeline, never a second command path.
- Computer actions today go through `computer/approvals.py`: one-time approvals bound to a payload digest (see `docs/computer/COMPUTER_ACCESS_AUDIT.md`).
- Speech recognition is a *guess*: the browser engine and local Faster-Whisper both return text only; Faster-Whisper reports a language and `language_probability`, not a per-word confidence. Roman Urdu has no engine language code (`docs/STATUS.md`, MIA-M1-11), so spoken Roman Urdu is the least reliable input.
- Spoken stop/end commands exist and are matched only when the whole utterance is short (`classifyVoiceCommand`); Interrupt, Esc, mute and Stop-reply cancel speech and the stream (MIA-M1-12, VOICE-05).
- Notifications and spoken replies are never sent off this machine by the voice layer itself.

## 2. Hazards specific to voice (why text rules are not enough)

1. **Mishearing becomes action.** A wrong transcript of "delete the draft" vs "delete the draft folder" is a different action. Recognition errors are silent.
2. **Overheard or injected audio.** Other people, a video, or a TV can speak to the microphone; text input cannot be injected that way.
3. **Self-hearing.** Mia's own speech can be transcribed as a command (echo). Barge-in is off by default for exactly this reason.
4. **Ambiguity and brevity.** "Yes", "okay", "go ahead" are short, common and easily triggered by background speech.
5. **Cancel latency.** A spoken "stop" must reach the action faster than the action finishes; cancellation must be real, not a UI state.
6. **Hidden consequences.** A listener cannot scan a diff; read-aloud summaries drop detail.

## 3. Proposed rules for COMP-10 (to be confirmed with COMP-04/09 owners)

1. **Same door as text.** Voice only produces a *proposed action* in the same typed form the Brain produces for text, submitted to the COMP-04 broker. No voice-only tools, no voice-only permission levels, no bypass of risk classification.
2. **Voice never approves its own side effects at risk >= the broker's "needs approval" level.** Approval is a deliberate UI action with the exact preview on screen (COMP-09). A spoken "yes" may *select* a pre-presented option only for low-risk, reversible actions, and only if the owner opted in to that in settings. Default: off.
3. **Read back what will happen, from the structured action, not from the model's prose.** The spoken confirmation is generated from the action fields (verb, target path/app, count), and the same text is shown on screen. It states the irreversible parts first.
4. **Confirm against the transcript the owner can see.** Show the recognised text before acting; for any action with side effects, require the action text itself (not just "yes") to match a stable identifier shown on screen, so a mishearing cannot hit a different target.
5. **Echo and third-party protection.** Do not accept a command while Mia is speaking (barge-in stays off for actions), and discard recognised audio that overlaps Mia's playback. Wake phrase or push-to-talk required for the *session*; each side-effecting action needs a fresh confirmation, never a standing one.
6. **Low-confidence handling.** If the engine gives low `language_probability`, the language differs from the owner's setting, or the utterance contains Roman Urdu code-switching, treat it as a question to ask back ("Did you mean ...?"), never as an action.
7. **Cancellation is a first-class command.** "Stop" / Esc / Interrupt / mute cancel the running action through the broker's cancellation path (COMP-09), report what had already happened, and offer undo where it exists. A cancel that cannot reach the action must say so out loud and on screen.
8. **Short, time-boxed windows.** A pending confirmation expires quickly (proposal: 30 seconds), and expiry is announced, not silent.
9. **Audit.** Every voice-originated action records: transcript (kept only if the owner allows), engine and language, the proposed action, who confirmed (voice option vs UI), and result. No audio is retained (current behaviour: `audio_retained: false`).
10. **Fail closed.** Microphone lost, engine error, network loss, backend restart or provider failure during a pending action cancels it and says so.

## 4. Acceptance tests COMP-10 should include (all with fakes)

- A mis-transcribed target (similar words) cannot execute without the on-screen identifier being confirmed.
- A spoken "yes" cannot approve a risky action; the UI approval can.
- Mia's own speech and a second speaker do not trigger a confirmation (echo/overlap fixtures).
- Low-confidence, wrong-language and Roman Urdu fixtures ask back instead of acting.
- "Stop" mid-action: the broker records cancellation, the action stops, the owner hears what completed.
- Confirmation expiry, mic loss, backend restart and provider failure all fail closed.
- The same request typed and spoken produces the identical proposed action and the identical broker decision.
- The spoken read-back matches the on-screen action text exactly.

## 5. Not decided (needs the owner)

- Whether any low-risk action may ever be confirmed by voice alone (default proposed: no).
- Retention of transcripts of voice-originated actions (default proposed: audit metadata only, no text unless the owner opts in).
- Whether wake-phrase sessions may trigger actions at all, or only push-to-talk.

## 6. What this note does not claim

No real-microphone, real-speaker or real-engine evidence exists for any of these rules; they are design requirements and test ideas, to be verified on the owner's devices (`OWNER_VERIFICATION_REQUIRED`) once implemented.
