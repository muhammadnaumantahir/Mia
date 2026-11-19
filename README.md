# Mia — Self-Programming Personal AI

**Mia** is a local-first, general-purpose intelligent personal assistant and proactive digital companion built on the user's existing **10-layer Self-Programming Software (SPS) framework**.

Mia is intended to maintain one persistent identity across conversations and model changes, explore permitted topics and projects between conversations, reflect on evidence and outcomes, initiate useful and well-timed conversations, work with the computer through controlled tools, and improve her capabilities through measurable, governed evolution.

Three co-equal core priorities guide development: **persistent identity and memory**, **autonomous digital life and proactive initiative**, and **self-programming with governed evolution**. These are product priorities implemented through the existing ten SPS layers, not additional SPS layers.

> **Project status:** Active implementation, not release-ready. The repository does not claim that full multi-step autonomy, unrestricted computer control, safe capability activation, or a completed 30-day experiment is ready.

## Product principles

- **Free/open-source first:** No paid API, voice service, hosted server, or subscription is required to start the core application.
- **Local-first, cloud-capable:** Use local models where practical; connect user-authorized provider keys through a replaceable gateway.
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
| Conversation | Context-aware text and natural voice interaction |
| AI gateway | Local models, multiple providers, model discovery, routing, fallbacks, usage and budget controls |
| Computer operations | Browser, applications, files, documents and authorized OS workflows |
| Research | Web research, repository inspection, PDFs, technical documentation and citations |
| Identity and memory | Persistent, source-aware identity, conversations, decisions, goals, experiences and contextual retrieval across sessions and model/provider changes |
| Proactive digital companion | Curiosity backlog, independent bounded exploration, reflection, follow-ups and relevant conversation initiation with quiet-hour and frequency controls; target behaviour, not yet complete |
| Autonomous work | Evidence-backed task proposals, live resource monitoring and a governed task lifecycle; broad proactive autonomy remains gated |
| Self-programming | Discover gaps; reuse, adapt, compose, create, test, version and activate capabilities |
| Evolution observatory | Day 0 to Day N timeline, benchmark trends, code lineage, layer health and experiment reports |

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

The first transcription downloads the selected Faster-Whisper model into the local Mia data directory. The default is the multilingual `base` model, CPU int8, and two worker threads. Voice recognition is push-to-talk; no background microphone listener is started.

Computer file access defaults to Mia's own `workspace` folder. The optional computer extra includes Playwright and psutil for browser automation and CPU/RAM inspection. To grant additional roots, add a semicolon-separated list to `.env`, for example `MIA_COMPUTER_ROOTS=D:/Projects;D:/Documents`. File writes require a preview, explicit approval and an exact payload match. Copy `config/applications.example.json` to `config/applications.json` to enable the Notepad/Calculator/File Explorer allowlist. Browser navigation uses an isolated profile; clicks need approval. See [Computer Access](docs/architecture/COMPUTER_ACCESS.md).

Open Mia at http://127.0.0.1:8765/assistant and the Evolution Observatory at http://127.0.0.1:8765/.

For local AI, start Ollama and make sure a model is installed. Mia discovers local models through Ollama's native API. The optional gateway extra enables HTTP adapters; no cloud key is needed for the local path.

To connect an already-running **self-hosted** FreeLLMAPI at its default localhost endpoint:
1. Save its unified key in the Windows credential manager: `mia-credentials set freellmapi`.
2. Set `MIA_FREELLMAPI_ENABLED=true` in `.env`.
3. Keep `MIA_FREELLMAPI_ROUTE_FREE=false` until you have checked the gateway's active model chain and confirmed all enabled routes are free tiers you are allowed to use.
4. Restart Mia. Its key is retrieved from the OS credential store and is never put in SQLite or logged.

For local CPU/RAM telemetry and automatic daily experiment capture, install the optional `monitoring` extra. The default scheduler is off unless `MIA_EXPERIMENT_AUTO_CAPTURE=true` is present in `.env`.

The Evolution Observatory includes a **Capability Lab** where Mia can draft pure-Python source and tests through a free-only model route. Review the draft, save an immutable candidate, run the local Docker validation pipeline, and register only passing versions. Owner-approved promotion and rollback records are supported for registered permission-free versions when MIA_CAPABILITY_ACTIVATION_ENABLED is enabled; the lifecycle enforces one active version per capability and records the decision. This does not hot-load code into the privileged Mia process. A complete permission broker and target-Windows verification remain open.

The Evolution Observatory can capture an immutable Day 0 baseline on the local Mia host after verifying a clean Git checkout, Ruff, and the full pytest suite. Starting the 30-day experiment is a separate manual action. With MIA_EXPERIMENT_AUTO_CAPTURE=true, Mia then checks for due daily snapshots while the local server is running, subject to CPU/RAM limits. The observatory displays actual SQLite events and metric samples; it does not generate sample growth data. The server binds to localhost by default.

## Development roadmap

| Phase | Outcome | Current status |
|---|---|---|
| 0. Research and reuse audit | Source audit, license matrix, architecture and integration decisions | In progress |
| 1. Foundation | Project skeleton, SPS registry, event store, observatory API, tests | Bootstrap committed |
| 2. AI gateway | Ollama/OpenAI-compatible adapters, free-first routing and price guard | In progress |
| 3. Voice | Push-to-talk, local STT, optional offline OS TTS and browser fallback | Partial implementation; real Windows voice metadata/latency and wake-word tests remain |
| 4. Computer intelligence | Scoped filesystem, approved app launch and isolated public-web browser | In progress |
| 5. Memory and knowledge | Persistent conversations, identity continuity, structured episodic/decision memory, contextual retrieval and source-aware document ingestion | In progress; knowledge retrieval is not yet automatically injected into chat context, and consolidation/structured memory remain open |
| 6. Autonomous digital life and agent runtime | Durable scheduler/queue, curiosity-led bounded exploration, reflection, proactive conversation and governed task execution | Partial foundation only; current background dispatch is limited to approved read-only system checks. Curiosity/social initiative, quiet-hour delivery policy, broader workflows, safe restart continuation and target-device verification remain open |
| 7. Self-programming | Capability discovery, candidate versioning/lineage, sandbox validation, owner-gated promotion and rollback | Foundation in progress; permission broker, safe plugin execution boundary and Windows verification remain open |
| 8. 30-day experiment | Immutable Day 0, explicit start, host-verified daily snapshots and optional resource-aware scheduler | Report API/viewer and terminal incomplete handling are implemented; owner-host baseline, real 30-day run and evidence-backed final results remain pending |

Phases are quality gates, not calendar promises. A phase is not complete until its documented exit criteria and tests pass. See [the complete roadmap](docs/ROADMAP.md).

## Open-source reuse strategy

Mia will not blindly copy whole repositories. Each candidate is assessed as one of:

- **Adopt:** use a maintained package or service as-is.
- **Integrate:** connect through a stable API or subprocess boundary.
- **Adapt:** reuse a specific component with an explicit upstream and license record.
- **Study:** use architecture and interaction ideas without copying code.
- **Reject/defer:** security, maintenance, licensing, hardware, or compatibility makes it unsuitable.

The first audit covers OpenJarvis, FreeLLMAPI, LiteLLM, OpenHands Software Agent SDK, Open Interpreter, 01, Faster-Whisper, openWakeWord, Piper and Playwright. See [Open-source audit](docs/research/OPEN_SOURCE_AUDIT.md) and [integration matrix](docs/research/INTEGRATION_MATRIX.md).

## Security and privacy baseline

Mia can be designed for broad computer assistance, but broad capability is not blanket permission to perform every action autonomously. Destructive operations, credential access, external communication, purchases, financial actions, security changes, and deployment require policy checks and, where configured, user approval. Secrets are never committed, logged, or passed to generated code.

See [Safety and Governance](docs/SAFETY_AND_GOVERNANCE.md).

## Repository map

~~~text
docs/                  Research, architecture, phases and experiment protocol
src/mia/               Python application and SPS foundations
src/mia/agent/         Task lifecycle, workload discovery and autonomy gates
src/mia/evolution/    Versioned capability persistence and validation lifecycle
src/mia/gateway/       Provider contracts and routing policy
src/mia/observatory/   SQLite event/metric store and dashboard
tests/                 Automated tests
config/                Safe non-secret defaults
workspace/             Runtime-created generated workspaces (gitignored)
experiment/            Runtime-created experiment data (gitignored)
~~~

## Contributing / AI coding agents

Read [AGENTS.md](AGENTS.md) before making changes. Keep implementation modular, add tests, update the source/license register when dependencies or upstream code change, and never mark a roadmap phase complete based on documentation alone.

## License

No project-wide license is declared yet. The project is completing an upstream dependency and code-reuse audit first. Do not assume that every component or model used by Mia has the same license.
