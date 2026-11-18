# Upstream source review pins

**Reviewed:** 2026-11-18  
**Purpose:** Reproducibility for the initial Mia source-reuse audit. These are the exact commit refs inspected; they are not automatic dependency pins or endorsements.

| Upstream repository | Branch | Inspected commit | Commit date | Intended use in Mia |
|---|---|---|---|---|
| https://github.com/open-jarvis/OpenJarvis | main | c3ed63f1d2a2c0c1f7dabbc69050e1968a11dc10 | 2026-11-18 | Architecture and selective engine/agent/memory/trace integration reference |
| https://github.com/tashfeenahmed/freellmapi | main | 9d2b4b407b12e489ecd35320cd630d5ff97cd666 | 2026-11-17 | Self-hosted OpenAI-compatible gateway sidecar candidate |
| https://github.com/OpenHands/software-agent-sdk | main | 7dd877ee7a4eb813a65fc539394bf4022fc1cc23 | 2026-11-18 | Optional coding-agent and workspace integration candidate |
| https://github.com/openinterpreter/openinterpreter | main | 382f615b907279be1b6518156ac2754d3c235ff4 | 2026-11-15 | Computer/code workflow reference behind Mia policy and sandbox |
| https://github.com/openinterpreter/01 | main | befddaf205d0eb03020af4df13bf7d8f1b3f6003 | 2024-11-01 | Historical voice-interface UX study only; AGPL-3.0 and stale source |
| https://github.com/SYSTRAN/faster-whisper | master | 6cb639e70786aea42b9233006ae60eaf07190bdb | 2026-11-12 | Optional local speech-to-text adapter |
| https://github.com/dscripka/openWakeWord | main | 368c03716d1e92591906a84949bc477f3a834455 | 2025-12-30 | Optional wake-word adapter |
| https://github.com/OHF-Voice/piper1-gpl | main | 46c3c71f9155c4f5bfb887845f524433fd02febc | 2026-11-17 | External TTS candidate only; GPL-3.0 review required |
| https://github.com/microsoft/playwright-python | main | 16fdfef93ef06990fae3f85d2af87105f1dcdc90 | 2026-11-17 | Browser automation adapter candidate |

## Important interpretation

- A commit pin makes the audit repeatable; it does not guarantee security, compatibility or legal permission.
- Re-check the license file and relevant subcomponent/model/voice licenses at the pinned commit before copying or shipping code.
- Re-check security advisories and maintenance state before installing a dependency.
- For production integration, record the exact package release or git commit in the lockfile/SBOM.
- Upstream main and master branches may advance after this review.
- The 01 project was not selected as an active dependency due to its old inspected commit, AGPL-3.0 license and the safety warning in its README.