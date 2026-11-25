# Capability-gap discovery (MIA-EVOL-01)

Code: `src/mia/evolution/gaps.py` (own SQLite file `mia-gaps.sqlite3`); API under
`/api/evolution/gaps`. This step only **records and ranks** gaps. It never writes or runs code and
never starts evolution; the generation/validation pipeline (MIA-EVOL-02 onward, A5) is separate and
may later read open gaps as input.

## What counts as a signal

| Source | Where it comes from | Needs consent | Threshold (distinct evidence) |
|---|---|---|---|
| `failed_tasks` | `TaskStore` rows with `status = failed`, grouped by `source:last_error` | yes | 2 |
| `corrections` | structured memory records whose origin has `corrects` (made by `supersede`), grouped by record type | yes | 3 |
| `evaluations` | failed evaluation cases the owner or an evaluator **submits** (`POST /evaluation-failure`), grouped by suite+case, one per run | yes | 2 |
| owner request | `POST /owner-request`: the owner explicitly asks for a capability | no (the request is the consent) | 1 |

There is no persisted evaluation store in Mia today, so evaluation evidence exists only when it is
submitted. A goal on its own is **not** a signal; an explicit owner request is.

## Rules

- **Consent first.** Each consented source is off until granted (`PUT /consent`). An ungranted source
  is not read at all, not even counted (`skipped: no_consent` in the scan report). Revoking a source
  deletes its evidence (and submitted evaluation signals), recomputes affected gaps and deletes any gap
  left without evidence. Owner requests are never touched by revoking another source.
- **No fabricated demand.** One failure is never a gap. Signatures below their threshold are reported
  under `below_threshold` and nothing is created. Only the last 90 days count. Nothing is inferred
  from code volume, file counts or any model; there is no model in this module.
- **Evidence is cited, not copied.** Each item keeps its source, a reference id and a short excerpt
  (160 characters, credential-like text replaced with a withheld marker). Corrections keep **no text**.
- **Deterministic scoring.** confidence = min(cap, base + step x count): failed tasks 0.35 + 0.10n
  (cap 0.90), evaluations 0.40 + 0.15n (cap 0.90), corrections 0.30 + 0.10n (cap 0.80); owner requests
  1.0. Impact: 5 or more pieces of evidence = high, 3-4 = medium, else low (owner requests: chosen by
  the owner). Gaps list highest impact, then confidence, then newest.
- **Duplicates are linked, not merged.** A new gap whose distinguishing words overlap an open gap by
  60% or more gets `duplicate_of` (the older gap) and is hidden from the default listing
  (`include_duplicates=true` shows it). Deleting the older gap clears the link.
- **Status.** `open`, `parked`, `dismissed`, `resolved`, owner-set with a short reason. New evidence is
  still attached to a dismissed gap but never reopens it. Every change is recorded in `gap_events`
  (ids, sources and counts only, no excerpts).
- **Scan.** `POST /scan` is a dry run by default and writes nothing; it is idempotent (re-scanning adds
  only new evidence).

## Not done

No scheduler (a scan runs when called), no UI, no automatic hand-off to the evolution pipeline, no
link from a gap to a capability candidate, no scraping of conversations or evaluation reports, no
model-written summaries. The scan examines at most the 500 newest-priority tasks and records the
`examined` counts so a truncation is visible. `mia-gaps.sqlite3` is not part of the life backup.
