# Open-source integration matrix

Status values: Candidate = researched but not integrated; Approved for prototype = suitable for isolated proof of concept; Adopted = integrated and tested in Mia.

## Gateway architecture decision

Mia owns its AI gateway. **FreeLLMAPI and OmniRoute are not runtime dependencies, required sidecars, or control planes.** Gateway projects may be studied for isolated design patterns or narrowly reused code after exact-source, licence, security and dependency review. Such research must not make Mia dependent on those projects.

| Capability | Candidate/reference | Decision | License / status | Main checks before adoption |
|---|---|---|---|---|
| Gateway architecture and routing patterns | Bifrost, Portkey Gateway, LiteLLM, FreeLLMAPI, OmniRoute | Study selectively; implement Mia-owned contracts and services | Verify exact release and licence before any code reuse | Provider coverage, security advisories, dependency weight, Windows/Python compatibility, notices, tests |
| Direct cloud inference | Provider-native APIs and generic OpenAI-compatible endpoints | Build/maintain independent Mia adapters | Provider terms vary | Authentication, model listing, streaming, tool calling, multimodal support, usage, pricing, region and quota |
| Local inference | Ollama | Keep as independent local adapter | Verify installed version and upstream licence | Local-only operation, model discovery, CPU/RAM footprint |
| Model intelligence | Provider catalogues and model cards | Build Mia-owned registry with sourced, timestamped metadata | Model licence and API terms are distinct | Context, modalities, tool calling, prices, free-tier limits, deprecation and provenance |
| Coding agent and workspace tools | OpenHands SDK | Adapter candidate | MIT observed in initial audit | Minimum package set; sandbox boundary; resource usage |
| Computer/code task patterns | Open Interpreter | Study/adapt behind policy | Apache-2.0 observed in initial audit | Execution isolation; permission adapter |
| Voice/device interface | 01 | Study only | AGPL-3.0 observed in initial audit | Copyleft review; upstream README safety warning |
| Speech-to-text | Faster-Whisper | Optional local adapter | MIT observed in initial audit | CPU int8 benchmark; language accuracy; memory |
| Wake-word detection | openWakeWord (0.6.0) | **Approved for prototype:** optional adapter behind Mia's `WakeWordDetector` contract (`src/mia/voice/wake.py`); not a default dependency | Code Apache-2.0. **Pre-trained models CC BY-NC-SA 4.0 (non-commercial)** - never bundled or auto-downloaded; owner supplies a model via `MIA_WAKE_MODEL_PATH`. Verified 2026-11-21 (see [WAKE_WORD_PLAN](../voice/WAKE_WORD_PLAN.md)) | Linux + Python 3.13: upstream `tflite-runtime` pin makes `pip install openwakeword` fail (use `--no-deps` + onnxruntime); Windows uses ONNX. Still unmeasured: real-speech false-accept/miss rates, Windows install, an owner-trained "Hey Mia" model |
| Text-to-speech | Kokoro-82M | Primary candidate for local TTS proof of concept | Apache-2.0 model weights observed in initial audit | PyTorch/Transformers footprint; voice asset license; actual English/Urdu quality and CPU benchmark |
| Text-to-speech alternative | Piper | Optional external process candidate | GPL-3.0 observed in initial audit | Distribution obligations; each voice model license |
| Offline system TTS | pyttsx3 | Browser/OS system-voice fallback candidate | MPL-2.0 observed in initial audit | Target-device voice availability and SAPI behavior |
| PDF ingestion | pypdf | Optional parser | BSD-style observed in initial audit | Encrypted files, malformed PDFs, page and extraction limits |
| DOCX ingestion | python-docx | Optional parser | MIT observed in initial audit | Table extraction and embedded object limitations |
| XLSX ingestion | openpyxl | Optional parser | MIT observed in initial audit | Formula/data-only interpretation, large sheet limits |
| PPTX ingestion | python-pptx | Optional parser | MIT observed in initial audit | Shapes, tables and embedded-media limitations |
| Browser control | Playwright Python | Browser adapter candidate | Apache-2.0 observed in initial audit | Profile isolation; download controls; domain policy |

## Mia-owned gateway requirements

The gateway must own provider registration and lifecycle, model discovery and metadata, task classification, policy-aware routing, escalation, load balancing, health/circuit breakers, compliant retries/fallbacks, credentials, data-egress controls, token accounting, pricing/quota metadata, request/daily/monthly budgets, streaming/cancellation, optional semantic caching, multimodal capability negotiation, benchmarking, traces, metrics and owner controls.

See [Independent AI Gateway direction](../architecture/AI_GATEWAY_DIRECTION.md) for detailed architecture and acceptance criteria.

## Initial dependency strategy

1. Keep the Mia core small and importable without GPU libraries.
2. Add optional extras for voice and computer adapters.
3. Keep local Ollama usable without any cloud provider or gateway service.
4. Implement direct provider adapters and a generic OpenAI-compatible adapter behind Mia-owned contracts.
5. Do not install FreeLLMAPI or OmniRoute as Mia runtime components.
6. Study gateway projects comparatively; reuse only narrowly scoped components after exact licence/security review.
7. Record package version, upstream commit, licence, dependency tree and verification date in the SBOM.

## Decision status

The architecture decision is **Mia-owned gateway; no FreeLLMAPI/OmniRoute runtime dependency**. Individual gateway capabilities must still be marked implemented only after code and tests demonstrate them. Do not infer implementation from this document.
