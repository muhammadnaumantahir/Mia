# Mia — Self-Programming Personal AI

**Mia** is a local-first, general-purpose intelligent personal AI companion built on the user's existing **10-layer Self-Programming Software (SPS) framework**.

**First goal:** the owner can talk naturally with Mia, discuss ideas and personal topics, ask Mia about itself, and continue conversations with reliable memory and context in text and voice. The longer-term destination is a Jarvis-style personal AI operating system that helps with daily life, research, projects and files, controls the computer safely, and improves its own capabilities under governance.

## Product principles

- **Conversation first:** complete a genuinely useful, context-aware, voice-enabled companion before expanding computer control.
- **Free/open-source first:** No paid API, voice service, hosted server, or subscription is required to start the core application.
- **Local-first, cloud-capable:** Use local models where practical; connect user-authorized provider keys through Mia's replaceable, independently owned gateway.
- **Reuse before rewrite:** Audit existing projects, licenses, dependencies, interfaces, tests, and security before implementing a feature.
- **SPS remains the framework:** Preserve the ten canonical layers. Do not invent an eleventh SPS layer.
- **Observable evolution:** Every proposed, tested, rejected, activated, or rolled-back change must leave an auditable trace.
- **Safe autonomy:** Generated code is never executed inside the privileged assistant process. Use isolated workspaces, explicit capability contracts, validation, and rollback.
- **Evidence over appearance:** Never report a capability, score, or improvement unless it is backed by a real test or recorded event.

## The ten SPS layers

1. Software DNA Core
2. Governance Core
3. Cognitive Core
4. Knowledge Core
5. Experience Core
6. Meta-Learning Core
7. Adaptation Core
8. Evolution Core
9. Verification & Validation Core
10. Execution Core

The coordinator/runtime connects these existing layers; it is not an additional layer. See [SPS Layer Contract](docs/architecture/SPS_LAYERS.md).

## Target capabilities

| Domain | Intended capability |
|---|---|
| Conversation and identity | Natural multi-turn text and voice conversation; truthful Mia self-description; continuity across sessions, projects and model/provider changes |
| Voice | Streaming STT/TTS, English/Urdu/Roman Urdu, interruption/barge-in, hands-free operation and always-on wake word with visible listening and immediate stop controls |
| AI Brain | Model-independent reasoning pipeline: intent, context, routing, planning, permission-gated tools, answer verification, self-correction and diagnostics (see [AI Brain](docs/architecture/AI_BRAIN.md)) |
| AI gateway | Independent Mia-owned gateway: local and direct cloud providers, model discovery, intelligent routing, escalation, resilience, security, budgets, parallel evaluation and observability |
| Memory and knowledge | Persistent, source-aware identity, conversations, decisions, goals, experiences and contextual/semantic retrieval across sessions and model/provider changes |
| Immersive interaction | Full 3D holographic interface that visualizes real conversation/runtime state and retains accessible non-3D fallback |
| Personal digital life | Daily Briefing, Task Manager, Project Workspace, File Intelligence, Browser Research, Workflow Automation, notifications, Knowledge Graph, backup/restore and resource dashboard |
| Computer operations | Browser, applications, files, documents and authorized OS workflows with a central permission broker, sandbox, audit, cancellation and recovery |
| Self-programming | Capability-gap discovery, code generation, validation, version lineage, permission-scoped activation, rollback and research into custom foundation models and automatic self-modification |
| Mia Code Graph | Live source-evidence graph of packages, files, symbols, dependencies, calls, APIs, tests, permissions and SPS layers, incrementally updated as code changes |
| Evolution observatory | Real Day 0 to Day N evidence, benchmark trends, capability attribution, before/after comparisons and experiment reports |

See [the implementation roadmap](docs/ROADMAP.md) for the mandatory conversation-first sequence and each milestone's acceptance criteria. See [Proactive Digital Companion Runtime](docs/architecture/DIGITAL_PERSON_RUNTIME.md) for bounded autonomy architecture; it is a design target and must not be mistaken for implemented functionality.

## Quick start — foundation preview

Requirements: Python 3.11 or newer. Python 3.13 is supported by this starter foundation.

~~~powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,gateway,credentials,monitoring]"
copy .env.example .env
copy config/applications.example.json config/applications.json
python -m mia
~~~

To enable local voice recognition, install the optional model runtime separately:

~~~powershell
python -m pip install -e ".[voice]"
~~~

To enable optional offline text-to-speech with installed Windows system voices:

~~~powershell
python -m pip install -e ".[tts]"
~~~

The assistant prefers an installed voice explicitly marked female by system metadata when one is available. Otherwise, choose an installed local or browser voice from the selector. This does not install voice packs or guarantee that a feminine voice is present.

For PDF, DOCX, XLSX and PPTX extraction, install the optional document parsers:

~~~powershell
python -m pip install -e ".[documents]"
~~~

For isolated browser automation, install Playwright and its Chromium runtime:

~~~powershell
python -m pip install -e ".[computer]"
playwright install chromium
~~~

The first transcription downloads the selected Faster-Whisper model into the local Mia data directory. The default is the multilingual base model, CPU int8, and two worker threads. The local engine can be used hands-free or push-to-talk from the Assistant page; no background microphone listener is started and the microphone is released when a session ends.

Computer file access defaults to Mia's own workspace folder. The optional computer extra includes Playwright and psutil for browser automation and CPU/RAM inspection. To grant additional roots, add a semicolon-separated list to .env, for example MIA_COMPUTER_ROOTS=D:/Projects;D:/Documents. File writes require a preview, explicit approval and an exact payload match. Copy config/applications.example.json to config/applications.json to enable the Notepad/Calculator/File Explorer allowlist. Browser navigation uses an isolated profile; clicks need approval. See [Computer Access](docs/architecture/COMPUTER_ACCESS.md).

Open Mia at http://127.0.0.1:8765/assistant and the Evolution Observatory at http://127.0.0.1:8765/. The Assistant page includes a hands-free browser voice conversation mode; press Start, speak, pause for Mia to reply aloud, and press End or say “stop listening”. Browser recognition may use an external service and is not offline; choose the Local Faster-Whisper engine (optional voice extra) for offline recognition. Esc or Interrupt stops Mia mid-answer; see docs/architecture/VOICE.md and docs/voice/OWNER_VERIFICATION.md.

For the supervisor walkthrough, see the [Supervisor Demo Runbook](docs/SUPERVISOR_DEMO.md). On Windows, run_demo.cmd starts the local app after setup; then run python -m mia.supervisor_demo seed and python -m mia.supervisor_demo check in a second terminal to add clearly labelled fictional demo memories and verify the local demo prerequisites.

For local AI, start Ollama and make sure a model is installed. Mia discovers local models through Ollama's native API. The optional gateway extra enables HTTP adapters; no cloud key is needed for the local path.

Mia's gateway is an independent, Mia-owned service. It does not require FreeLLMAPI, OmniRoute, or another external gateway. See [Independent AI Gateway direction](docs/architecture/AI_GATEWAY_DIRECTION.md) for the owner-approved architecture and implementation requirements.

For local CPU/RAM telemetry and automatic daily experiment capture, install the optional monitoring extra. The default scheduler is off unless MIA_EXPERIMENT_AUTO_CAPTURE=true is present in .env.

The Evolution Observatory includes a **Capability Lab** where Mia can draft pure-Python source and tests through a free-only model route. Review the draft, save an immutable candidate, run the local Docker validation pipeline, and register only passing versions. Owner-approved promotion and rollback records are supported for registered permission-free versions when MIA_CAPABILITY_ACTIVATION_ENABLED is enabled; the lifecycle enforces one active version per capability and records the decision. This does not hot-load code into the privileged Mia process. A complete permission broker and target-Windows verification remain open.

The Evolution Observatory can capture an immutable Day 0 baseline on the local Mia host after verifying a clean Git checkout, Ruff, and the full pytest suite. Starting the 30-day experiment is a separate manual action. With MIA_EXPERIMENT_AUTO_CAPTURE=true, Mia then checks for due daily snapshots while the local server is running, subject to CPU/RAM limits. The observatory displays actual SQLite events and metric samples; it does not generate sample growth data. The server binds to localhost by default.

## Development roadmap

| Priority | Outcome | Execution rule |
|---|---|---|
| 0. Integration baseline | Inspect PR/source state, resolve application integration defects, retain Mia-owned gateway, initialize/update Code Graph | Do this first; CI infrastructure must not block |
| 1. Conversation-first Mia | Self-identity, Brain/reasoning, context and persistent memory, independent gateway, natural multilingual voice, wake word and bounded parallel judges | **First product milestone; complete before computer-control expansion** |
| 2. Immersive interaction | Full 3D holographic UI and multimodal experience | Start after Priority 1 development criteria |
| 3. Personal digital life | Daily briefing, tasks, projects, files, research, workflows, notifications, knowledge graph, backup, dashboard, remote/multi-device access | Start after Priority 2 |
| 4. Computer intelligence | Permission broker, sandboxed browser/desktop/filesystem control, approvals, audit, cancellation and recovery | Start after Priority 3 |
| 5. Self-programming and model evolution | Governed capability evolution, custom foundation model research, automatic self-modification research objective | Start after Priority 4 |
| 6. Evolution Observatory | Real growth metrics, benchmark attribution, before/after reports and 30-day experiment infrastructure | Start after Priority 5 |

Phases are quality gates, not calendar promises. AI coding agents must always select the highest-priority unfinished development item from [the full roadmap](docs/ROADMAP.md), continue without waiting for "continue", and update [project status](docs/STATUS.md). CI is optional and does not gate development. Owner-only device/manual checks are tracked separately as OWNER_VERIFICATION_REQUIRED.

## Open-source reuse strategy

Mia will not blindly copy whole repositories. Each candidate is assessed as one of:

- **Adopt:** use a maintained package or service as-is.
- **Integrate:** connect through a stable API or subprocess boundary.
- **Adapt:** reuse a specific component with an explicit upstream and license record.
- **Study:** use architecture and interaction ideas without copying code.
- **Reject/defer:** security, maintenance, licensing, hardware, or compatibility makes it unsuitable.

The first audit covers OpenJarvis, LiteLLM, OpenHands Software Agent SDK, Open Interpreter, 01, Faster-Whisper, openWakeWord, Piper and Playwright. See [Open-source audit](docs/research/OPEN_SOURCE_AUDIT.md), [integration matrix](docs/research/INTEGRATION_MATRIX.md), and the [Independent AI Gateway direction](docs/architecture/AI_GATEWAY_DIRECTION.md).

## Security and privacy baseline

Mia can be designed for broad computer assistance, but broad capability is not blanket permission to perform every action autonomously. Destructive operations, credential access, external communication, purchases, financial actions, security changes, and deployment require policy checks and, where configured, user approval. Secrets are never committed, logged, or passed to generated code. Always-on microphone mode must be clearly indicated and immediately stoppable.

See [Safety and Governance](docs/SAFETY_AND_GOVERNANCE.md).

## Repository map

~~~text
docs/                  Research, architecture, phases, experiment protocol and supervisor demo runbook
src/mia/               Python application, SPS foundations and demo preflight utility
src/mia/agent/         Task lifecycle, workload discovery and autonomy gates
src/mia/evolution/     Versioned capability persistence and validation lifecycle
src/mia/gateway/       Provider contracts and routing policy
src/mia/observatory/   SQLite event/metric store and dashboard
tests/                 Automated tests
run_demo.cmd           Windows local demo launcher
config/                Safe non-secret defaults
workspace/             Runtime-created generated workspaces (gitignored)
experiment/            Runtime-created experiment data (gitignored)
~~~

## Contributing / AI coding agents

Read AGENTS.md and always follow the conversation-first order in docs/ROADMAP.md. Mia supports a six-specialist-agent workflow coordinated through [the shared task board](docs/AI_TASK_BOARD.md), [agent registry](docs/AI_AGENT_REGISTRY.md), [development rules](docs/AI_DEVELOPMENT_SYSTEM.md), and [master/specialist prompts](prompts/AI_MASTER_AGENT.md). All agents commit directly to main; parallelize disjoint work and serialize shared-file writes/pushes. Keep implementation modular, add tests, update the source/license register when dependencies or upstream code change, and never mark a capability complete based on documentation alone.

## License

No project-wide license is declared yet. The project is completing an upstream dependency and code-reuse audit first. Do not assume that every component or model used by Mia has the same license.
