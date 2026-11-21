# Offline wake-word: audit, decision and integration plan

**Task:** MIA-VOICE-01 (agent A4) - **Audit date:** 2026-11-21
**Status:** core detector/lifecycle module implemented and tested; **no audio transport and no UI yet**, so
Mia does **not** have an operational offline wake word. The browser "Hey Mia" phrase described in
[VOICE.md](../architecture/VOICE.md) is separate, online, and unchanged.

## 1. What exists today

| Piece | State | Evidence |
|---|---|---|
| Browser "Hey Mia" phrase (Web Speech, online, page must stay open) | Implemented, opt-in | `src/mia/assistant.html`, `tests/test_voice_browser.py` (needs Chromium; not run in this audit) |
| Detector contract, opt-in/stop/time-bounded session, false-activation guards | **Implemented** | `src/mia/voice/wake.py`, `tests/test_voice_wake.py` |
| openWakeWord adapter (lazy import, owner-supplied model, never downloads) | **Implemented** | same files; also run once against the real engine (section 5) |
| Capability report in `GET /api/voice/status` -> `offline_wake_word` | **Implemented** | `tests/test_voice_api.py` |
| Audio transport (microphone -> detector) and UI | **Not implemented** | planned as MIA-VOICE-02 (section 6) |
| Real positive detection of a spoken phrase | **Not verified** | no TTS/mic in the audit sandbox; `OWNER_VERIFICATION_REQUIRED` |

## 2. Upstream audit: openWakeWord

Checked 2026-11-21 against the upstream README, PyPI and the Hugging Face model card.

| Fact | Finding | Source |
|---|---|---|
| Code license | Apache-2.0 | https://github.com/dscripka/openWakeWord |
| **Pre-trained model license** | **CC BY-NC-SA 4.0 (non-commercial, share-alike)** because of training-data licensing | https://huggingface.co/davidscripka/openwakeword |
| Latest release | 0.6.0 (Feb 2024); `Requires-Python >=3.7` | https://pypi.org/project/openwakeword |
| Windows runtime | ONNX only (upstream: no modern tflite on Windows) | upstream README |
| Linux runtime dependency | `tflite-runtime` (marker `platform_system == "Linux"`) | wheel `METADATA`, verified |
| **Install on Linux + Python 3.13** | **`pip install openwakeword` fails**: no `tflite-runtime` distribution. Workaround verified here: install `onnxruntime numpy scipy scikit-learn tqdm requests`, then `pip install --no-deps openwakeword==0.6.0` | dry-run + real install in a scratch venv |
| Input | 16 kHz mono int16; frames in multiples of 80 ms (1280 samples) | upstream README |
| Language | Wake words are English-only | upstream README |
| Custom phrase ("Hey Mia") | Trainable from synthetic speech (upstream Colab notebooks); no Mia model exists | upstream README |
| Tuning | default threshold 0.5; optional Silero VAD (`vad_threshold`) cuts non-speech false accepts | upstream README |

**Dependency decision:** adopt as an *optional* adapter behind Mia's own `WakeWordDetector` contract
(`docs/research/INTEGRATION_MATRIX.md`). Code license is compatible. **Models are not bundled, not downloaded
by Mia and not stored in Git.** The owner points `MIA_WAKE_MODEL_PATH` at a model file they are licensed to
use. The upstream pre-trained models are non-commercial: acceptable for the owner's personal use, **not** for a
commercial distribution of Mia. Recommended path: the owner trains an own "Hey Mia" model (English phrase;
Urdu/Roman Urdu are handled after activation by the existing language selector, not by the detector).

## 3. Architecture decision: where detection runs

The microphone lives on the owner's device; the Mia server may not.

| Option | Verdict |
|---|---|
| **A. Detector in the Mia process, audio streamed over loopback** | **Chosen for the first transport.** Audio never leaves the machine. Allowed only when the server is bound to 127.0.0.1 and the client is loopback. |
| B. Detector in the browser (onnxruntime-web) | Best long-term privacy for remote Mia (no audio leaves the page), but adds a JS runtime and ships model assets; separate, later task. |
| C. Detector in a paired local companion agent | Required for an Oracle-hosted Mia (ROADMAP Priority 4 agent). Same `WakeWordDetector` contract. |
| D. Stream always-on raw audio to a remote server | **Rejected.** Violates the privacy requirements. |

Consequences: the transport (MIA-VOICE-02) must **refuse non-loopback clients and any non-loopback bind**, so a
cloud-hosted Mia reports `audio_transport_not_available` rather than receiving continuous microphone audio.
No OS-level always-on service is promised: while the Assistant page is the capture source, the page must be open.

## 4. Privacy and safety controls (what is enforced where)

| Requirement | Enforcement | Test |
|---|---|---|
| Never starts by default; explicit owner opt-in | `WakeWordSession.arm(owner_opt_in=True)` only; nothing is constructed before | `test_session_is_off_by_default...`, `test_arm_requires_explicit_opt_in` |
| Visible listening state | `status()["listening"]` is the single source for the indicator | `test_arm_reports_listening...` |
| Immediate stop | `stop()` closes detector, clears buffer; late frames are dropped unscored | `test_stop_is_immediate...`, thread test |
| Never unlimited | `max_armed_seconds` required, 1 s-24 h, default 4 h; auto-disarm | `test_armed_session_disarms_itself...`, config tests |
| False-activation control | threshold, consecutive-frame patience, cooldown, activation-storm guard that **disarms** | `test_activation_*`, `test_cooldown_*`, storm tests |
| No audio retention | only a sub-frame re-framing buffer, cleared on stop/trigger/cooldown; events and status carry no audio | `test_stop_clears_partial_audio_buffer...`, `test_chunk_validation...` |
| Fail closed | detector error stops the session; NaN score = no wake | `test_unusable_scores_fail_closed`, `test_detector_error_...` |
| No implicit model download | adapter never calls the upstream downloader | `test_adapter_never_invokes_the_library_model_downloader` |
| Honest self-report | `operational` only if engine **and** model **and** transport exist | `test_status_is_not_operational_until...`, API test |

Mutation check (run, not committed): disabling each of nine of these properties in a copy of `wake.py` made the
suite fail every time (9/9 caught).

## 5. Resource evidence (measured, with limits)

Scratch venv, Python 3.13, onnxruntime 1.32, openWakeWord 0.6.0, the upstream `hey_jarvis` model, **2-core Linux
sandbox - not the owner's laptop**:

- ~3-5 ms mean CPU time per 80 ms frame (max 9 ms) = roughly 4-6 % of one core.
- Through Mia's session with deliberately odd chunk sizes: 20 s of synthetic noise processed in 0.78 s; zero
  activations; `stop()` then dropped a late frame.
- **Not measured:** false-accept rate on real speech/TV/music, missed-wake rate, accuracy of any "Hey Mia" model,
  Windows behaviour. Synthetic noise is *not* evidence of false-accept performance.

## 6. Remaining work (ordered)

1. **MIA-VOICE-02 - loopback audio transport.** `WebSocket` endpoint fed by an `AudioWorklet` at 16 kHz int16,
   loopback-only, one session at a time, server stops the session when the socket closes; `stop` message and
   socket close both call `WakeWordSession.stop()`. Needs UI in `assistant.html` (coordinate with A6): a persistent
   "Listening for wake word" badge, an always-visible Stop, an opt-in toggle that explains the model license and
   the 4 h auto-stop. On activation it hands off to the existing voice session (which opens its own microphone
   state explicitly).
2. Brain self-description must read `offline_wake_word_status(...)["operational"]` instead of inferring a wake
   feature from route names (today `brain/self_description.py` treats any route containing "wake" as registered).
   Owner: A1 / coordinator - **do not name the transport route until this is agreed**, or Mia could claim a wake
   word that is not operational.
3. Owner-trained "Hey Mia" model and threshold tuning on the owner's real environment.
4. Optional browser-side (option B) or paired-agent (option C) detector for remote deployments.

## 6a. Transport implemented (MIA-VOICE-02, `src/mia/voice/wake_transport.py`) - not yet wired

`register_wake_routes(app, ...)` provides `WS /api/voice/wake/stream`, `GET /api/voice/wake/status` and
`POST /api/voice/wake/stop`. It is **not called by `create_app`** until MIA-BRAIN-05 lands, so no wake route
exists in a running Mia and nothing can claim a wake word.

Protocol v1 (WebSocket): server sends `ready` (`pcm_s16le`, 16000 Hz, 1280-sample frames). Client sends text
`{"type":"start","owner_opt_in":true}` (strictly `true`) -> `status`; then binary PCM chunks (any size up to
~2.5 s). Server sends `{"type":"wake","label","score"}` (no audio). `{"type":"stop"}`, a socket close, REST stop,
an audio stall (15 s), the time limit or the activation-storm guard all end listening and produce a `stopped`
message with `stop_reason`. Errors: `opt_in_required`, `unavailable`, `bad_audio`, `audio_before_start`,
`bad_message`, `unknown_type`, `message_too_large`.

Guards: loopback client address, no proxy-forwarding headers, Origin absent or same-origin on a loopback host
(blocks cross-site and DNS-rebinding), one connection at a time, REST endpoints loopback-only, arming possible
only through the WebSocket opt-in message. The `voice-wake` extra now also installs `websockets` (bare
`uvicorn` cannot serve WebSockets).

## 7. Owner verification (cannot be automated here)

See `docs/voice/OWNER_VERIFICATION.md` section G. `OWNER_VERIFICATION_REQUIRED`: real microphone detection,
false-accept/miss rates, Windows install of the `voice-wake` extra, CPU use on the laptop.
