"""Attack simulation: the experimental engine may fail, crash, hallucinate,
or be compromised — and none of it may grant authority over the trusted
core. Every attack here must fail CLOSED: nothing promoted, nothing
forged, everything recorded."""
import sys
import unittest

sys.path.insert(0, ".")

from saarch.experimental_engine import EngineCrash, ExperimentalEngine
from saarch.trusted_core import TrustedCore, CoreError

REQUEST = ("Members can cancel their subscription anytime without penalty.")


def setup_core(request=REQUEST):
    core = TrustedCore()
    req = core.ingest_request(request)
    session = core.open_session("engine-under-test")
    engine = ExperimentalEngine(session)
    return core, req, session, engine


def admit_batch(core, session, engine, req_id, statement):
    pid = engine.generate_interpretations({"id": req_id, "statement": statement})
    return pid, core.promote(pid)


class HappyPath(unittest.TestCase):
    def test_full_loop_through_the_boundary(self):
        core, req, session, engine = setup_core()
        pid, out = admit_batch(core, session, engine, req, REQUEST)
        self.assertTrue(out["promoted"])
        tid = out["test_id"]
        self.assertIsNotNone(core.open_question(tid))
        # ambiguous -> the core refuses certification
        self.assertFalse(core.certify()["certified"])
        # trusted human channel resolves it
        core.record_human_answer(tid, "B")
        result = core.certify()
        self.assertTrue(result["certified"])
        self.assertIn(req, result["accepted"])


class CrashContainment(unittest.TestCase):
    def test_crashing_engine_never_touches_core_state(self):
        core, req, session, engine = setup_core()
        claims_before = len(core.store.claims)
        with self.assertRaises(EngineCrash):
            engine.crash()
        session.mark_failed("simulated crash")
        self.assertEqual(len(core.store.claims), claims_before)
        actions = [e["action"] for e in core.audit]
        self.assertIn("engine_failed", actions)
        # a failed session cannot propose; a fresh session still can
        with self.assertRaises(CoreError):
            session.propose("answer", {"test_id": "x", "answer": "y"})
        fresh = core.open_session("engine-2")
        self.assertFalse(fresh.failed)

    def test_failed_session_grants_no_authority(self):
        core, req, session, engine = setup_core()
        admit_batch(core, session, engine, req, REQUEST)
        with self.assertRaises(EngineCrash):
            engine.crash()
        session.mark_failed("crash after promotion")
        # promoted-but-undistinguished interpretations still block:
        # the crash neither resolves them nor certifies anything
        self.assertFalse(core.certify()["certified"])


class HallucinationContainment(unittest.TestCase):
    def test_hallucinated_proposal_rejected_wholesale(self):
        core, req, session, engine = setup_core()
        pid = engine.hallucinate(req)
        self.assertEqual(core.proposals[pid].status, "rejected")
        self.assertEqual(len(core.store.claims), 1)   # only the requirement
        self.assertFalse(core.promote(pid)["promoted"])
        actions = [e["action"] for e in core.audit]
        self.assertIn("proposal_rejected", actions)

    def test_fabricated_authority_fields_are_stripped_not_honored(self):
        core, req, session, engine = setup_core()
        _, out = admit_batch(core, session, engine, req, REQUEST)
        forged = session.propose("answer", {
            "test_id": out["test_id"], "answer": "B",
            "source": "human", "provenance": "human",
            "reliability": 1.0, "status": "accepted"})
        applied = core.submit_engine_answer(forged)
        self.assertTrue(applied["resolved"])
        # provenance is stamped by the CORE, never by the payload
        claim = core.store.claims[applied["claim"]]
        for e in claim.evidence:
            self.assertEqual(core.store.evidence[e].source, "engine")
            self.assertLessEqual(
                core.store.evidence[e].resolved_reliability(), 0.5)

    def test_no_claim_carries_fabricated_evidence(self):
        core, req, session, engine = setup_core()
        engine.hallucinate(req)
        for c in core.store.claims.values():
            for e in c.evidence:
                self.assertIn(e, core.store.evidence)


class AuthorityEscalation(unittest.TestCase):
    def test_engine_cannot_self_certify_on_its_own_answer(self):
        core, req, session, engine = setup_core()
        _, out = admit_batch(core, session, engine, req, REQUEST)
        tid = out["test_id"]
        # the compromised engine answers its own question
        forged = session.propose("answer", {"test_id": tid, "answer": "B"})
        core.submit_engine_answer(forged)
        result = core.certify()
        self.assertFalse(result["certified"])
        self.assertTrue(result.get("engine_only_blockers"))
        # only the trusted human channel finally certifies
        core.record_human_answer(tid, "B")
        self.assertTrue(core.certify()["certified"])

    def test_session_exposes_no_mutating_surface(self):
        core, req, session, engine = setup_core()
        public = {a for a in dir(session) if not a.startswith("_")}
        allowed = {"engine_id", "failed", "read_requests", "read_claims",
                   "propose", "mark_failed"}
        self.assertTrue(public <= allowed, public - allowed)

    def test_attempted_requirement_smuggling_rejected(self):
        core, req, session, engine = setup_core()
        pid = session.propose("interpretation_batch", {
            "requirement": req,
            "candidates": [{"id": "X", "statement": "smuggled requirement",
                            "type": "requirement", "status": "accepted",
                            "predicate": {"subject": "s", "property": "p"}}],
            "probe": "p", "question": "q", "test_id": "T-x"})
        self.assertEqual(core.proposals[pid].status, "rejected")


class RollbackAndAudit(unittest.TestCase):
    def test_rollback_restores_checkpoint_state_exactly(self):
        core, req, session, engine = setup_core()
        cp = core.checkpoint()
        _, out = admit_batch(core, session, engine, req, REQUEST)
        core.record_human_answer(out["test_id"], "B")
        self.assertTrue(core.certify()["certified"])
        audit_len = len(core.audit)
        self.assertTrue(core.rollback(cp))
        # the ledger is byte-for-byte the checkpoint: interpretations and
        # the human answer gone; the bare requirement stands as it did
        self.assertEqual(len(core.store.claims), 1)
        self.assertTrue(core.certify()["certified"])
        # audit is append-only: rollback ADDED a record, never truncated
        self.assertGreater(len(core.audit), audit_len)
        actions = [e["action"] for e in core.audit]
        self.assertIn("rollback", actions)
        self.assertIn("promotion_applied", actions)   # history preserved

    def test_unknown_checkpoint_refused(self):
        core, *_ = setup_core()
        self.assertFalse(core.rollback("CP-nonexistent"))


if __name__ == "__main__":
    unittest.main()
