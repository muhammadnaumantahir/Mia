# Owner-side voice verification (real devices)

Automated tests use fake microphones, a fake speech recogniser and a fake synthesiser. These steps need a human
with the real laptop. Record results in `docs/STATUS.md`.

Setup: Windows laptop, latest Chrome or Edge, Mia running at http://127.0.0.1:8765/assistant, microphone and
speakers working. Optional extras installed for sections C and D:
`python -m pip install -e ".[voice]"` and `python -m pip install -e ".[tts]"`.

## A. Browser engine, English (needs internet)
1. Engine = Browser, Input = Hands-free, Language = English. Press **Start voice**, allow the microphone.
2. Badge says "Mic on · browser speech service"; state shows Listening.
3. Say a question. Expect: "Hearing:" text, a reply in chat, the reply spoken, then Listening again.
4. While Mia is speaking press **Interrupt** (or Esc). Speech stops and she listens again.
5. Say "stop listening". Session ends, badge says "Mic off", state Stopped. Wait 10 s: the microphone must not
   reopen (check the browser tab's recording indicator).
6. Deny the permission once (browser site settings) and confirm a clear error appears.
7. Turn Wi-Fi off during a session: expect "Network connection lost" and a stopped session.

## B. Push-to-talk and mute
1. Input = Push-to-talk, Start voice: the tab must NOT show a recording indicator.
2. Hold **Hold to talk** (or Space) and speak; release. Expect submit on release and reply spoken.
3. Hands-free again: press **Mute mic** and speak; nothing is heard. Unmute resumes.

## C. Local engine (offline) - disconnect from the network for the test
1. Engine = Local Faster-Whisper. Press Start. First use downloads the model (needs internet once).
2. Speak English; check transcript accuracy and latency (note CPU, model size, seconds from end of speech to
   transcript). If too slow set `MIA_STT_MODEL=tiny`.
3. Choose a non-default microphone in **Microphone** and confirm that device is used.
4. Language = Urdu, speak a short Urdu sentence; record accuracy. Try Auto with a mixed English/Urdu sentence.
5. End the session; the recording indicator must disappear immediately.

## D. Speech output
1. Test voice with each local and browser voice; confirm audible output and Speed/Volume sliders.
2. Ask for a long answer (several paragraphs): it should be spoken in sequence without overlap, and Interrupt
   must stop it.
3. Urdu: note whether an Urdu voice exists on the laptop (Settings > Time & language > Speech). If none, Urdu
   replies fall back to whatever voice is selected and will sound wrong; this is a limitation, not a bug.

## E. Echo / barge-in (optional)
With headphones, enable **Interrupt by voice**, let Mia start a long answer, then speak: she should stop and take
your sentence as the next turn. Without headphones expect self-interruption; leave the option off.


## F. Opt-in Hey Mia wake phrase (browser engine only)

1. Engine = Browser, Input = Hands-free. Press **Enable Hey Mia** and allow microphone access.
2. Confirm the visible state says **Listening for 'Hey Mia'** and the Mic on badge remains visible while armed.
3. Say an unrelated sentence without the wake phrase. Mia must not submit it to chat or speak a reply.
4. Say **“Hey Mia”** followed by a question. Mia should remove the wake phrase, submit only the question, answer, and re-arm for the next wake phrase.
5. Select Urdu (Pakistan), enable wake phrase, say “Hey Mia”, then speak a short Urdu sentence. Confirm the recognition language switches to Urdu after activation.
6. Press **Mute mic**; recognition must stop and the state must show Muted. Unmute should return to waiting for “Hey Mia”, not continue the previous active turn.
7. Press **Disable Hey Mia** or **End conversation**. Confirm Mic off and that no recognition session restarts.
8. Confirm the browser's recording indicator and permission state match Mia's visible Mic badge. Wake phrase mode uses the browser speech service and is **not offline local wake-word detection**.


## G. Offline wake-word engine (OWNER_VERIFICATION_REQUIRED - not usable from the UI yet)

The detector core exists but no audio transport/UI is wired, so these checks are **prerequisites for MIA-VOICE-02**
and cannot yet be done end to end. Do them when convenient; none blocks development.

1. On Windows: `python -m pip install -e ".[voice-wake]"`. Record whether it installs on your Python version.
2. Decide the model: the upstream pre-trained models are CC BY-NC-SA 4.0 (non-commercial). For personal use you may
   point `MIA_WAKE_MODEL_PATH` at one; for anything else train your own "Hey Mia" model (see WAKE_WORD_PLAN).
3. Run `GET http://127.0.0.1:8765/api/voice/status`: `offline_wake_word.engine_installed` and `model_configured`
   should become true. `operational` must still be false until the transport task lands.
4. Once the transport exists: confirm the Listening badge, the Stop control, the automatic stop after the configured
   time, that unrelated speech/TV does not activate, and note false accepts per hour and missed wakes.
