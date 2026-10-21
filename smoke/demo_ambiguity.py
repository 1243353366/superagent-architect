#!/usr/bin/env python3
"""Smoke demo 1 — the dial-in loop, shown physically.

Watch a request enter as ambiguous English and leave as either a certified
requirement or a refused certification with the exact resolving question.
Nothing is simulated: this is the live interpreter + gate.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from saarch.interpret import Interpreter  # noqa: E402


def bar(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def show(request: str, answer=None) -> None:
    print(f"\nREQUEST: “{request}”")
    it = Interpreter()
    report = it.compile(request)
    if not report["ambiguous"]:
        print("  -> no ambiguity pattern matched; proceeding deterministically")
    for interp in it.interpretations:
        print(f"  AMBIGUITY [{interp.pattern}] — candidates:")
        for cand_id, claim_id in interp.candidate_claims.items():
            print(f"    [{cand_id}] {it.store.claims[claim_id].statement}")
        print(f"  DISTINGUISHING TEST [{interp.test.id}]: {interp.test.probe}")
    result = it.certify()
    if result["certified"]:
        print("  GATE: CERTIFIED — every requirement has evidence, none ambiguous")
        return
    for b in result.get("ambiguous_blockers", []):
        print(f"  GATE: REFUSED — requirement {b['requirement']} has "
              f"{len(b['live_candidates'])} live candidates: "
              f"{b['live_candidates']}")
        print(f"         certification is blocked until they are distinguished")
    for b in result["blockers"]:
        print(f"  GATE: REFUSED — claim {b['claim_id']} is {b['reason']} "
              f"(band: {b['band']}); remedy: {b['remedy']}")
    for q in it.resolving_questions():
        print(f"\n  THE RESOLVING QUESTION: {q['question']}")
        if answer is None:
            print("  (no answer provided — the requirement stays uncertified. "
                  "That is the honest outcome.)")
            return
        print(f"  HUMAN ANSWERS: “{answer}”")
        outcome = it.answer(q["test_id"], answer)
        if not outcome["changed"]:
            print(f"  -> answer not recognized; question stays open (honestly): "
                  f"{outcome['question'][:60]}…")
            return
        print(f"  -> evidence attached, candidate {outcome['selected']} "
              f"selected, others contradicted automatically")
    result = it.certify()
    if result["certified"]:
        print("  GATE: NOW CERTIFIED — the answer re-propagated through the "
              "dependency graph; no re-plumbing was done by hand")
    else:
        print(f"  GATE: STILL BLOCKED — {result['blockers']} "
              f"{result.get('ambiguous_blockers', [])}")


def main() -> None:
    bar("SMOKE 1a — ambiguous request, dialed in by a human answer")
    show("Users can cancel their subscription anytime without fees.",
         "Yes, they can cancel after commitment too")

    bar("SMOKE 1b — same request, but nobody answers")
    show("Members may cancel anytime they like.", None)

    bar("SMOKE 1c — an answer that matches nothing is NOT silently absorbed")
    show("Users can delete their account records on request.",
         "I like turtles")

    bar("SMOKE 1d — unambiguous request passes straight through")
    show("Users can create entries with a title.", None)


if __name__ == "__main__":
    main()
