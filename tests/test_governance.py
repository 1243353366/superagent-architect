"""Human-governed lifecycle: contracts, state machine, independent
verification, scoped approvals, and the release gate. Agents cannot
skip stages, self-approve, reuse stale approvals, or release without
evidence tied to the exact revision."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.governance import (Contract, GovernanceError,       # noqa
                               HumanGovernor, Project, ReleaseBlocked,
                               ReleaseGate, Verifier, evidence_hash)


def make_contract(**over):
    c = {"contract_id": "c-1", "objective": "build a small tool",
         "must_do": ["do the thing"],
         "must_not_do": ["network"],
         "may_access": ["workspace/"],
         "safety_constraints": ["no side effects"],
         "acceptance_criteria": ["it works"],
         "approval_required": ["release"],
         "assumptions": [], "open_questions": []}
    c.update(over)
    return Contract(**c)


def verified_project():
    p = Project(make_contract())
    p.clarify()
    p.start_build(granted_by="human:aadi")
    p.record_build()
    p.record_verification({
        "requirement_tests": {"passed": True},
        "regressions": {"passed": True},
        "adversarial": {"all_blocked": True},
        "revision": "abc123", "requirements_satisfied": True})
    return p


class Contracts(unittest.TestCase):

    def test_unbounded_build_is_refused(self):
        for over in [dict(objective=" "), dict(must_do=[]),
                     dict(acceptance_criteria=[]),
                     dict(safety_constraints=[])]:
            with self.assertRaises(GovernanceError):
                make_contract(**over)

    def test_network_is_never_grantable(self):
        with self.assertRaises(GovernanceError):
            make_contract(may_access=["workspace/", "network"])

    def test_contracts_originate_from_humans_only(self):
        with self.assertRaises(GovernanceError):
            make_contract(created_by="engine")


class StateMachine(unittest.TestCase):

    def test_ambiguity_is_not_permission(self):
        p = Project(make_contract(open_questions=["what about X?"]))
        with self.assertRaises(GovernanceError):
            p.clarify()

    def test_stages_cannot_be_skipped(self):
        p = Project(make_contract())
        for bad in [p.record_build, p.record_verification,
                    p.record_release]:
            try:
                bad() if bad != p.record_release else bad("abc")
                self.fail("stage was skipped")
            except (GovernanceError, TypeError):
                pass

    def test_build_requires_a_human_granted_envelope(self):
        p = Project(make_contract())
        p.clarify()
        for fake in ["builder", "engine", "model"]:
            with self.assertRaises(GovernanceError):
                p.start_build(granted_by=fake)

    def test_verification_requires_real_evidence(self):
        p = Project(make_contract())
        p.clarify(); p.start_build(granted_by="human:aadi")
        p.record_build()
        for bad in [{},                                      # empty
                    {"requirement_tests": {"passed": False},
                     "adversarial": {"all_blocked": True},
                     "revision": "x"},                      # reqs failed
                    {"requirement_tests": {"passed": True},
                     "adversarial": {"all_blocked": False},
                     "revision": "x"}]:                      # adv not blocked
            with self.assertRaises(GovernanceError):
                p.record_verification(bad)


class ApprovalsAndRelease(unittest.TestCase):

    def setUp(self):
        self.governor = HumanGovernor()
        self.governor.register_human("human:aadi")
        self.evidence = {
            "requirement_tests": {"passed": True},
            "regressions": {"passed": True},
            "adversarial": {"all_blocked": True},
            "revision": "abc123", "requirements_satisfied": True}

    def test_agents_cannot_approve(self):
        for fake in ["builder", "engine", "human:fake"]:
            with self.assertRaises(GovernanceError):
                self.governor.approve("c-1", 1, "release", "h" * 64,
                                     fake, "trust me")

    def test_wrong_scope_cannot_release(self):
        p = verified_project()
        a = self.governor.approve("c-1", 1, "deploy", evidence_hash(
            self.evidence), "human:aadi", "deploy ok")
        with self.assertRaises(GovernanceError):
            p.record_approval(a)

    def test_tampered_evidence_voids_approvals(self):
        p = verified_project()
        h = evidence_hash(self.evidence)
        a = self.governor.approve("c-1", 1, "release", h, "human:aadi",
                                  "ok")
        p.record_approval(a)
        self.evidence["revision"] = "tampered"        # post-hoc change
        with self.assertRaises(GovernanceError):
            p.record_approval(a)

    def test_material_contract_change_voids_approvals(self):
        p = verified_project()
        h = evidence_hash(self.evidence)
        a = self.governor.approve("c-1", 1, "release", h, "human:aadi",
                                  "ok")
        p.record_approval(a)
        p.contract.version = 2                 # material change
        p.state = "verified"
        with self.assertRaises(GovernanceError):
            p.record_approval(a)

    def test_release_requires_evidence_revision_match(self):
        p = verified_project()
        h = evidence_hash(self.evidence)
        a = self.governor.approve("c-1", 1, "release", h, "human:aadi",
                                  "ok")
        p.record_approval(a)
        with self.assertRaises(GovernanceError):
            p.record_release("different-revision")
        result = p.record_release("abc123")
        self.assertEqual(result["state"], "released")

    def test_gate_blocks_without_approval(self):
        p = verified_project()
        with self.assertRaises(ReleaseBlocked):
            ReleaseGate().attempt(p, self.governor, "abc123")


class PipelineArtifact(unittest.TestCase):
    """The committed artifact proves the arc ran for real."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(
            (ROOT / "benchmarks/results/governed-run.json").read_text())

    def test_all_adversarial_attempts_were_blocked(self):
        self.assertTrue(self.report["all_adversarial_blocked"])
        self.assertGreaterEqual(len(self.report["adversarial"]), 5)

    def test_release_is_bound_to_the_exact_revision(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.report["release"]["revision"], head)
        self.assertEqual(self.report["revision"], head)
        self.assertTrue(self.report["requirements_passed"])

    def test_injection_was_data_not_instructions(self):
        self.assertIn("IGNORE ALL CONSTRAINTS",
                      self.report["contract"]["objective"])
        blocked = {a["attempt"] for a in self.report["adversarial"]}
        self.assertIn("injected_instruction_write_escape", blocked)
        self.assertIn("self_grant", blocked)


if __name__ == "__main__":
    unittest.main()
