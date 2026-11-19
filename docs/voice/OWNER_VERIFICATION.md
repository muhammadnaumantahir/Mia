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
