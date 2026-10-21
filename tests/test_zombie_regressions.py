"""The zombie regression suite: every retired zombie's minimized repro is
replayed here. If one of these fails, a documented failure has silently
returned — the one thing this policy forbids."""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/benchmarks")

from benchmarks.bypass_lab import Lab          # noqa: E402
from saarch.trusted_core import TrustedCore    # noqa: E402


def core_with_question():
    core = TrustedCore()
    req = core.ingest_request(
        "Users can cancel their subscription anytime.")
    session = core.open_session("engine")
    pid = session.propose("interpretation_batch", {
        "requirement": req,
        "candidates": [
            {"id": "A", "statement": "before commitment only",
             "predicate": {"subject": "cancel", "property": "scope",
                           "value": "pre"}},
            {"id": "B", "statement": "anytime after commitment",
             "predicate": {"subject": "cancel", "property": "scope",
                           "value": "any"}}],
        "probe": "subscribe then cancel",
        "question": "after commitment?", "test_id": "T-z"})
    out = core.promote(pid)
    assert out["promoted"], out
    return core, session, "T-z"


class ZombieRegressions(unittest.TestCase):

    def test_z1(self):
        """Z1+Z2 (D1/D3): replay the minimized repro — an engine candidate
        with zero evidence must not certify, and a live question must be
        immutable against engine redefinition."""
        lab = Lab(28)
        for op in ["ingest", "open_session", "collision"]:
            lab.op(op)
        breaches, result = lab.detect()
        self.assertEqual(breaches, [])
        self.assertFalse(result["certified"],
                         "zero-evidence engine resolution certified")

        # full hijack shape: human answered, engine redefines the test id
        core, session, tid = core_with_question()
        core.record_human_answer(tid, "B: cancel anytime after commitment")
        self.assertTrue(core.certify()["certified"])
        req_id = [c.id for c in core.store.claims.values()
                  if c.type == "requirement"][0]
        pid2 = session.propose("interpretation_batch", {
            "requirement": req_id,
            "candidates": [{"id": "E", "statement": "engine's choice",
                            "predicate": {"subject": "cancel",
                                          "property": "scope",
                                          "value": "engine"}}],
            "probe": "p", "question": "q", "test_id": tid})
        out = core.promote(pid2)
        self.assertFalse(out["promoted"], "question redefined by engine")
        self.assertIn("immutable", out["reason"])
        self.assertTrue(core.certify()["certified"],
                        "human resolution must survive the hijack attempt")

    def test_z3(self):
        """Z3 (D5): replay the minimized repro — the trap answer must not
        misparse; ambiguous answers leave the question open."""
        lab = Lab(32)
        for op in ["ingest", "open_session", "propose_batch", "promote",
                   "human_answer_trap"]:
            lab.op(op)
        breaches, _ = lab.detect()
        self.assertEqual(breaches, [])
        self.assertIsNone(lab.wrong_resolution)

        core, _, tid = core_with_question()
        trap = core.record_human_answer(
            tid, "Absolutely yes - cancel anytime after commitment")
        self.assertFalse(trap["resolved"],
                         "substring match silently selected a candidate")
        self.assertIsNotNone(core.open_question(tid),
                             "question must stay open after an ambiguous "
                             "answer")
        clean = core.record_human_answer(
            tid, "B: cancel anytime after commitment")
        self.assertTrue(clean["resolved"])
        self.assertEqual(clean["selected"], "B")
        self.assertTrue(core.certify()["certified"])


if __name__ == "__main__":
    unittest.main()
