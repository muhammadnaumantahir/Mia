# Open-source integration matrix

Status values: Candidate = researched but not integrated; Approved for prototype = suitable for isolated proof of concept; Adopted = integrated and tested in Mia.

| Capability | Candidate | Decision | License observed | Main checks before adoption |
|---|---|---|---|---|
| Local AI, model/engine registry, traces | OpenJarvis | Selective adapter/integration candidate | Apache-2.0 | Pin commit; minimal extras; Windows + Python 3.13; memory/CPU |
| Multi-provider gateway | FreeLLMAPI | Optional self-hosted sidecar candidate | MIT | Paid catalog independence; provider ToS; key encryption; Windows/Node/Docker |
| Alternative gateway | LiteLLM | Comparison only | Verify exact release | Avoid running two gateways; security advisories; dependency weight |
| Coding agent and workspace tools | OpenHands SDK | Adapter candidate | MIT | Minimum package set; sandbox boundary; resource usage |
| Computer/code task patterns | Open Interpreter | Study/adapt behind policy | Apache-2.0 | Execution isolation; permission adapter |
| Voice/device interface | 01 | Study only | AGPL-3.0 | Copyleft review; upstream README safety warning |
| Speech-to-text | Faster-Whisper | Optional local adapter | MIT | CPU int8 benchmark; language accuracy; memory |
| Wake-word detection | openWakeWord | Optional local adapter | Apache-2.0 | Model license; false-positive and missed-wake test |
| Text-to-speech | Piper | Optional external process candidate | GPL-3.0 | Distribution obligations; each voice model license |
| Browser control | Playwright Python | Browser adapter candidate | Apache-2.0 | Profile isolation; download controls; domain policy |

## Initial dependency strategy

1. Keep the Mia core small and importable without GPU libraries.
2. Add optional extras for voice and computer adapters.
3. Integrate one gateway only after comparison; FreeLLMAPI is the first candidate because it is already familiar to the owner.
4. Keep provider adapters replaceable through a shared Mia model interface.
5. Do not add 01 or Piper source code to Mia until the copyleft implications are explicitly accepted.
6. Record package version, upstream commit, license, dependency tree and verification date in the SBOM.

## Decision status

Nothing in this table is marked Adopted yet. The initial Mia repository contains only its own contracts and observability foundation. Upstream components will be integrated only after local tests and security checks.
