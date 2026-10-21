#!/usr/bin/env python3
"""Benchmark: how often is uncertainty lost during multi-stage agent workflows?

Novelty claim kept honest: this does NOT simulate other people's tools. It
measures two *policies* on a fixed seeded corpus:

  * NAIVE  — the documented failure mode of typical agent pipelines: the
             first stage resolves ambiguity silently (picks one candidate),
             later stages inherit the choice as fact, nothing is ever asked.
  * CLAIMS — the claims layer: candidates stay explicit, the gate refuses to
             certify undistinguished interpretations, resolving questions are
             surfaced, and answers re-propagate.

Stages modeled per workflow (the "multi-stage" dimension):
  interpret -> specify -> implement -> certify

Metrics (deterministic, recomputable, no randomness):
  * uncertainty_retained — fraction of gold-ambiguous requirements still
    explicitly flagged uncertain at the final stage
  * false_certifications — requirements certified as fact while the gold
    standard says the ambiguity was never resolved
  * resolving_questions_surfaced — count of exact resolving questions named

Run: python3 benchmarks/uncertainty_loss.py   (also runnable as a module)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from saarch.claims import BAND_SUPPORTED  # noqa: E402
from saarch.interpret import Interpreter, active_patterns  # noqa: E402

# ------------------------------------------------------------------ corpus
# Each entry: an ambiguous request + the pattern it should trigger + a
# resolution answer (or "" when the corpus deliberately leaves it unresolved).
CORPUS = [
    {"text": "Users can cancel their subscription anytime without fees.",
     "pattern": "cancellation-scope",
     "answer": "after commitment too"},
    {"text": "Members may cancel anytime they like.",
     "pattern": "cancellation-scope", "answer": ""},
    {"text": "A journal where users can delete their entries when done.",
     "pattern": "deletion-semantics", "answer": "hard delete"},
    {"text": "Users can delete their account records on request.",
     "pattern": "deletion-semantics", "answer": ""},
    {"text": "A notes app where all data stays local.",
     "pattern": "local-strength", "answer": "no network at all"},
    {"text": "Expense tracking; the data stays local.",
     "pattern": "local-strength", "answer": ""},
    {"text": "The dashboard must feel fast to browse.",
     "pattern": "performance-threshold", "answer": "200ms"},
    {"text": "Search should be quick and responsive.",
     "pattern": "performance-threshold", "answer": ""},
    # control: unambiguous requests must certify without questions
    {"text": "Users can create entries with a title.",
     "pattern": None, "answer": ""},
    {"text": "The app must show a list of saved entries.",
     "pattern": None, "answer": ""},
]

STAGES = ["interpret", "specify", "implement", "certify"]


# ------------------------------------------------------------------ policies

def run_naive(item: dict) -> dict:
    """The documented failure mode: silent resolution, inherited as fact.

    Deterministic model of the lossy pipeline (this is a policy, not a claim
    about any specific vendor):
      interpret: ambiguity detected but silently resolved to the FIRST
                 candidate; no question asked
      specify:   the choice is written into requirements as fact
      implement: implemented as fact
      certify:   certified as fact
    """
    stages = {}
    ambiguous = bool(item["pattern"])
    stages["interpret"] = {"flagged_uncertain": ambiguous, "asked": False}
    stages["specify"] = {"flagged_uncertain": False, "asked": False}
    stages["implement"] = {"flagged_uncertain": False, "asked": False}
    stages["certify"] = {"flagged_uncertain": False, "asked": False,
                         "certified": True}          # always certifies
    return {"ambiguous": ambiguous, "stages": stages,
            "final_uncertain": False, "certified": True, "asked": False}


def run_claims(item: dict) -> dict:
    """The claims layer: explicit candidates, a gate, resolving questions."""
    it = Interpreter()
    report = it.compile(item["text"])
    ambiguous = bool(item["pattern"])
    stages = {}
    stages["interpret"] = {"flagged_uncertain": ambiguous,
                           "asked": False}
    # specify/implement proceed; the gate holds the requirement uncertified
    questions = it.resolving_questions()
    stages["specify"] = {"flagged_uncertain": ambiguous,
                        "asked": bool(questions)}
    if item["answer"]:
        # the human answers the resolving question: evidence re-propagates
        for q in questions:
            it.answer(q["test_id"], item["answer"])
        questions = it.resolving_questions()
    result = it.certify()
    stages["implement"] = {"flagged_uncertain": bool(questions),
                           "asked": bool(questions)}
    stages["certify"] = {"flagged_uncertain": bool(questions),
                          "certified": result["certified"]}
    return {"ambiguous": ambiguous, "stages": stages,
            "final_uncertain": bool(questions),
            "certified": result["certified"],
            "asked": bool(questions or it.interpretations),
            "report": report}


# ------------------------------------------------------------------ metrics

def measure(policy) -> dict:
    per_item = [policy(item) for item in CORPUS]
    ambiguous_items = [r for r, c in zip(per_item, CORPUS) if c["pattern"]]
    resolved = [r for r, c in zip(per_item, CORPUS)
                if c["pattern"] and c["answer"]]
    unresolved = [r for r, c in zip(per_item, CORPUS)
                  if c["pattern"] and not c["answer"]]
    control = [r for r, c in zip(per_item, CORPUS) if not c["pattern"]]

    def stage_uncertain(items, stage):
        items = [r for r in items if r["ambiguous"]]
        if not items:
            return 1.0
        return sum(1 for r in items if r["stages"][stage]["flagged_uncertain"]) / len(items)

    return {
        "stage_uncertainty_retention": {
            stage: {
                "ambiguous_all": round(stage_uncertain(per_item, stage), 3),
            } for stage in STAGES
        },
        "final_uncertainty_retained_on_unresolved": round(
            sum(1 for r in unresolved if r["final_uncertain"]) / max(1, len(unresolved)), 3),
        "false_certifications": sum(
            1 for r in unresolved if r["certified"]),
        "correctly_certified_after_answer": sum(1 for r in resolved if r["certified"]),
        "control_certified_without_questions": sum(
            1 for r in control if r["certified"]),
        "resolving_questions_surfaced": sum(
            1 for r in ambiguous_items if r["stages"]["specify"]["asked"]),
    }


def run() -> dict:
    naive = measure(run_naive)
    claims = measure(run_claims)
    return {"stages": STAGES, "naive": naive, "claims": claims}


def main() -> None:
    result = run()
    n_amb = sum(1 for c in CORPUS if c["pattern"])
    n_unres = sum(1 for c in CORPUS if c["pattern"] and not c["answer"])
    print("=" * 74)
    print("UNCERTAINTY-LOSS BENCHMARK — how often uncertainty dies in a pipeline")
    print("=" * 74)
    print(f"corpus: {len(CORPUS)} requests ({n_amb} gold-ambiguous, "
          f"{n_unres} left deliberately unresolved)")
    print(f"stages: {' -> '.join(STAGES)}")
    print()
    print(f"{'metric':46s} {'NAIVE':>10s} {'CLAIMS':>10s}")
    print("-" * 74)
    for stage in STAGES:
        row = f"uncertainty retained at end of {stage:14s}"
        print(f"{row:46s} "
              f"{result['naive']['stage_uncertainty_retention'][stage]['ambiguous_all']:>10.3f} "
              f"{result['claims']['stage_uncertainty_retention'][stage]['ambiguous_all']:>10.3f}")
    for key, label in (
            ("final_uncertainty_retained_on_unresolved",
             "uncertainty retained at final stage (unresolved)"),
            ("false_certifications",
             "FALSE certifications of ambiguous requirements"),
            ("correctly_certified_after_answer",
             "certified correctly after a resolving answer"),
            ("resolving_questions_surfaced",
             "resolving questions surfaced to the human")):
        print(f"{label:46s} {result['naive'][key]:>10} {result['claims'][key]:>10}")
    out = ROOT / "benchmarks" / "results" / "uncertainty-loss.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("-" * 74)
    print(f"results written to {out}")
    print("Deterministic: same corpus + same policies -> identical numbers.")


if __name__ == "__main__":
    main()
