#!/usr/bin/env python3
"""Smoke demo 2 — calibration: missing evidence ≠ contradictory evidence.

The hard problem, shown physically: two claims that both 'fail' do not fail
the same way. One has no evidence at all (remedy: ask). One has evidence on
both sides of a contradiction (remedy: reconcile). Collapsing them into one
'uncertain' bucket is the uncertainty-loss failure mode this prototype exists
to prevent.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from saarch.claims import (  # noqa: E402
    BAND_SUPPORTED, BAND_UNKNOWN_CONFLICTED, BAND_UNKNOWN_MISSING,
    Claim, ClaimStore, Evidence,
)


def calib_line(store, cid):
    c = store.calibration(cid)
    return (f"    band={c['band']:<18s} numeric={str(c['numeric']):<6s} "
            f"evidence={c['evidence_count']} kinds={c['evidence_kinds']} "
            f"deps_broken={c['deps_broken']}\n"
            f"      remedy: {c['remedy']}")


def main() -> None:
    print("=" * 74)
    print("SMOKE 2 — missing evidence vs contradictory evidence")
    print("=" * 74)

    store = ClaimStore()

    # --- claim with NO evidence: missing, not contradictory
    e1 = store.add_evidence(Evidence(kind="prompt_excerpt",
                                     content="The store must be local",
                                     source="request"))
    missing = store.add_claim(Claim(
        id="Q-missing", type="requirement",
        statement="Offline-first sync is required (never stated)",
        predicate={"subject": "sync", "property": "mode",
                   "value": "offline_first", "polarity": "positive"}))
    # --- claim with evidence on BOTH sides: contradictory
    ea = store.add_evidence(Evidence(kind="prompt_excerpt",
                                     content="The spec says: delete removes data",
                                     source="spec v1"))
    eb = store.add_evidence(Evidence(kind="user_answer",
                                     content="The user says: delete only hides data",
                                     source="review call"))
    contradicted = store.add_claim(Claim(
        id="Q-conflicted", type="requirement",
        statement="Deletion removes data permanently",
        predicate={"subject": "data", "property": "delete_semantics",
                   "value": "hard", "polarity": "positive"},
        evidence=[ea, eb]))

    store.detect_contradictions()

    print("\n1) A claim nobody ever evidenced:")
    print(calib_line(store, missing))
    print("\n2) A claim with an explicit counter-assertion attached:")
    # attach the counter-assertion so the detector can see the conflict
    counter = store.add_claim(Claim(
        id="Q-counter", type="interpretation",
        statement="Deletion only hides data",
        predicate={"subject": "data", "property": "delete_semantics",
                   "value": "soft", "polarity": "positive"},
        evidence=[eb]))
    store.detect_contradictions()
    print(f"    contradictions detected: {len(store.contradictions)} "
          f"({store.contradictions[0].claim_a} vs {store.contradictions[0].claim_b} "
          f"on {store.contradictions[0].property_})")
    print(calib_line(store, contradicted))
    print(calib_line(store, counter))

    print("\n3) A claim with independent evidence kinds compounding to certainty:")
    ec = store.add_evidence(Evidence(kind="test_result",
                                     content="probe: cancel after commit -> accepted",
                                     source="T-cancel-scope"))
    certain = store.add_claim(Claim(
        id="Q-certain", type="derived_fact",
        statement="Cancellation after commitment succeeds",
        predicate={"subject": "subscription", "property": "cancel_window",
                   "value": "any_time", "polarity": "positive"},
        evidence=[e1, ec]))
    print(calib_line(store, certain))

    print("\nThe two 'unknown' bands are the point: a missing band sends a "
          "question,")
    print("a conflicted band sends a reconciliation. Never one generic "
          "'uncertain'.")


if __name__ == "__main__":
    main()
