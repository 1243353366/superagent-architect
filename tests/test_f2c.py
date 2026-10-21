"""Failure-to-Capability engine: schemas, persistence, lifecycle, isolation,
and adversarial behavior. Every test fails closed or it is wrong."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.f2c import (F2CError, F2C_SCHEMA_VERSION,          # noqa: E402
                        FailureToCapabilityEngine, Store, run_isolated,
                        sha256_file)

FIXTURE = "benchmarks/results/minimized-D5_wrong_resolution.json"


def valid_failure(**overrides):
    rec = {
        "failure_id": "F-test-1", "schema_version": F2C_SCHEMA_VERSION,
        "created_at": "2026-10-21T13:00:00+00:00",
        "failure_class": "answer_parsing", "severity": "S2",
        "status": "observed",
        "expected_behavior": {"behavior": "x", "source": "spec"},
        "observed_behavior": {"behavior": "y", "evidence": "log"},
        "fixture_ref": FIXTURE,
        "environment_fingerprint": {"python": "3.11"},
        "root_cause": {"kind": "unknown"},
        "provenance": {"artifact_hashes": {
            FIXTURE: sha256_file(ROOT / FIXTURE)}},
    }
    rec.update(overrides)
    return rec


def valid_capability(**overrides):
    rec = {
        "capability_id": "C-test-1", "schema_version": F2C_SCHEMA_VERSION,
        "created_at": "2026-10-21T13:00:00+00:00",
        "type": "strategy", "status": "proposed",
        "description": "do the right thing",
        "originating_failures": ["F-test-1"],
        "failure_class_for_retrieval": "answer_parsing",
        "preconditions": ["p"], "permissions": "none",
        "limitations": ["l"], "provenance": {"artifact_hashes": {}},
    }
    rec.update(overrides)
    return rec


class TempStoreTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="f2c-test-"))
        self.store_path = self.tmp / "failures.jsonl"
        self.engine = FailureToCapabilityEngine(self.store_path, ROOT)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class Schemas(TempStoreTestCase):

    def test_round_trip_without_semantic_loss(self):
        rec = self.engine.ingest_failure(valid_failure())
        loaded = Store(self.store_path).records[0]
        self.assertEqual(loaded, rec)
        self.assertEqual(json.loads(json.dumps(rec)), rec)

    def test_unsupported_schema_version_fails_closed(self):
        with self.assertRaises(F2CError):
            self.engine.ingest_failure(valid_failure(schema_version="0.9"))

    def test_retrieval_boundary_rejects_incompatible_store(self):
        self.engine.ingest_failure(valid_failure())
        row = json.loads(self.store_path.read_text().splitlines()[0])
        row["record"]["schema_version"] = "0.9"   # forge an old-version row
        self.store_path.write_text(
            json.dumps({"hash": row["hash"], "record": row["record"]}) + "\n")
        with self.assertRaises(F2CError):          # hash AND version checked
            Store(self.store_path)

    def test_missing_unknown_and_malformed_fields_fail_closed(self):
        base = valid_failure()
        for mutate in [
            lambda r: r.pop("severity"),
            lambda r: r.update(trust_me=True),
            lambda r: r.update(severity="S99"),
            lambda r: r.update(failure_class=" vibes"),
            lambda r: r.update(status="fixed_but_no_test"),
            lambda r: r.update(created_at="2026-10-21T13:00:00"),
            lambda r: r.update(root_cause="definitely a bug"),
        ]:
            with self.assertRaises(F2CError):
                self.engine.ingest_failure(mutate(dict(base)))

    def test_facts_and_hypotheses_stay_separate(self):
        with self.assertRaises(F2CError):
            self.engine.ingest_failure(
                valid_failure(root_cause={"kind": "proven-fact"}))


class EvidenceAndStore(TempStoreTestCase):

    def test_broken_fixture_reference_fails_closed(self):
        with self.assertRaises(F2CError):
            self.engine.ingest_failure(
                valid_failure(fixture_ref="benchmarks/results/missing.json"))

    def test_altered_artifact_is_detected(self):
        forged = self.tmp / "forged.json"
        forged.write_text("{}")
        engine = FailureToCapabilityEngine(
            self.tmp / "store.jsonl", self.tmp)
        rec = valid_failure(
            fixture_ref="forged.json",
            provenance={"artifact_hashes": {
                "forged.json": sha256_file(forged)}})
        engine.ingest_failure(rec)
        forged.write_text('{"tampered": true}')
        with self.assertRaises(F2CError):        # hash mismatch at ingestion
            engine.ingest_failure(valid_failure(failure_id="F-test-2"))

    def test_ledger_tampering_is_detected(self):
        self.engine.ingest_failure(valid_failure())
        row = json.loads(self.store_path.read_text().splitlines()[0])
        row["record"]["severity"] = "S1"          # mutate after chaining
        self.store_path.write_text(json.dumps(row) + "\n")
        with self.assertRaises(F2CError):         # detected at load
            Store(self.store_path)

    def test_duplicate_submissions_are_idempotent(self):
        first = self.engine.ingest_failure(valid_failure())
        second = self.engine.ingest_failure(valid_failure())
        self.assertIs(first, second)
        self.assertEqual(len(Store(self.store_path).records), 1)

    def test_conflicting_duplicate_is_refused(self):
        self.engine.ingest_failure(valid_failure())
        with self.assertRaises(F2CError):
            self.engine.ingest_failure(valid_failure(severity="S3"))

    def test_interrupted_write_is_detected(self):
        self.engine.ingest_failure(valid_failure())
        with self.store_path.open("a") as fh:      # torn append
            fh.write('{"hash": "partial')
        with self.assertRaises(F2CError):         # detected at load
            Store(self.store_path)


class Lifecycle(TempStoreTestCase):

    def test_valid_transitions_only(self):
        self.engine.ingest_failure(valid_failure())
        self.engine.set_status("F-test-1", "reproduced")
        self.engine.set_status("F-test-1", "classified")
        self.engine.set_status("F-test-1", "candidate_created")
        with self.assertRaises(F2CError):         # skipped validated
            self.engine.set_status("F-test-1", "retired")

    def test_retirement_requires_a_real_regression_test(self):
        self.engine.ingest_failure(valid_failure())
        self.engine.set_status("F-test-1", "reproduced")
        self.engine.set_status("F-test-1", "classified")
        self.engine.set_status("F-test-1", "candidate_created")
        self.engine.set_status("F-test-1", "validated")
        with self.assertRaises(F2CError):
            self.engine.retire_failure(
                "F-test-1", "tests.test_missing_module::test_nothing")
        retired = self.engine.retire_failure(
            "F-test-1", "tests.test_zombie_regressions::test_z3")
        self.assertEqual(retired["status"], "retired")
        self.assertEqual(retired["disposition"]["regression_test"],
                         "tests.test_zombie_regressions::test_z3")


class Adversarial(TempStoreTestCase):

    def test_candidate_cannot_bypass_schema_validation(self):
        self.engine.ingest_failure(valid_failure())
        for payload in [
            valid_capability(validation_results={"passed": True}),
            valid_capability(regression_results={"all": "green"}),
            valid_capability(promotion_history=[{"action": "promoted"}]),
            valid_capability(permissions="trusted"),
            valid_capability(capability_id=None),
            valid_capability(status="validated"),
        ]:
            with self.assertRaises(F2CError):
                self.engine.propose_candidate(payload)

    def test_rejected_candidates_remain_auditable(self):
        self.engine.ingest_failure(valid_failure())
        with self.assertRaises(F2CError):
            self.engine.propose_candidate(
                valid_capability(capability_id="C-bad", trust_me=1))
        rec = self.engine.store.find("capability_id", "C-bad")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "rejected")

    def test_harness_owns_the_verdict(self):
        self.engine.ingest_failure(valid_failure())
        cap = self.engine.propose_candidate(valid_capability())
        with self.assertRaises(F2CError):         # no arms, no verdict
            self.engine.promote(cap["capability_id"], "harness", "trust me")
        rec = self.engine.validate_candidate(cap["capability_id"], [
            lambda: {"name": "one_case", "passed": True},
            lambda: {"name": "regression", "passed": False},
        ], required=["one_case", "regression"])
        self.assertFalse(rec["validation_results"]["passed"],
                         "a fix that breaks a regression must be rejected")
        with self.assertRaises(F2CError):         # cannot promote a failure
            self.engine.promote(cap["capability_id"], "harness", "fixes it")

    def test_missing_required_arm_is_an_error(self):
        self.engine.ingest_failure(valid_failure())
        cap = self.engine.propose_candidate(valid_capability())
        with self.assertRaises(F2CError):
            self.engine.validate_candidate(cap["capability_id"], [
                lambda: {"name": "a", "passed": True}],
                required=["a", "never_ran"])

    def test_promotion_is_reversible(self):
        self.engine.ingest_failure(valid_failure())
        cap = self.engine.propose_candidate(valid_capability())
        self.engine.validate_candidate(cap["capability_id"], [
            lambda: {"name": "all", "passed": True}])
        self.engine.promote(cap["capability_id"], "harness", "evidence ok")
        demoted = self.engine.demote(cap["capability_id"], "new evidence")
        self.assertEqual(demoted["status"], "deprecated")
        self.assertEqual(demoted["promotion_history"][-1]["action"],
                         "deprecated")


class Isolation(unittest.TestCase):
    """A candidate may run, fail, or misbehave — the trusted core and the
    real repository must come out unchanged."""

    def test_candidate_execution_cannot_touch_the_real_repo(self):
        before = sha256_file(ROOT / "saarch" / "f2c.py")
        out = run_isolated([sys.executable, "-c",
                            "import pathlib; "
                            "pathlib.Path('MARKER').write_text('x'); "
                            "pathlib.Path('saarch/f2c.py').write_text('')"],
                           ROOT)
        self.assertFalse((ROOT / "MARKER").exists(),
                         "candidate escaped its sandbox into the real repo")
        self.assertEqual(sha256_file(ROOT / "saarch" / "f2c.py"), before,
                         "candidate rewrote trusted-core code")

    def test_validation_runs_from_a_throwaway_snapshot(self):
        out = run_isolated([sys.executable, "-m", "unittest",
                            "tests.test_zombie_regressions"], ROOT)
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["cwd"], "throwaway snapshot")


class PipelineReport(unittest.TestCase):
    """The committed report is generated from real artifacts and is bound
    to its schema version and store state."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(
            (ROOT / "benchmarks/results/f2c-report.json").read_text())

    def test_report_is_bound_to_schema_and_store(self):
        self.assertEqual(self.report["schema_version"], F2C_SCHEMA_VERSION)
        store = Store(ROOT / "benchmarks/results/failures.jsonl")
        self.assertEqual(self.report["store_head"], store.head)
        self.assertEqual(store.verify_file(), [])

    def test_memorization_is_never_reported_as_generalization(self):
        arms = self.report["validation"]["strategy_candidate"]["arms"]
        held = next(a for a in arms if a["name"] == "held_out_generalization")
        self.assertLess(held["passed_cases"], held["denominator"],
                        "strategy must not claim full held-out success")
        self.assertTrue(held["failures"],
                        "the surviving-variant evidence must be recorded")
        self.assertEqual(
            self.report["lifecycle"]["candidates"]["C-z3-whole-token-matching"],
            "rejected")

    def test_promotion_happened_only_for_the_narrow_claim(self):
        self.assertEqual(
            self.report["lifecycle"]["candidates"]["C-z3-regression-guard"],
            "validated")
        self.assertEqual(self.report["lifecycle"]["failure_status"],
                         "retired")

    def test_rejected_hostile_payloads_are_auditable(self):
        for entry in self.report["lifecycle"]["hostile_payloads_rejected"]:
            self.assertIn("reason", entry)


if __name__ == "__main__":
    unittest.main()
