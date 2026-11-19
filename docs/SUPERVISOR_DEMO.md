# Mia — Supervisor Demo Runbook

**Demo date:** 20 November 2026  
**Repository:** `muhammadnaumantahir/Mia`  
**URLs:** Assistant `http://127.0.0.1:8765/assistant` · Evolution Observatory `http://127.0.0.1:8765/`

This is a short, repeatable demonstration of the working Mia foundation. It is not a claim that every planned SPS capability is complete.

## 1. What to demonstrate

1. **Persistent, source-aware memory:** conversations and structured memories are stored locally; relevant earlier memories can be retrieved into a later chat independently of the model used.
2. **Governed task execution:** Mia creates a task proposal, shows the exact read-only tool plan, waits for owner approval, then executes a registered read-only system check.
3. **Evolution Observatory:** shows actual local resource readings, recorded events, tasks, capability lifecycle controls and experiment status.
4. **Self-programming foundation:** Capability Lab can draft, version and validate Python capability candidates through the configured provider and Docker sandbox; promotion is separate and owner-gated.

The three permanent product priorities remain equal: persistent identity/memory, autonomous digital life/proactive initiative, and governed self-programming. The demo proves parts of these foundations; it does not prove the complete future digital-companion runtime.

## 2. Prepare the Windows laptop

From the repository root:

```bat
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev,gateway,monitoring]"
copy .env.example .env
copy config\applications.example.json config\applications.json
```

If `.env` already exists, do not overwrite it. Review these settings for a safe supervisor demo:

- `MIA_HOST=127.0.0.1`
- `MIA_AUTONOMY_ENABLED=false`
- `MIA_EMERGENCY_STOP=false`
- `MIA_ALLOW_PAID=false`
- `MIA_ALLOW_REMOTE_FILE_CONTEXT=false`
- `MIA_EXPERIMENT_AUTO_CAPTURE=false` (recommended during the demo)

Do not add API keys to `.env` or any repository file. If using Ollama, start it separately and confirm at least one model is already installed. Mia's local provider status should show `online`. For a hosted/self-hosted provider, only use a route you have explicitly configured and checked for cost and data handling.

Hands-free Voice Conversation Mode is the main voice demo and uses Chrome/Edge browser speech recognition plus browser speech synthesis; it does not require the Faster-Whisper or local TTS extras. Browser speech recognition may use an external speech service and needs an available network. For a fully local speech-input fallback, run `.venv\\Scripts\\python.exe -m pip install -e ".[voice]"` and verify the model has finished downloading before the demo. Offline Windows voice output is optional via `.venv\\Scripts\\python.exe -m pip install -e ".[tts]"`. Test the optional local path before the demo rather than installing/downloading it during the presentation. Playwright and Docker capability validation are not demo prerequisites.

## 3. Start and preflight

Double-click `run_demo.cmd`, or run it from the repository root. Keep its terminal open.

In a second terminal, run:

```bat
.venv\Scripts\python.exe -m mia.supervisor_demo seed
.venv\Scripts\python.exe -m mia.supervisor_demo check
```

The seed command adds five fictional, clearly labelled `[MIA DEMO]` records (project, entity, decision, goal and episode) through Mia's own API. It is idempotent: repeating it does not duplicate these exact demo titles. It does not delete, replace or modify unrelated owner memories.

The check command verifies server reachability, AI provider availability, the demo records and the read-only resource tool. It prints a note reminding you that hands-free browser speech must be verified in Chrome/Edge on the actual demo laptop; it cannot prove browser microphone permission from the server. Optional local Whisper and OS TTS status are reported separately. Resolve any AI provider warning before relying on live chat. If the API is unreachable, confirm the server window is still running and the port is not already occupied.

Open both URLs above in the latest Chrome or Edge on the laptop that has the microphone and speakers. Keep the browser window at a readable zoom (90–100%). Close unrelated tabs and notifications. Turn off browser automatic translation. Allow microphone access for the local Mia page and verify the browser is not muted. Keep the laptop plugged in and test speaker volume.

## 4. Suggested 9–12 minute walkthrough

### Part A — Introduce the research idea (about 1 minute)

Say:

> Mia is a local-first personal AI assistant built around my ten-layer Self-Programming Software framework. The models are replaceable reasoning workers; identity, memory, governance and evidence belong to Mia's own runtime. The goal is not only to answer prompts, but to make software capabilities measurable, governed and evolvable.

Show the ten layers from `docs/architecture/SPS_LAYERS.md`. The coordinator and supporting runtime services do not add an eleventh SPS layer.

### Part B — Live voice conversation (about 2–3 minutes)

1. On the Assistant page, choose a voice from the **Speech voice** selector in the composer footer. Use **Test voice** to hear a sample, then choose **English** in the voice-language selector and press **Start voice**.
2. Allow microphone access. Wait until the status says **Listening**.
3. Say: **“Hi Mia. Please introduce yourself and explain what makes your memory independent of the AI model answering me.”**
4. Pause naturally. Mia should submit the transcript, answer aloud, and return to Listening after her speech finishes.
5. Ask a second question by voice: **“For the Atlas Learning Portal, what database decision was recorded? Explain why, and distinguish that from the database Mia uses for her own memory.”**
6. Stop the session by saying **“stop listening”** or pressing **End conversation**.

For Urdu, end the session, select **Urdu · Pakistan**, start again and speak a short sentence. If a suitable installed female voice is available, choose it in Speech voice and verify it with Test voice; the app only labels local voices as female when OS metadata reports female. Browser speech recognition language quality varies; rehearse the language you intend to use.

**Voice privacy and fallback:** this hands-free mode uses the browser's speech-recognition service and may send microphone audio outside Mia. Do not speak confidential information. If the browser does not support recognition or the service is unavailable, use **Local record** after installing the voice extra and completing the first model download; that fallback transcribes locally and lets you review the text before sending. Do not claim local offline recognition while using the browser mode.

### Part C — Persistent memory (about 2 minutes)

1. Open the Assistant page and point out the **Long-term memory** panel. Expand the `[MIA DEMO]` decision record so the supervisor can see the saved content and owner-provided provenance.
2. Turn **Tools** off for this chat so the UI uses the streaming response path and displays retrieved memory references.
3. Start a new conversation and ask:

   `For the Atlas Learning Portal, what database decision was recorded? Explain why, and distinguish that from the database Mia uses for her own memory.`

4. Show the `Memory used for this reply` source chip below Mia's answer. Explain that the reference came from the stored structured decision, not from the model's training or a hard-coded answer.

Expected content: PostgreSQL is the planned database for the fictional Atlas Learning Portal; Mia stores conversations and structured memories together in its local SQLite memory store, with observability in a separate local SQLite store. The project and all five seed records are fictional demo data.

### Part D — Governed computer/task work (about 2 minutes)

1. Open **Task Desk**.
2. Click **Safe CPU/RAM check**. The task should appear as a proposal.
3. Expand the task details and show the exact registered read-only tool (`system.read_resources`).
4. Click **Approve**, then **Run read-only check**.

Explain that natural-language task text is not executed as a shell command. This example reads resource telemetry only. File writes, app launches and browser clicks follow separate preview/approval paths.

### Part E — Evolution Observatory (about 2 minutes)

1. Open the Evolution Observatory.
2. Point out live CPU/RAM readings and the event ledger; the task and memory actions should have real recorded events.
3. Show the Capability Lab and explain its lifecycle: draft → immutable candidate → isolated Docker validation → registration → owner-gated promotion/rollback.
4. Show the 30-day experiment area, but do not claim that an experiment has run.

**Important:** Do not press **Capture & freeze Day 0** just to make the dashboard look populated. Day 0 runs repository checks on this actual computer and freezes a baseline; it should only be captured when the laptop is on the intended clean checkout and you genuinely want to start the experiment. Do not invent benchmark scores, growth charts or daily progress.

### Part F — Close (about 1 minute)

Say:

> The current build demonstrates persistent cross-session memory, source-aware retrieval, a governed read-only task path, computer-access approval boundaries and an observable capability-evolution pipeline. Full independent curiosity, self-directed research, reflection and proactive conversation initiation are still active development work. The next stage is to connect memory, autonomous digital life and self-programming into one evidence-backed runtime.

## 5. Live demo prompts

Use these exact prompts so the retrieved memory has distinctive terms to match:

**Memory retrieval**

`For the Atlas Learning Portal, what database decision was recorded? Explain why, and distinguish that from the database Mia uses for her own memory.`

**Memory provenance**

`Which saved Atlas Learning Portal record supports your answer, and what is its provenance?`

**Planning (optional; requires an online model)**

`Review the fictional Atlas Learning Portal demo scope and propose up to three low-risk, read-only review tasks. Do not execute anything.`

Do not use personal credentials, confidential work repositories, real student data or private files during the supervisor demo.

## 6. Honest implementation boundaries

- **Persistent memory:** local SQLite conversation/knowledge/structured records and bounded FTS5 contextual retrieval are implemented. Owner corrections preserve history and competing claims are surfaced. Automatic extraction, consolidation, semantic/relationship-aware retrieval and an owner-editable persisted identity remain incomplete.
- **Autonomous digital life:** not complete. There is a limited opt-in background dispatcher for previously approved read-only system checks, not an independent curiosity/research/reflection/social loop. Keep it disabled during this demo.
- **Self-programming:** candidate drafting, immutable versions, Docker validation and owner-gated activation/rollback records exist. A full permission broker and safe activation of permissioned plugins remain incomplete.
- **Voice:** the assistant now has an explicit hands-free browser voice session with utterance auto-send, spoken replies, automatic listen-again, English/Urdu language selection and a stop command. It depends on browser SpeechRecognition support and may use a server-based recognition service. Test it on the exact demo browser and microphone. Local Whisper remains a separate push-to-talk fallback; the first local model download and real Windows microphone/voice latency are not verified by repository tests.
- **Thirty-day experiment:** no real host baseline or completed 30-day evidence run exists yet. Dashboard activity is only what the local stores actually recorded.
- **Cross-platform CI:** latest GitHub Actions jobs have failed before runner allocation, so report local test evidence separately from successful CI. Do not describe the latest main commit as CI-verified.

Use `docs/STATUS.md` for the exact implementation and verification record.

## 7. Recovery plan

- **No AI provider online:** start Ollama and load a model already installed, or verify the configured provider service and credentials. Run the `check` command again. Do not enter a key on screen.
- **Chat is slow:** use the smallest suitable already-installed model and keep Tools off for streaming.
- **Hands-free voice unavailable:** check microphone permission, browser mute state, network and Chrome/Edge support. End the session and retry. If browser recognition remains unavailable, use Local record only if Faster-Whisper and its model are already installed; otherwise switch to text chat. Do not start installing/downloading a speech model in front of the supervisor.
- **Docker unavailable:** show the Capability Lab lifecycle and explain that isolated validation requires Docker; do not claim a validation succeeded.
- **Browser/network distractions:** keep the demo on localhost. Mia's server is designed to bind to localhost until authentication is implemented.
- **Unexpected issue:** switch to the prepared explanation and show the actual source record in the memory panel. Do not improvise fake experiment metrics.

## 8. After the demo

The `[MIA DEMO]` records remain in local memory so the walkthrough can be repeated. They are fictional examples, not real owner facts. To remove them later, use the individual record IDs with `DELETE /api/memory/records/{record_id}`; the seed utility intentionally has no destructive cleanup command.
