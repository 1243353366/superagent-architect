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


def z1_scenario(core_cls):
    """Z1's exclusive surface: an engine resolution resting on zero
    trusted (human or test) evidence must not certify. Raises
    AssertionError if the guard is gone."""
    core = core_cls()
    req = core.ingest_request(
        "Users can cancel their subscription anytime.")
    session = core.open_session("engine")
    pid = session.propose("interpretation_batch", {
        "requirement": req,
        "candidates": [{"id": "A", "statement": "before commitment only",
                        "predicate": {"subject": "cancel",
                                      "property": "scope", "value": "pre"}}],
        "probe": "subscribe then cancel",
        "question": "after commitment?", "test_id": "T-z1"})
    out = core.promote(pid)
    assert out["promoted"], out
    cert = core.certify()
    assert not cert["certified"], "zero-evidence engine resolution certified"
    assert cert.get("engine_only_blockers") or cert.get("ambiguous_blockers")


def z2_scenario(core_cls):
    """Z2's exclusive surface: a question is immutable once a human has
    answered it — against engine redefinition, and across a rollback
    that must restore the exact checkpoint state. Raises
    AssertionError if the guard is gone."""
    core = core_cls()
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
                           "value": "post"}}],
        "probe": "subscribe then cancel",
        "question": "after commitment?", "test_id": "T-z2"})
    assert core.promote(pid)["promoted"]
    cp = core.checkpoint()
    ans = core.record_human_answer(
        "T-z2", "B: cancel anytime after commitment")
    assert ans["resolved"], ans
    assert core.certify()["certified"]
    assert core.rollback(cp), "rollback failed"
    assert core.open_question("T-z2") is not None, \
        "rollback must restore the open question exactly"
    pid2 = session.propose("interpretation_batch", {
        "requirement": req,
        "candidates": [{"id": "E", "statement": "engine's choice",
                        "predicate": {"subject": "cancel",
                                      "property": "scope",
                                      "value": "engine"}}],
        "probe": "p", "question": "q", "test_id": "T-z2"})
    out = core.promote(pid2)
    assert not out["promoted"], "question redefined after rollback"
    assert "immutable" in out["reason"], out
    again = core.record_human_answer(
        "T-z2", "B: cancel anytime after commitment")
    assert again["resolved"], again
    assert core.certify()["certified"]


class ZombieRegressions(unittest.TestCase):

    def test_z1(self):
        """Z1 (D1/D3, collision + zero evidence): replay the minimized
        repro AND Z1's exclusive core-level scenario. Z1 is
        independently reproducible and detectable — a broken Z1 guard
        fails THIS test even with Z2 intact."""
        lab = Lab(28)
        for op in ["ingest", "open_session", "collision"]:
            lab.op(op)
        breaches, result = lab.detect()
        self.assertEqual(breaches, [])
        self.assertFalse(result["certified"],
                         "zero-evidence engine resolution certified")
        z1_scenario(TrustedCore)   # must not raise

    def test_z2(self):
        """Z2 (D3, answered-question immutability): the question is
        immutable once a human has answered it — against engine
        redefinition and across exact-state rollback. Independently
        detectable: a broken Z2 guard fails THIS test with Z1 intact."""
        z2_scenario(TrustedCore)   # must not raise

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
