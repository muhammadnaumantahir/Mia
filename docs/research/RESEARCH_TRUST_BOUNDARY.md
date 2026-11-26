# Research untrusted-content boundary

**Status:** implemented defense in depth; adversarial regression cases authored.  
**Scope:** owner-scoped browser research and its Gateway synthesis path.

## Trust contract

Webpage content is evidence, never executable instructions. A page cannot change Mia's role, policy,
tools, permissions, privacy settings or owner-approval requirements. The research feature uses the
existing owner-allowlisted fetch boundary and never follows commands or arbitrary links contained
in page text.

The current pipeline preserves several independent barriers:

1. The fetcher restricts destinations and redirects to the owner's allowed hosts, and extracts
   readable page text rather than executing page scripts.
2. Each source is stored with its final URL, fetch time and SHA-256 provenance. The source text,
   page metadata and search snippets are labelled untrusted in the synthesis input.
3. The Gateway request uses a dedicated system instruction stating that every source is untrusted
   evidence and must never be treated as instructions. It asks for bounded claims and exact,
   continuous quotes, and tells the model not to cite spans marked as instruction-like.
4. The deterministic detector scans a compatibility-normalized, case-folded view. It strips
   zero-width formatting controls, folds selected Unicode lookalikes, and collapses whitespace
   and line breaks before matching common instruction, role-change, secret-exfiltration,
   concealment, command-execution and data-transfer patterns.
5. Detector results are mapped back to offsets in the original unmodified source. The claim
   validator requires exact quotes from the same research run and refuses any quote that overlaps
   an instruction-like span.
6. Model-generated claims remain `proposed`; only an explicit owner confirmation makes a finding.
   Only confirmed, citation-backed claims can be imported into a registered project-scoped memory.

## Why normalization preserves source offsets

Detection does not rewrite or replace the stored page. Every normalized character maps back to
its original source range, including compatibility expansions. When a match includes removed
zero-width characters or normalized line breaks, the original matched span still includes those
characters. Citation validation therefore rejects the original obfuscated passage, not a
synthetic normalized quote.

## Limits and required interpretation

This is a heuristic detector, not a semantic security proof. It improves coverage for common
Unicode confusables, zero-width separators and multi-line variants, but cannot identify every
novel instruction, image-only prompt, encoded payload or semantic paraphrase. A page may contain
false positives; those spans are intentionally withheld from citation, while unrelated passages
remain usable. A clean detector result must never be interpreted as proof that a page is safe.

The model is still given untrusted content in a separate user-role JSON payload under a system
instruction. The model output is never executed as instructions. Claims are constrained again by
the deterministic same-run quote validator and owner review. These controls are independent:
neither a prompt instruction nor the pattern detector alone is relied on for safety.

Automated adversarial cases cover compatibility-width characters, newline-separated words,
zero-width insertion, selected Cyrillic/Greek lookalikes, original-offset preservation, refusal of
an obfuscated citation, and ordinary multilingual prose. Test execution remains separately
recorded when unavailable; no live-provider behavior or real-site robustness is inferred from
scripted tests.
