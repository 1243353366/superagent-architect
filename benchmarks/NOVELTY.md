# Prior-work search and novelty statement

Searched 2026-10-21 (web search; terms: LLM clarifying questions / ambiguous
requirements benchmarks, requirements ambiguity detection NLP, differential
testing of candidate interpretations, uncertainty calibration in agent
workflows). Novelty is claimed ONLY for the narrow items marked DISTINCT,
and only in the form "no prior work was found measuring X" — a negative
search result, not a proof of nonexistence.

## Prior work found (the method is NOT claimed novel where this exists)

| Area | Prior work | Consequence for our claims |
|---|---|---|
| Ambiguity detection in requirements | NLP4RE literature and tooling (QuARS, RETA, ACE, Egnalyzer, systematic reviews) | Cue-based ambiguity detection is decades old; our pattern engine is a naive member of this family — and FAILURES.md measures its 0.333 paraphrase recall accordingly. |
| LLMs asking clarifying questions | ClarifyGPT (arXiv:2309.16120) — identifies specification ambiguities, asks clarifying questions, uses test execution to resolve them; "Clarify When Necessary" (task-agnostic clarification, arXiv:2504.12113 relates) | Asking questions to resolve ambiguity, and even using tests to do it, is NOT novel. Our distinguishing-test mechanism is closest to ClarifyGPT's and must be read in its shadow. |
| Benchmarking clarification | ClarifyCodeBench (arXiv:2607.00711) — evaluates LLMs clarifying ambiguous code requirements | A clarification benchmark exists; ours differs in scope (gold requirements + interacting assumptions + cost accounting + refusal metrics), not in existing at all. |
| LLM uncertainty calibration | Calibration benchmarks and surveys (e.g., "Can LLMs Express Their Uncertainty?", verbalized confidence work) | Calibration as such is established. |
| Question selection | Active learning / experimental design / value-of-information (textbook) | Greedy max-split selection is textbook set cover; no algorithmic novelty claimed. |

## DISTINCT (as far as the search reached)

1. **A typed claim ledger with one-sided refutation and an acceptance gate
   that REFUSES certification while candidate interpretations remain
   indistinguishable** — measured as false-certification rate (4 for the
   naive pipelines, 0 for the gate on the dial-in corpus; 4 vs 0 on the
   uncertainty-loss corpus). No prior benchmark or method was found that
   measures a *certification refusal* rate for ambiguous requirements.
2. **Calibration that separates missing evidence from contradictory
   evidence as distinct bands with distinct remedies** (ask vs reconcile)
   inside a pipeline gate. Searches surfaced calibration of confidence
   *levels*, not this two-mode distinction at a gate.
3. **An uncertainty-LOSS benchmark across multi-stage workflow stages**
   (`uncertainty_loss.py`): how often uncertainty that existed at intake is
   silently flattened to fact by the second stage. No prior benchmark
   measuring cross-stage uncertainty retention was found.
4. **A dial-in cost benchmark with interacting assumptions and known gold
   requirements** (`dial_in_run.py`): questions, answer words, probes, and
   false resolutions per policy, with interacting assumptions collapsing
   dependent ambiguities. The closest found (ClarifyCodeBench) scores LLM
   clarification quality, not cost-to-resolve under interaction.

If prior work contradicting any of these four turns up, this file is the
place the claim gets retracted.
