# Mia — Self-Programming Personal AI

**Mia** is a local-first, general-purpose intelligent personal assistant built on the user's existing **10-layer Self-Programming Software (SPS) framework**.

Mia is designed to converse by voice and text, work with the computer through controlled tools, research and manage knowledge, execute multi-step tasks, and improve its own capabilities through a measurable, governed evolution process.

> **Project status:** Research and foundation bootstrap. This repository does not yet claim that the full assistant, autonomous runtime, computer-control suite, or 30-day experiment is complete.

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
| Memory | Persistent conversations, searchable local knowledge items, source provenance and trust labels |
| Autonomous work | Evidence-backed task proposals, live resource monitoring and a governed task lifecycle; autonomous dispatch remains gated |
| Self-programming | Discover gaps; reuse, adapt, compose, create, test, version and activate capabilities |
| Evolution observatory | Day 0 to Day N timeline, benchmark trends, code lineage, layer health and experiment reports |

## Quick start — foundation preview

Requirements: Python 3.11 or newer. Python 3.13 is supported by this starter foundation.

~~~powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,gateway,credentials]"
copy .env.example .env
copy config/applications.example.json config/applications.json
python -m mia
~~~

To enable local voice recognition, install the optional model runtime separately:

~~~powershell
python -m pip install -e ".[voice]"
~~~

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

The Evolution Observatory includes a **Capability Lab** where Mia can draft pure-Python source and tests through a free-only model route. Review the draft, save an immutable candidate, run the local Docker validation pipeline, and register only passing versions. Registered code remains inactive; no hot-load or rollback is implied. Recent conversations are stored locally and can be restored from the sidebar. The assistant's **Task Desk** lets you ask the model for reviewable task proposals, approve/reject them and track their state. A built-in CPU/RAM check demonstrates the manually approved, read-only task runner; free-form task descriptions are never interpreted as commands, and preview/mutation tools remain on their separate approval-card flow. Long-term notes and research documents can be added through the memory API; external content is tagged untrusted by default. See [Memory and Knowledge](docs/architecture/MEMORY_AND_KNOWLEDGE.md).\n\nThe Evolution Observatory can capture an immutable Day 0 baseline on the local Mia host after verifying a clean Git checkout, Ruff, and the full pytest suite. Starting the 30-day experiment is a separate manual action. With MIA_EXPERIMENT_AUTO_CAPTURE=true, Mia then checks for due daily snapshots while the local server is running, subject to CPU/RAM limits. The observatory displays actual SQLite events and metric samples; it does not generate sample growth data. The server binds to localhost by default.

## Development roadmap

| Phase | Outcome | Current status |
|---|---|---|
| 0. Research and reuse audit | Source audit, license matrix, architecture and integration decisions | In progress |
| 1. Foundation | Project skeleton, SPS registry, event store, observatory API, tests | Bootstrap committed |
| 2. AI gateway | Ollama/OpenAI-compatible adapters, free-first routing and price guard | In progress |
| 3. Voice | Push-to-talk, local STT and installed browser/OS speech voices | In progress |
| 4. Computer intelligence | Scoped filesystem, approved app launch and isolated public-web browser | In progress |
| 5. Memory and knowledge | Persistent conversations, FTS5 knowledge and source-aware document ingestion | In progress |
| 6. Autonomous agent | Task lifecycle, evidence-backed proposals and resource-gated dispatcher primitive | Foundation in progress |
| 7. Self-programming | Candidate versioning, lineage, validation/registration APIs; activation blocked | Foundation in progress |
| 8. 30-day experiment | Immutable baseline, daily snapshots, fixed benchmarks and final evaluation | Planned |

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
