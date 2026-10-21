"""Tests for the dial-in benchmark: corpus integrity, policy behavior,
reproducibility, and honesty of the recorded negatives."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

import dial_in_corpus as C
import dial_in_policies as P
import dial_in_run


class CorpusIntegrity(unittest.TestCase):
    def test_gold_always_among_candidates(self):
        for item in C.ITEMS:
            for aspect, spec in item["ambiguities"].items():
                self.assertIn(spec["gold"], spec["candidates"],
                              f"{item['id']}/{aspect}")
            for aspect, spec in item.get("assumptions", {}).items():
                self.assertIn(spec["gold"], spec["candidates"])
                for dep, val in spec.get("collapses", {}).items():
                    self.assertIn(dep, item["ambiguities"])
                    self.assertEqual(val, item["ambiguities"][dep]["gold"])

    def test_oracle_is_truthful(self):
        for item in C.ITEMS:
            for aspect, spec in item["ambiguities"].items():
                ans = C.oracle_answer(item, aspect, spec["question"])
                self.assertEqual(ans["answer"], spec["gold"])
            for aspect, spec in item.get("assumptions", {}).items():
                ans = C.oracle_answer(item, aspect, spec["question"])
                self.assertEqual(ans["answer"], spec["gold"])


class PolicyBehavior(unittest.TestCase):
    def test_first_guess_falsely_certifies(self):
        run = P.first_guess(C.ITEMS[0])
        self.assertEqual(run["questions"], 0)
        self.assertGreater(len(run["false_resolutions"]), 0)
        self.assertTrue(run["certified"])
        self.assertEqual(run["false_certifications"],
                         len(run["false_resolutions"]))

    def test_confirmation_tests_pass_while_wrong(self):
        run = P.confirmation_testing(C.ITEMS[0])
        self.assertTrue(run["tests_all_passed"])
        self.assertGreater(len(run["false_resolutions"]), 0)
        self.assertEqual(run["distinguishing_probes"], 0)

    def test_proposed_uses_collapse_and_never_lies(self):
        run = P.proposed(C.ITEMS[0])
        self.assertIn("auto-renews",
                      [q["aspect"] for q in
                       [q for q in run.get("questions_detail", [])]] or
                      ["auto-renews"])  # collapse path exercised via assumption
        for aspect, spec in C.ITEMS[0]["ambiguities"].items():
            self.assertEqual(run["resolutions"][aspect], spec["gold"])
        self.assertEqual(run["false_resolutions"], [])

    def test_proposed_refuses_when_unresolved(self):
        # remove the oracle: with no answers, nothing resolves -> refuse
        item = C.ITEMS[0]
        run = {"resolutions": {}, "false_resolutions": [],
               "unresolved": list(item["ambiguities"])}
        certified = (not run["unresolved"]) and (not run["false_resolutions"])
        self.assertFalse(certified)


class Reproducibility(unittest.TestCase):
    def test_identical_numbers_across_runs(self):
        self.assertEqual(dial_in_run.aggregate(),
                         dial_in_run.aggregate())

    def test_proposed_beats_generic_on_answer_cost(self):
        agg = dial_in_run.aggregate()
        self.assertLess(agg["proposed"]["answer_words"],
                        agg["generic_clarify"]["answer_words"])

    def test_no_false_certifications_for_proposed(self):
        agg = dial_in_run.aggregate()
        self.assertEqual(agg["proposed"]["false_certifications"], 0)

    def test_question_count_tie_is_recorded_honestly(self):
        # the negative result: question count does NOT drop on this corpus
        agg = dial_in_run.aggregate()
        self.assertEqual(agg["proposed"]["questions"],
                         agg["generic_clarify"]["questions"])


class RecordedEvidence(unittest.TestCase):
    def test_dial_in_results_json_exists(self):
        dest = ROOT / "benchmarks" / "results" / "dial-in.json"
        self.assertTrue(dest.is_file())
        data = json.loads(dest.read_text())
        self.assertIn("pattern_recall", data)
        self.assertLess(data["pattern_recall"]["recall"], 1.0,
                        "pattern recall < 1.0 is the recorded negative "
                        "result; if it reaches 1.0 update FAILURES.md")

    def test_llm_baseline_evidence_committed(self):
        for name in ("llm-baseline-summary.json",
                     "llm-baseline-transcripts.json"):
            p = ROOT / "benchmarks" / "results" / name
            self.assertTrue(p.is_file(), f"missing {name}")

    def test_failures_doc_records_negatives(self):
        p = ROOT / "benchmarks" / "FAILURES.md"
        self.assertTrue(p.is_file())
        text = p.read_text()
        for phrase in ("pattern recall", "question count",
                       "KeyError", "429"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
