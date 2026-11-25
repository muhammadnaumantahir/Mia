# Custom foundation-model feasibility study (MIA-EVOL-08)

**Author:** A3 (Claude Sonnet 5.5 session) · **Written:** 2026-11-24 · **Status:** study and recommendation only.
**Not done:** no model was downloaded, trained or run, no money was spent, and no hardware was measured. Figures from outside the repo come from public web pages read on 2026-11-24 (listed at the end), many of them blogs or vendor pages; they are ranges to re-check before any spending, not guarantees.

## 1. Recommendation (go / no-go)

| Option | Decision | One-line reason |
|---|---|---|
| A. Keep using open-weight models through Ollama, improve Mia around them (memory, tools, evaluation) | **GO** | No new data, hardware or spend needed; the roadmap itself says start here. |
| B. Small adapter (LoRA/QLoRA) on an open-weight model | **NO-GO today, CONDITIONAL GO once five gates pass (section 5)** | Technically cheap, but Mia has no lawful training set, no held-out evaluation strong enough to detect a benefit, and unknown owner hardware. |
| C. Full fine-tuning of a 7B+ model | **NO-GO** | Needs far more memory than a typical single consumer GPU, and the same data and evaluation gaps as B, with more risk. |
| D. Pretraining a model from scratch | **NO-GO** | Hundreds to millions of dollars of compute and trillions of tokens of data for a model that would be weaker than ones Mia can already run. |
| E. Rented cloud fine-tuning | **NO-GO for private data; possible only for non-private, licensed data** | Training on the owner's conversations would send them to a provider. |

This study makes no claim that fine-tuning would improve Mia. It says only whether trying is responsible and affordable, and what has to be true first.

## 2. What Mia actually has today (repo evidence)

- **No training pipeline, no training-data policy and no consent flow for training.** A search of `src/` and `docs/` finds fine-tuning only in the roadmap and backlog text. EVOL-01's gap store (A1) has per-source consent for *discovering capability gaps*, which is not consent to train a model on the same text.
- **Models run locally through Ollama** (`MIA_OLLAMA_BASE_URL`, default `http://127.0.0.1:11434`) plus optional configured providers. Which models the owner runs is not recorded anywhere in the repo, so base-model choice, licence and hardware cannot be settled here.
- **Evaluation cannot yet detect a model improvement.** The 31 scripted scenarios use fake providers and say nothing about model quality (stated in their own output). The live harness (`mia.brain.live_eval`) has 5 probes per model. Section 4 shows why that cannot support any before/after claim.
- **Dataset size is unknown.** The owner's real conversation and memory volume is not in the repo, and most of it is private and may quote third parties.
- **Rollback and lineage** exist for capabilities (versions, rollback, lineage), not for model weights or adapters.

## 3. Options in detail

### A. Open-weight inference (status quo plus better scaffolding)
- Costs nothing extra. Licence conditions are those of whichever models the owner already runs.
- Best fit with the roadmap: "Start with open-weight/cloud-hosted models ... do not make expensive pretraining a prerequisite for Mia's usefulness."

### B. LoRA / QLoRA adapters
- **Hardware (from Unsloth's documentation and tutorials, 2026):** QLoRA of a ~3B model needs roughly 3.5 GB of GPU memory at the absolute minimum; an 8B model roughly 8 GB in typical guides (one measured run reports 6.6 GB peak at a 2048-token sequence on an RTX 4090). 16-bit LoRA of 8B is about 18 GB. Unsloth's documentation lists NVIDIA GPUs on Linux/WSL and on Windows; other hardware (AMD, CPU-only) needs its own check, and the owner's GPU is unknown.
- **Time and cost:** tutorials report about 30 minutes (3B) to 2 to 4 hours (8B) for ~1,000 examples over 3 epochs on a consumer GPU. Arithmetic with rented RTX 4090 prices of about $0.30 to $0.90 per hour: one run is roughly $0.6 to $3.6, so ten iterations are under about $40. These are order-of-magnitude figures, not quotes.
- **Deployment fit:** Ollama can apply a LoRA adapter on top of a base model (`ADAPTER` in a Modelfile), but its documentation lists Llama, Mistral and Gemma families for adapters (the copies read here may lag) and says the adapter must match the exact base model and that non-quantized adapters work best. If the owner's models are another family, check support first.
- **Risks:** a tuned adapter can lose general ability or safety behaviour; benefits are specific to style, format and domain, while facts are usually better served by Mia's memory and retrieval. A change in behaviour is not an improvement until measured on held-out data.

### C. Full fine-tuning
- One 2026 guide puts full fine-tuning of a 7B model at 48 GB of GPU memory or more (workstation or multi-GPU class). Updating every weight also makes rollback and safety regression testing heavier. No benefit over B for Mia's likely data volume is evidenced.

### D. Pretraining
- Published data points: TinyLlama (1.1B parameters, 3 trillion tokens) took about 90 days on 16 A100-40GB GPUs, 3,456 A100 GPU-hours per 300 billion tokens; one secondary source estimated about $140k at AWS p4d prices. A "compute-optimal" 1.1B run (about 22 billion tokens) was reported as 32 hours on 8 A100s.
- Arithmetic with A100 80GB rental of about $0.70 to $3.50 per hour (list prices vary widely by provider):
  - 1.1B on ~22B tokens: 256 A100-hours, about $180 to $900. Cheap, but the result is far weaker than current open models and needs ~22B tokens of data.
  - 1.1B on 3T tokens: about 34,560 A100-hours, about $24k to $121k.
  - An 8B model on ~15T tokens (the scale cited for Llama 3) scales to roughly 1.3 million A100-hours, about $0.9M to $4.4M. This is a rough proportional scaling of TinyLlama's throughput and is only meant to show the order of magnitude.
- Mia's own data is megabytes against needs of billions of tokens. Pretraining is not feasible or useful.

### E. Cloud fine-tuning services and rented GPUs
- GPU rental in September to October 2026: RTX 4090 about $0.30 to $0.90 per hour, A100 80GB about $0.70 to $3.50, H100 about $2 to $12 depending on provider; H100 one-year contract prices were reported to have risen about 40% between October 2025 and March 2026, so quotes move.
- Private conversations must not leave the machine without explicit consent. Only data the owner may share, or data that is licensed and non-private, could be used this way.

## 4. Why today's evaluation cannot support an adaptation claim

Using the Wilson 95% interval (the same method as `observatory/benchmarks.py`):

- With **5 probes**, 5/5 passes gives [0.57, 1.00] and 3/5 gives [0.23, 0.88]. They overlap almost completely, so a "better" or "worse" verdict is impossible.
- To tell a 70% baseline from an 85% tuned model at 95% confidence and 80% power needs about **118 independent items per condition** (normal-approximation two-proportion calculation, unpaired); 80% vs 90% needs about 197; 60% vs 80% about 79. With 120 items, 84/120 [0.61, 0.78] and 102/120 [0.78, 0.90] only just separate.
- Therefore any adaptation experiment first needs a **held-out set of at least ~120 graded items** that never appears in training, written independently of the training data, plus a measured baseline under identical conditions.
- Scripted pipeline tests remain useful for regressions in Mia's own code, but they cannot tell whether a tuned model is better.

## 5. Gates that must all pass before any adaptation experiment (EVOL-09)

1. **Lawful, consented dataset exists:** owner-created or licensed text, with provenance recorded per item; third-party and private material excluded unless the owner explicitly approves; a written statement that training on it is permitted and that the owner may delete it.
2. **Held-out evaluation:** at least ~120 graded items, separate from the training data, plus a baseline run (section 4).
3. **Base model chosen with its exact repository licence read.** Licences vary by repository, not just by family: reports in 2026 describe Apache-2.0 for gpt-oss, most Qwen3 releases and Gemma 4, while Llama's community licence adds naming and other conditions, and some Qwen releases use a separate community licence. Verify the specific model card.
4. **Owner hardware or budget stated** (GPU memory and operating system, or a spending cap) and the owner's explicit approval for any spend.
5. **Rollback and safety plan:** the base model stays untouched, the adapter is a separate versioned file, switching is a configuration change, and a safety/regression comparison runs before and after.

## 6. Inputs only the owner can provide
- Which models run on the owner's machine, and its GPU memory and operating system.
- How much conversation and memory data exists, what third-party content it contains, and whether the owner is willing to use it for training.
- A budget ceiling, if any cloud spending is ever considered.
- Approval before any training run or paid service.

## 7. What would change this recommendation
- Gates 1 to 5 pass, and the held-out baseline shows a specific, measurable gap that an adapter plausibly addresses (style, output format, a narrow task) rather than missing facts (better handled by memory).
- A base model Mia can run locally is replaced by a smaller or different family, changing hardware or licence assumptions.

## Sources read (2026-11-24; check again before relying on them)
- Unsloth requirements and fine-tuning guides: https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements.md ; https://computingforgeeks.com/fine-tune-llm-unsloth-qlora/ ; https://dev.to/jovan_chan_9500711396d4e6/fine-tuning-llama3-unsloth-2026-4odd ; https://www.sitepoint.com/fine-tune-local-llms-2026/
- Ollama import and Modelfile documentation: https://docs.ollama.com/import.md ; https://docs.ollama.com/modelfile.md
- Open-weight licence summaries: https://vercel.com/i/open-weight-models ; https://dev.to/jovan_chan_9500711396d4e6/open-source-llm-license-guide-2026-mit-apache-gpl-llama-205c ; https://huggingface.co/datasets/open-weights/open-weight-model-registry
- Pretraining data points: https://arxiv.org/abs/2401.02385 ; https://github.com/jzhang38/TinyLlama ; https://gonzoml.substack.com/p/tinyllama-an-open-source-small-language
- Cloud GPU prices: https://www.runpod.io/gpu-cloud/pricing ; https://neurohive.io/en/guides/cloud-gpu-pricing/ ; https://intuitionlabs.ai/pdfs/cloud-gpu-rental-prices-comparison.pdf
