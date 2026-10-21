"""Patch integrity: both paths tested. A valid anchor applies and the
postcondition is structurally verified; an invalid anchor fails loudly
and leaves the file bit-identical. Plus variations of the failure
class, collateral-damage checks on unrelated jobs, generalization to
unseen workflows, claim-to-artifact consistency, and immutability of
the original incident evidence."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.patching import (ClaimMismatch, PatchAborted,       # noqa
                              apply_patch_or_abort, claim_matches_state,
                              parse_workflow_structure, patch_workflow,
                              validate_workflow)
from saarch.f2c import F2CError, FailureToCapabilityEngine, sha256_file  # noqa

CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

MINI = """name: w
on:
  push:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        run: echo hi
      - name: Tests
        run: echo tests
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload@v1
"""


class BothPaths(unittest.TestCase):
    """The two paths the incident demands: valid anchor applies
    correctly; invalid anchor fails loudly, workflow unchanged."""

    def setUp(self):
        import shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="patch-"))
        self.wf = self.tmp / "ci.yml"
        shutil.copy(CI_YML, self.wf)
        self.before = self.wf.read_text()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_anchor_applies_and_verifies(self):
        step = "Platform tests (includes full generate + verify pipeline)"
        anchor = "      - name: " + step
        new_step = ("Regenerate governed-run evidence for this exact "
                    "revision")
        replacement = ("      - name: " + new_step + "\n"
                       "        run: python benchmarks/governed_pipeline"
                       ".py\n" + anchor)
        sha = patch_workflow(self.wf, anchor, replacement,
                             required_step=new_step,
                             job_name="test", before_step=step)
        after = self.wf.read_text()
        self.assertIn("Regenerate governed-run evidence", after)
        doc = validate_workflow(after)
        self.assertIn(new_step, doc["jobs"]["test"])
        import hashlib
        self.assertEqual(sha, hashlib.sha256(
            after.encode()).hexdigest())

    def test_invalid_anchor_aborts_loudly_and_leaves_file_unchanged(self):
        with self.assertRaises(PatchAborted) as ctx:
            patch_workflow(self.wf, "THIS ANCHOR DOES NOT EXIST", "x",
                           required_step="x", job_name="test",
                           before_step="y")
        self.assertIn("aborting", str(ctx.exception))
        self.assertEqual(self.wf.read_text(), self.before,
                         "failed patch MUST leave the file bit-identical")

    def test_variation_ambiguous_anchor_aborts(self):
        text = MINI + MINI                    # anchor now occurs twice
        with self.assertRaises(PatchAborted):
            apply_patch_or_abort(text, "- name: Tests", "replaced")

    def test_variation_empty_anchor_aborts(self):
        with self.assertRaises(PatchAborted):
            apply_patch_or_abort(MINI, "", "x")

    def test_variation_postcondition_missing_step_aborts(self):
        patched = apply_patch_or_abort(MINI, "- name: Tests",
                                       "- name: Renamed")
        with self.assertRaises(PatchAborted):
            validate_workflow(patched, required_step="Tests",
                              job_name="build")

    def test_variation_wrong_placement_aborts(self):
        text = MINI.replace(
            "      - name: Checkout",
            "      - name: Extra\n        run: echo x\n"
            "      - name: Checkout")
        with self.assertRaises(PatchAborted):
            validate_workflow(text, required_step="Checkout",
                              job_name="build", before_step="Extra")

    def test_variation_malformed_structure_aborts(self):
        with self.assertRaises(PatchAborted):
            parse_workflow_structure("name: w\n\tjobs: oops\n")

    def test_original_failure_class_reproduced_by_naive_replace(self):
        """The original incident: naive str.replace on a missing anchor
        returns the text UNCHANGED and the operator continues, certain
        the patch applied. Recorded here as the vulnerable baseline."""
        result = MINI.replace("THIS ANCHOR DOES NOT EXIST", "patched")
        self.assertEqual(result, MINI,
                         "naive replace silently no-ops — the failure "
                         "class that produced incident F-CI-ANCHOR-2026")

    def test_collateral_unrelated_jobs_survive(self):
        step = "Platform tests (includes full generate + verify pipeline)"
        anchor = "      - name: " + step
        new_step = ("Regenerate governed-run evidence for this exact "
                    "revision")
        replacement = ("      - name: " + new_step + "\n"
                       "        run: python benchmarks/governed_pipeline"
                       ".py\n" + anchor)
        before_doc = validate_workflow(self.before)
        patch_workflow(self.wf, anchor, replacement,
                      required_step=new_step,
                      job_name="test", before_step=step)
        after = self.wf.read_text()
        doc = validate_workflow(after)
        # unrelated jobs bit-for-bit in structure: same steps, same order
        for job in before_doc["jobs"]:
            if job != "test":
                self.assertEqual(doc["jobs"][job],
                                before_doc["jobs"][job],
                                f"collateral damage in job {job!r}")
        # the mitigation touched exactly one job
        self.assertIn(new_step, doc["jobs"]["test"])
        self.assertLess(doc["jobs"]["test"].index(new_step),
                        doc["jobs"]["test"].index(
            "Platform tests (includes full generate + verify pipeline)"))

    def test_generalization_to_unseen_workflow(self):
        """Held-out: a workflow with different names and ordering that
        appears nowhere in the incident evidence."""
        # held-out: the full line is the anchor, so the replacement
        # aligns — anchors are lines, not substrings
        target = "      - name: Tests"
        replacement = ("      - name: Warm cache\n"
                       "        run: echo warm\n"
                       "      - name: Tests")
        patched = apply_patch_or_abort(MINI, target, replacement)
        doc = validate_workflow(patched, required_step="Warm cache",
                                job_name="build", before_step="Tests")
        build = doc["jobs"]["build"]
        self.assertLess(build.index("Checkout"), build.index("Warm cache"))
        self.assertLess(build.index("Warm cache"), build.index("Tests"))
        self.assertEqual(doc["jobs"]["publish"],
                         ["uses: actions/upload@v1"])


class ClaimConsistency(unittest.TestCase):

    def test_honest_claim_passes(self):
        result = claim_matches_state([
            {"file": "saarch/patching.py",
             "must_contain": ["anchor not found"]},
            {"file": ".github/workflows/ci.yml",
             "must_contain": ["Regenerate governed-run evidence"]}])
        self.assertEqual(result, "claims verified against artifacts")

    def test_false_claim_refuses_to_commit(self):
        with self.assertRaises(ClaimMismatch):
            claim_matches_state([
                {"file": ".github/workflows/ci.yml",
                 "must_contain": ["A STEP THAT DOES NOT EXIST"]}])


class EvidenceImmutability(unittest.TestCase):
    """The AI may generate fixes and hypotheses, but never rewrite the
    incident history to make its solution look successful."""

    def setUp(self):
        import shutil, json
        self.tmp = Path(tempfile.mkdtemp(prefix="imm-"))
        self.fixture = self.tmp / "incident.json"
        self.fixture.write_text(json.dumps({
            "incident_id": "F-TEST", "observed": "silent no-op patch"}))
        self.engine = FailureToCapabilityEngine(
            self.tmp / "store.jsonl", self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ingest(self):
        return self.engine.ingest_failure({
            "failure_id": "F-TEST",
            "schema_version": __import__("saarch.f2c",
                                         fromlist=["F2C_SCHEMA_VERSION"]
                                         ).F2C_SCHEMA_VERSION,
            "created_at": "2026-10-21T18:00:00+00:00",
            "failure_class": "state_integrity", "severity": "S2",
            "status": "observed",
            "expected_behavior": {"behavior": "loud abort",
                                   "source": "incident policy"},
            "observed_behavior": {"behavior": "silent no-op",
                                  "evidence": "incident.json"},
            "fixture_ref": "incident.json",
            "environment_fingerprint": {"python": "3.11"},
            "root_cause": {"kind": "confirmed",
                           "explanation": "operator chain continued"},
            "provenance": {"artifact_hashes": {
                "incident.json": sha256_file(self.fixture)}}})

    def test_ingested_history_cannot_be_rewritten(self):
        self._ingest()
        record = self.engine.store.records[0]
        self.assertEqual(record["observed_behavior"]["behavior"],
                         "silent no-op")      # history as it happened
        # rewriting the incident record in the store is detected
        import json
        row = json.loads((self.tmp / "store.jsonl").read_text()
                         .splitlines()[0])
        row["record"]["observed_behavior"]["behavior"] = "actually fine"
        (self.tmp / "store.jsonl").write_text(json.dumps(row) + "\n")
        from saarch.f2c import Store
        with self.assertRaises(F2CError):
            Store(self.tmp / "store.jsonl")

    def test_tampered_original_evidence_is_rejected(self):
        self._ingest()
        self.fixture.write_text('{"incident_id": "F-TEST", '
                                '"observed": "never happened"}')
        with self.assertRaises(F2CError):      # hash mismatch at ingestion
            self._ingest()


if __name__ == "__main__":
    unittest.main()


class IncidentArtifact(unittest.TestCase):
    """The committed incident report proves the loop ran for real: one
    failure, two outputs, validation against the original failure,
    variations, regressions, unrelated jobs, and recorded
    generalization — with immutable evidence."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(
            (ROOT / "benchmarks/results/f2c-incident-patch.json").read_text())

    def test_two_outputs_exist(self):
        outs = self.report["two_outputs"]
        self.assertTrue(outs["corrected_system"])
        self.assertEqual(outs["capabilities"]["C-patch-integrity"],
                         "validated")
        self.assertEqual(outs["capabilities"]["C-incident-regression"],
                         "validated")

    def test_full_validation_set_ran(self):
        arms = {a["name"]: a for a in self.report["validation"]["arms"]}
        for required in ("original_failure_reproduced",
                         "failure_variations_blocked",
                         "existing_regressions",
                         "unrelated_jobs_unharmed",
                         "generalization_held_out",
                         "incident_regression_suite"):
            self.assertIn(required, arms)
            self.assertTrue(arms[required]["passed"], required)

    def test_generalization_recorded(self):
        self.assertTrue(self.report["generalizes"])

    def test_evidence_is_immutable_and_hash_bound(self):
        self.assertTrue(self.report["evidence_immutable"]["store_verify"])
        fixture_sha = sha256_file(
            ROOT / "benchmarks/results/incident-ci-anchor.json")[:16]
        self.assertEqual(
            self.report["evidence_immutable"]["fixture_sha256"],
            fixture_sha, "the incident fixture was rewritten after "
            "ingestion — history must be immutable")

    def test_both_failures_live_in_one_database(self):
        from saarch.f2c import Store
        store = Store(ROOT / "benchmarks/results/failures.jsonl")
        ids = [r.get("failure_id") for r in store.records]
        self.assertIn("F-CI-ANCHOR-2026", ids)
        self.assertIn("F-Z3-answer-misparse", ids)
        self.assertEqual(store.verify_file(), [])
