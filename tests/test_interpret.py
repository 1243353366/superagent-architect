"""Interpreter tests: candidate interpretations, distinguishing tests, the
resolving-question mechanism, and the refuse-then-dial-in loop."""
import unittest

from saarch.interpret import Interpreter


class CompileTests(unittest.TestCase):
    def test_ambiguous_request_yields_candidates_and_a_test(self):
        it = Interpreter()
        r = it.compile("Users can cancel their subscription anytime without fees.")
        self.assertTrue(r["ambiguous"])
        interp = it.interpretations[0]
        self.assertEqual(len(interp.candidate_claims), 2)
        self.assertTrue(interp.test.probe.endswith("."),
                        "the test must be a concrete, executable experiment")
        self.assertEqual(len(interp.test.outcomes), 2)

    def test_unambiguous_request_certifies_immediately(self):
        it = Interpreter()
        r = it.compile("Users can create entries with a title.")
        self.assertFalse(r["ambiguous"])
        result = it.certify()
        self.assertTrue(result["certified"])

    def test_all_four_patterns_fire(self):
        cases = [
            ("Cancel the plan anytime you want.", "cancellation-scope"),
            ("Users can delete their account data on request.", "deletion-semantics"),
            ("An offline notes app where the data stays local.", "local-strength"),
            ("The interface must be fast to use.", "performance-threshold"),
        ]
        for text, pattern in cases:
            with self.subTest(pattern=pattern):
                it = Interpreter()
                r = it.compile(text)
                self.assertIn(pattern, r["interpretations"], text)


class GateLoopTests(unittest.TestCase):
    def test_gate_refuses_while_undistinguished(self):
        it = Interpreter()
        it.compile("Users can cancel their subscription anytime without fees.")
        result = it.certify()
        self.assertFalse(result["certified"])
        blockers = result["ambiguous_blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(len(blockers[0]["live_candidates"]), 2)
        # the refusal names the requirement, the ambiguity, and the candidates
        self.assertEqual(blockers[0]["ambiguity"], "cancellation-scope")

    def test_resolving_question_is_exact(self):
        it = Interpreter()
        it.compile("Members may cancel anytime.")
        qs = it.resolving_questions()
        self.assertEqual(len(qs), 1)
        self.assertTrue(qs[0]["question"].endswith("?"))
        self.assertIn("probe", qs[0])
        self.assertEqual(qs[0]["distinguishes"], ["A", "B"])

    def test_answer_dials_in_and_certifies(self):
        it = Interpreter()
        it.compile("Users can cancel their subscription anytime without fees.")
        self.assertFalse(it.certify()["certified"])
        out = it.answer("T-cancel-scope", "Yes, they can cancel after commitment too")
        self.assertTrue(out["changed"])
        self.assertTrue(out["resolved"])
        result = it.certify()
        self.assertTrue(result["certified"])
        self.assertIn(result["accepted"][0], ["R1"])

    def test_answer_selects_the_other_candidate(self):
        it = Interpreter()
        it.compile("Users can cancel their subscription anytime without fees.")
        out = it.answer("T-cancel-scope", "only before commitment")
        self.assertEqual(out["selected"], "A")
        self.assertTrue(it.certify()["certified"])

    def test_unrecognized_answer_is_not_absorbed(self):
        it = Interpreter()
        it.compile("Users can delete their account records on request.")
        out = it.answer("T-delete-semantics", "I like turtles")
        self.assertFalse(out["changed"])
        # the question stays open and the gate still refuses
        self.assertFalse(it.certify()["certified"])

    def test_answers_repropagate_automatically(self):
        # the human's answer becomes evidence; statuses change without any
        # manual re-plumbing — the second certify() reflects the new evidence
        it = Interpreter()
        it.compile("A journal where users can delete their entries when done.")
        before = it.store.calibration(it.interpretations[0].candidate_claims["A"])
        it.answer("T-delete-semantics", "hard delete")
        after = it.store.calibration(it.interpretations[0].candidate_claims["A"])
        self.assertEqual(before["band"], "unknown-missing")
        self.assertEqual(after["band"], "supported")
        self.assertTrue(it.certify()["certified"])

    def test_multi_request_isolation(self):
        it = Interpreter()
        it.compile("Users can cancel their subscription anytime without fees.")
        it.compile("Users can create entries with a title.")
        qs = it.resolving_questions()
        self.assertEqual(len(qs), 1)   # only the ambiguous request blocks


if __name__ == "__main__":
    unittest.main()
