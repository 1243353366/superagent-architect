#!/usr/bin/env python3
"""Run the dial-in benchmark: aggregate metrics + pattern-recall negatives.

Prints the measured table (deterministic), writes results JSON, and measures
the deterministic interpreter's recall on paraphrased requests — reported
as-is, including misses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dial_in_corpus as C
import dial_in_policies as P

POLICIES = [("first_guess", P.first_guess),
            ("generic_clarify", P.generic_clarify),
            ("confirmation_testing", P.confirmation_testing),
            ("proposed", P.proposed)]


def aggregate() -> dict:
    per_policy = {}
    for name, fn in POLICIES:
        rows = [fn(item) for item in C.ITEMS]
        per_policy[name] = {
            "questions": sum(r["questions"] for r in rows),
            "answer_words": sum(r["answer_words"] for r in rows),
            "probes": sum(r.get("probes", 0) for r in rows),
            "false_resolutions": sum(len(r["false_resolutions"]) for r in rows),
            "false_certifications": sum(r.get("false_certifications", 0)
                                        for r in rows),
            "certified_items": sum(1 for r in rows if r.get("certified")),
        }
    return per_policy


def pattern_recall() -> dict:
    """Feed paraphrased requests through the REAL interpreter and count
    whether the known ambiguity was detected. Reported honestly: the
    deterministic pattern engine is cue-word based and misses paraphrases."""
    from saarch.interpret import Interpreter
    total, detected = 0, 0
    misses, true_negatives = [], []
    for item in C.ITEMS:
        expected = set(item["ambiguities"])
        for para in item["paraphrases"]:
            it = Interpreter()
            found = set()
            try:
                it.compile(para)
                found = {i.pattern for i in it.interpretations}
            except Exception as exc:  # pragma: no cover - defensive
                if expected:
                    total += 1
                    misses.append({"paraphrase": para[:60],
                                   "reason": f"compile error: {exc}"})
                continue
            if not expected:
                # no gold ambiguity: detecting nothing is a TRUE NEGATIVE
                if found:
                    misses.append({"paraphrase": para[:60],
                                   "reason": f"false positive: {found}"})
                else:
                    true_negatives.append(para[:60])
                continue
            total += 1
            if found:
                detected += 1
            else:
                misses.append({"paraphrase": para[:60],
                               "reason": "no pattern matched (cue-word "
                                         "engine limitation)"})
    return {"paraphrases": total, "detected": detected,
            "recall": round(detected / total, 3) if total else None,
            "misses": misses, "true_negatives": true_negatives}


def main() -> None:
    agg = aggregate()
    recall = pattern_recall()
    print("=" * 74)
    print("DIAL-IN BENCHMARK — gold requirements, ambiguous requests, "
          "interacting assumptions")
    print("=" * 74)
    print(f"corpus: {len(C.ITEMS)} items, "
          f"{sum(len(i['ambiguities']) for i in C.ITEMS)} gold ambiguities, "
          f"{sum(len(i.get('assumptions', {})) for i in C.ITEMS)} "
          "interacting assumptions; truthful oracle")
    print()
    hdr = f"{'policy':24s}{'questions':>10s}{'answer words':>13s}{'probes':>8s}{'false resol.':>13s}{'false certif.':>13s}"
    print(hdr)
    print("-" * len(hdr))
    for name, row in agg.items():
        print(f"{name:24s}{row['questions']:>10d}{row['answer_words']:>13d}"
              f"{row['probes']:>8d}{row['false_resolutions']:>13d}"
              f"{row['false_certifications']:>13d}")
    print("-" * len(hdr))
    print()
    print("PATTERN RECALL on paraphrased requests (negative result, "
          "reported as-is):")
    print(f"  paraphrases: {recall['paraphrases']}   detected: "
          f"{recall['detected']}   recall: {recall['recall']}")
    for m in recall["misses"]:
        print(f"  MISS: {m['paraphrase']!r} — {m['reason']}")

    out = {"corpus_items": len(C.ITEMS), "policies": agg,
           "pattern_recall": recall,
           "deterministic": True}
    dest = Path(__file__).parent / "results" / "dial-in.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nresults written to {dest}")
    print("Deterministic: same corpus + same policies -> identical numbers.")


if __name__ == "__main__":
    main()
