# Open-source research and code audit

**Audit date:** 2026-11-18  
**Purpose:** Identify mature components to reuse or integrate rather than rewriting existing work.  
**Important:** This is an initial repository-level audit. It is not a legal opinion, full dependency/SBOM review, or proof that every optional module is safe for Mia.

## Findings

### 1. OpenJarvis — highest-priority architecture and local-AI reference

- Repository: https://github.com/open-jarvis/OpenJarvis
- License observed: Apache-2.0.
- Architecture documentation describes five primitives: Intelligence/model catalog, Engine/inference backends, Agentic Logic, Memory, and Learning/trace-driven routing.
- Engine adapters include Ollama and cloud providers; memory includes SQLite/FTS5 plus optional retrieval backends; agent registry and container sandbox are documented.
- Current pyproject metadata specifies Python >=3.11 and <3.14; Python 3.13 therefore fits the declared range.
- Reuse decision: **Integrate selectively / study deeply.** Map its engine, registry, agent, memory and trace patterns into the existing ten SPS layers. Do not replace SPS with its five primitives and do not install all optional extras on the constrained target computer.
- Risks: broad dependency set and optional model/vector/agent extras can be heavy. Pin a known commit/tag after a local installation test.

Sources:
- https://github.com/open-jarvis/OpenJarvis
- https://github.com/open-jarvis/OpenJarvis/blob/main/docs/architecture/overview.md
- https://raw.githubusercontent.com/open-jarvis/OpenJarvis/main/pyproject.toml

### 2. FreeLLMAPI — highest-priority gateway candidate

- Repository: https://github.com/tashfeenahmed/freellmapi
- License observed: MIT.
- Current repository is a TypeScript/npm monorepo with shared, server, client and CLI workspaces. Server uses Express and supports an OpenAI-compatible API, provider adapters, key encryption, routing, quota/health tracking, fallbacks and dashboards.
- The inspected README advertises a hosted live-catalog service with a paid option; the self-hosted repository is MIT-licensed, but do not make Mia depend on the hosted paid catalog.
- Free-tier catalogs and model availability can change. Treat repository counts and throughput claims as maintainer-reported, time-sensitive figures, not guarantees.
- Reuse decision: **Integrate as an optional local sidecar after security and policy review.** Test the self-hosted mode, static/local catalog behavior, credential encryption, Windows deployment, localhost binding and provider terms before adoption.
- Important: upstream provider terms still apply to proxied requests. Never rotate identities/keys to evade quota or access restrictions.

Sources:
- https://github.com/tashfeenahmed/freellmapi
- https://github.com/tashfeenahmed/freellmapi/blob/main/docs/en/architecture/00-high-level-index.md
- https://github.com/tashfeenahmed/freellmapi/blob/main/docker-compose.yml

### 3. LiteLLM — alternative gateway and adapter reference

- Repository: https://github.com/BerriAI/litellm
- Use as a comparison candidate for provider coverage, routing and observability.
- Reuse decision: **Compare, do not install alongside FreeLLMAPI by default.** One gateway should be selected for the first production path to avoid duplicate routing, key storage and operational complexity.
- Verify the exact release license, security advisories, optional extras and compatibility before pinning.

Source: https://github.com/BerriAI/litellm

### 4. OpenHands Software Agent SDK — coding-agent runtime candidate

- Repository: https://github.com/OpenHands/software-agent-sdk
- License observed at repository root: MIT.
- Provides Python/TypeScript/REST interfaces and separates SDK, tools, workspaces and agent-server packages.
- Reuse decision: **Integrate or adapt selectively** for coding tasks and workspace lifecycle after sandbox requirements are met. Start with a narrow adapter; do not expose arbitrary workspace access to the main process.
- Risks: multi-package workspace and runtime dependencies may be too heavy for the target device. Test only the minimal package set.

Sources:
- https://github.com/OpenHands/software-agent-sdk
- https://github.com/OpenHands/software-agent-sdk/blob/main/README.md

### 5. Open Interpreter — computer/code execution reference

- Repository: https://github.com/openinterpreter/openinterpreter
- License observed: Apache-2.0.
- Use as a reference for user-directed computer/code workflows and model/tool interaction.
- Reuse decision: **Study and prototype behind an adapter.** Do not allow its execution interface to bypass Mia's own governance, audit and sandbox controls.

Source: https://github.com/openinterpreter/openinterpreter

### 6. 01 by Open Interpreter — voice/computer UX reference only

- Repository: https://github.com/openinterpreter/01
- License observed: AGPL-3.0.
- Its own README describes it as experimental and warns that it lacks basic safeguards.
- Reuse decision: **Study only for interaction ideas** until license and safety review. Do not copy its code into the Mia codebase at this stage.

Sources:
- https://github.com/openinterpreter/01
- https://github.com/openinterpreter/01/blob/main/README.md
- https://github.com/openinterpreter/01/blob/main/LICENSE

### 7. Faster-Whisper — speech-to-text candidate

- Repository: https://github.com/SYSTRAN/faster-whisper
- License observed: MIT.
- CTranslate2 implementation supports CPU execution and int8 quantization; upstream benchmarks show CPU usage, but their hardware differs from Mia's target machine.
- Reuse decision: **Adopt as an optional STT adapter after device benchmarking.** Test small models, CPU threads, memory and Urdu/English accuracy on the actual computer.

Source: https://github.com/SYSTRAN/faster-whisper

### 8. openWakeWord — wake-word candidate

- Repository: https://github.com/dscripka/openWakeWord
- License observed: Apache-2.0.
- Provides local wake-word detection and configurable model loading.
- Reuse decision: **Adopt as an optional adapter**, after measuring false positives, missed activations, Windows audio behavior and the selected wake-word model's license.

Source: https://github.com/dscripka/openWakeWord

### 9. Piper — local TTS candidate with copyleft constraints

- Repository: https://github.com/OHF-Voice/piper1-gpl
- License observed at repository root: GPL-3.0.
- Fast local neural TTS; the upstream documentation separately discusses voice licensing.
- Reuse decision: **Evaluate as an optional external engine only.** Check application distribution obligations and each voice model's license independently. Do not copy GPL source into Mia without a deliberate licensing decision.

Sources:
- https://github.com/OHF-Voice/piper1-gpl
- https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md
- https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/COMMERCIAL_USE.md

### 10. Playwright Python — browser automation

- Repository: https://github.com/microsoft/playwright-python
- License observed: Apache-2.0.
- Provides browser automation across Chromium, Firefox and WebKit and has a Windows-supported Python API.
- Reuse decision: **Adopt as the browser automation adapter**, with a dedicated browser profile, restricted downloads and domain/action policies.

Source: https://github.com/microsoft/playwright-python

## Audit rules

- Re-check repository commit, release, license and security advisories immediately before adding a dependency.
- Pin exact versions/commits and maintain a software bill of materials.
- Model weights, datasets, TTS voices and other downloaded assets require separate license checks.
- Avoid installing multiple full agent frameworks when a single adapter or package is sufficient.
- Record all modified upstream files and retain mandatory notices.


### 11. Kokoro-82M — local TTS candidate

- Model card: https://huggingface.co/hexgrad/Kokoro-82M
- License shown on the current model card: Apache-2.0 for the model weights.
- Python package repository: https://github.com/hexgrad/kokoro
- The current package metadata declares Python >=3.10 and <3.14, but includes PyTorch and Transformers dependencies. This is likely heavier than Mia's lightweight core.
- Reuse decision: **Evaluate as an optional TTS adapter** and benchmark CPU latency, memory, voice quality, pronunciation and voice-asset licensing. Do not install it in the core environment by default.
- The model's available languages/voices do not establish Urdu suitability; test the selected voice and language before promising Urdu speech.

Sources:
- https://huggingface.co/hexgrad/Kokoro-82M
- https://github.com/hexgrad/kokoro/blob/main/pyproject.toml
