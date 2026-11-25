# Small-model adaptation experiment: justified no-go for now (MIA-EVOL-09)

**Author:** A3 (Claude Sonnet 5.5 session) · **Written:** 2026-11-24 · **Decision: NO-GO today.**
**Not done, and deliberately so:** no experiment was run, no model or dataset was downloaded, no money was spent, and no result is reported, because the preconditions in the task ("EVOL-08 go decision; lawful dataset available") are not met. The task allows a justified no-go "rather than fabricate results or spend money"; this is that document.

## Why no-go

EVOL-08 ([FOUNDATION_MODEL_FEASIBILITY.md](FOUNDATION_MODEL_FEASIBILITY.md)) found adapter tuning technically cheap but conditional on five gates. Today none of them is documented as met:

1. **Lawful dataset:** none exists. Mia has no training-data policy or consent flow, and the owner's conversations are private and may quote third parties.
2. **Held-out evaluation:** the strongest model check is the 5-probe live harness; detecting 70% vs 85% needs about 118 items per condition.
3. **Base model and licence:** the owner's models are not recorded in the repo, so no repository licence has been read for a chosen base model.
4. **Hardware/budget and approval:** unknown, and no training run has been approved by the owner.
5. **Rollback and safety plan:** model adapters have no versioning or rollback path in Mia yet.

## The gate check is executable

`python -m mia.evolution.adaptation_gates MANIFEST.json` prints PASS/FAIL per gate and exits 0 only when all five pass. It rejects missing, empty, merely-truthy or too-small evidence (for example fewer than 120 held-out items, any item without provenance or owner consent, or a held-out item that also appears in training). "Ready" means the preconditions are documented, not that tuning will help.

Run on the blank template `adaptation_manifest.example.json` (the real output, exit code 1):

```
FAIL lawful_dataset
     - dataset.items must be a positive integer
     - dataset.owner_may_delete must be true
FAIL held_out_evaluation
     - held_out.items must be at least 120
     - held_out.baseline_measured must be true
FAIL base_model_licence
     - base_model.model_id is required
     - base_model.repository_url is required
     - base_model.licence_name is required
     - base_model.licence_checked_on is required
     - base_model.licence_permits_fine_tuning must be true
FAIL owner_hardware_or_budget
     - owner.gpu_memory_gb or owner.budget_cap_usd must be a positive number
     - owner.approved_training_run must be true
FAIL rollback_and_safety_plan
     - rollback.base_model_untouched must be true
     - rollback.adapter_is_separate_versioned_file must be true
     - rollback.switch_is_configuration must be true
     - rollback.safety_regression_comparison_planned must be true
NOT READY: no adaptation experiment may start
```

## Protocol to use when the gates pass

1. **Freeze the evidence:** commit the filled manifest, the exact base-model repository revision and licence text, and the dataset's provenance list. Split the data once, with a fixed seed, into training and held-out; never regenerate the split.
2. **Baseline first:** run the held-out set on the untouched base model under the conditions you will use for the tuned one (same prompts, settings and hardware); record per-item outcomes with `mia.observatory.benchmarks` so runs are versioned and comparable.
3. **Train small and local:** one LoRA/QLoRA adapter on the owner's own machine if the data is private; a different seed or a second run to see run-to-run spread. Keep the base weights untouched and the adapter a separate file.
4. **Compare honestly:** the same held-out items for base and tuned; report both pass rates with 95% Wilson intervals and name every item that got worse. Call it better only if the intervals separate; otherwise report "no clear difference".
5. **Check for harm:** rerun Mia's scripted safety and regression scenarios and the live probes with the adapter enabled; any new failure blocks promotion.
6. **Promote or discard:** enabling the adapter is a configuration switch by the owner; rollback is removing it. Retain the report; delete the dataset on request.

## Stop conditions
Stop and report a no-go if data provenance cannot be shown for any item, the held-out set overlaps the training data, the licence forbids the use, spending would exceed the owner's cap, or the tuned model is worse on any safety scenario.

## What the owner needs to supply to reopen this
Which models run locally and on what GPU; which of the owner's own text may be used for training and whether third-party content is excluded; a held-out set of at least ~120 graded items (or approval to build one); a spending cap, if any; and approval for a training run. Then fill in a copy of `adaptation_manifest.example.json` and run the gate check.
