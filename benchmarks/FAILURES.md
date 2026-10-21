# Recorded failures and negative results

This file publishes what did NOT work, on purpose. Successful demonstrations
live elsewhere; everything here was measured and kept as-is.

## Measured negative results (dial-in benchmark, deterministic)

1. **The question-count claim does not hold on this corpus.** The claims
   layer asked exactly as many questions as the naive generic-clarify
   baseline (5 vs 5). The max-split / interacting-assumption machinery did
   NOT reduce question count here. What it did reduce: answer verbosity
   (5 words total vs 110 — the distinguishing questions admit one-word
   answers) and false certifications (0 vs 4). If question count is the
   only metric you care about, the naive baseline ties.

2. **Cue-word pattern recall on paraphrases: 0.333.** The deterministic
   interpretation patterns are cue-word based. Re-worded requests —
   "terminate the service at any moment", "wipe their profile whenever" —
   are missed. LLM-generated interpretation is the plausible fix; it was
   deliberately out of scope for this prototype. (benchmarks/results/
   dial-in.json records the misses verbatim.)

3. **Conventional tests validate the guess, not the gold.** The
   confirmation-testing baseline wrote one test per ambiguous requirement,
   all tests passed, and the resolution was still wrong on 4 of 5 — the
   tests encoded the first guess and confirmed it. This is the
   confirmatory-testing blind spot, measured.

## Live-run failures (zero-shot LLM baseline, 2026-10-21)

Model: llm7/gpt-oss:20b via a local OpenProvider gateway (free tier).

- **Rate limiting killed 3 of 4 items** (HTTP 429, retried with backoff:
  30/60/120s, then recorded as failed). Recorded in
  `results/llm-baseline-transcripts.json`.
   A second full run at 16:14 UTC was 0/4 — the free-tier upstream was
   fully rate-limited by then. The committed evidence file is the 16:05
   run because it completed more items; the rerun exists only in git
   history. Publishing both outcomes, keeping the informative one.

- **Schema compliance: 0.0.** The single completed item parsed, but on the
  lenient reading only — the model ignored the requested JSON keys
  (`ambiguous_aspects`/`clarifications_needed` instead of
  `ambiguities`/`questions_you_would_ask`). Strict parse success: 1/4.
- **Lenient ambiguity recall: 0.4, gold interpretation match: 0.0.** On
  the one scored item the model did surface both gold ambiguities but
  committed to none of the gold interpretations and asked its own
  questions in its own format.
- These numbers describe ONE run of ONE small free model; they bound the
  baseline instantiation, not LLM prompting in general.

## Tooling failures found and fixed during the run (published per repo policy)

- `run_llm_baseline.py` initially used `str.format()` on a prompt containing
  literal JSON braces — `KeyError: '"ambiguities"'` before any model call.
  Fixed with plain concatenation; the failure and traceback were recorded
  in the transcripts before the fix.
- Earlier recorded platform failures (kept for honesty): free-tier auto
  routing intermittently returns HTTP 402 mid-pipeline (planner retries
  once, then falls back deterministically); one sandbox loopback
  `ConnectionResetError` flake in generated-app tests; a Python 3.9
  incompatibility (`str | None`) that CI caught and is now regression-
  tested by the matrix.

## Reading order

- `NOVELTY.md` — prior-work search and the (narrow) claims that survive it.
- `uncertainty_loss.py`, `dial_in_run.py` — the benchmarks that produced
  these numbers. Both are deterministic: same corpus, same policies,
  identical output.
