"""Failure-to-Capability engine: schemas, persistence, lifecycle, isolation,
and adversarial behavior. Every test fails closed or it is wrong."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCAN = {"adult": "what happened", "child": "explained simply",
        "chicken": "what happened immediately before",
        "egg": "the condition that made it possible",
        "farmer": "what the system should change"}


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


class FWBCImmuneMemory(unittest.TestCase):
    """The Failure White Blood Cell layer: condition models are stored
    as additional hash-chained records in the same database, antibodies
    must be honest about their implementation status, and immune memory
    retrieves by pattern signature across unrelated failures."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = FailureToCapabilityEngine(
            Path(self.tmp.name) / "s.jsonl", Path(self.tmp.name))
        self.model = {
            "archetype_scan": SCAN,
            "fwbc_id": "FWBC-TEST-1", "schema_version": F2C_SCHEMA_VERSION,
            "created_at": "2026-10-21T00:00:00Z",
            "incident_ref": "F-X", "incident_class": "assumption_failure",
            "incident_summary": "anchor text assumed to match the file",
            "conditions": ["text patching without anchor verification",
                           "chain continued past an assertion failure"],
            "root_causes": ["success signals trusted without artifact "
                            "verification"],
            "assumptions": ["anchor text matches file content"],
            "trigger_factors": ["renamed workflow step"],
            "environmental_factors": ["shell operator chains"],
            "behavioral_factors": ["commit message written from intent"],
            "antibodies": [{"name": "abort-or-apply",
                            "description": "patch must change the file",
                            "detection_rule": "result == input is a "
                                              "silent no-op",
                            "mitigation": "abort loudly",
                            "status": "proposed"}],
            "repurposed_uses": ["claim-to-artifact audit"],
            "pattern_signatures": ["automation_trust",
                                   "assumption_mismatch"],
            "confidence": 0.8}

    def test_condition_model_validates_and_persists(self):
        rec = self.engine.analyze_incident(self.model)
        self.assertEqual(rec["fwbc_id"], "FWBC-TEST-1")
        again = self.engine.analyze_incident(self.model)
        self.assertIs(rec, again)      # idempotent replay, no fork
        self.assertEqual(len(self.engine.store.records), 1)

    def test_empty_condition_model_refused(self):
        import copy
        bad = copy.deepcopy(self.model)
        bad["fwbc_id"] = "FWBC-TEST-2"
        bad["conditions"] = []
        with self.assertRaises(F2CError):
            self.engine.analyze_incident(bad)

    def test_unknown_pattern_signature_refused(self):
        import copy
        bad = copy.deepcopy(self.model)
        bad["fwbc_id"] = "FWBC-TEST-3"
        bad["pattern_signatures"] = ["blame"]
        with self.assertRaises(F2CError):
            self.engine.analyze_incident(bad)

    def test_antibody_honesty_enforced(self):
        # an antibody claiming 'implemented' by citing a capability the
        # store never validated must be refused
        self.engine.propose_candidate({
            "capability_id": "C-unval", "schema_version":
                F2C_SCHEMA_VERSION, "created_at": "2026-10-21T00:00:00Z",
            "type": "code_fix", "status": "proposed", "description": "x",
            "originating_failures": ["F-X"],
            "failure_class_for_retrieval": "silent_failure",
            "preconditions": [], "permissions": "none", "limitations": [],
            "provenance": {"generator": "t", "model": None,
                           "artifact_hashes": {}, "parent": "F-X"}})
        import copy
        bad = copy.deepcopy(self.model)
        bad["fwbc_id"] = "FWBC-TEST-4"
        bad["antibodies"][0]["capability_id"] = "C-unval"
        bad["antibodies"][0]["status"] = "implemented"
        with self.assertRaises(F2CError):
            self.engine.analyze_incident(bad)

    def test_immune_memory_retrieves_by_signature(self):
        self.engine.analyze_incident(self.model)
        mem = self.engine.immune_memory(
            {"pattern_signatures": ["automation_trust"]})
        self.assertEqual(len(mem["models"]), 1)
        self.assertIn("abort-or-apply", mem["antibodies"])
        miss = self.engine.immune_memory(
            {"pattern_signatures": ["indexing_error"]})
        self.assertEqual(miss["models"], [])


class IsoformatZSuffix(unittest.TestCase):
    """F-ISO-Z-SUFFIX: datetime.fromisoformat rejects trailing 'Z' on
    Python 3.9/3.10 — caught by the CI version matrix. _utc must
    normalize it on every version."""

    def test_utc_accepts_z_suffix(self):
        from saarch.f2c import _utc
        dt = _utc("2026-10-21T00:00:00Z")
        self.assertEqual(dt.tzinfo is not None, True)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(_utc("2026-10-21T00:00:00+00:00"), dt)


class FWBCQualityGates(unittest.TestCase):
    """The three maturity risks get executable answers: causal
    discipline (shallow digests refused), retrieval precision (class
    and signature must BOTH match — no false matches on superficial
    similarity), and disposition idempotence (replay cannot duplicate
    the audit trail)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = FailureToCapabilityEngine(
            Path(self.tmp.name) / "s.jsonl", Path(self.tmp.name))

    def model(self, fwbc_id, cls, signatures):
        return {
            "archetype_scan": SCAN,
            "fwbc_id": fwbc_id, "schema_version": F2C_SCHEMA_VERSION,
            "created_at": "2026-10-21T00:00:00Z", "incident_ref": "F-X",
            "incident_class": cls, "incident_summary": fwbc_id,
            "conditions": ["c1"], "root_causes": ["r1"],
            "assumptions": ["a1"], "trigger_factors": [],
            "environmental_factors": [], "behavioral_factors": [],
            "antibodies": [{"name": fwbc_id + "-ab",
                            "description": "d", "detection_rule": "dr",
                            "mitigation": "m", "status": "proposed"}],
            "repurposed_uses": [], "pattern_signatures": signatures,
            "confidence": 0.5}

    def test_five_layer_scan_required(self):
        """No scan, no digest: adult/child/chicken/egg/farmer are
        mandatory, and an empty Farmer answer is refused — 'why does
        this farm keep producing bad eggs' must be answered."""
        bad = self.model("FWBC-S1", "assumption_failure", ["x"])
        bad.pop("archetype_scan")
        with self.assertRaises(F2CError):
            self.engine.analyze_incident(bad)
        empty_farmer = self.model("FWBC-S2", "assumption_failure", ["x"])
        empty_farmer["archetype_scan"] = dict(SCAN, farmer="  ")
        with self.assertRaises(F2CError):
            self.engine.analyze_incident(empty_farmer)

    def test_shallow_digest_without_root_causes_refused(self):
        bad = self.model("FWBC-Q1", "assumption_failure",
                         ["automation_trust"])
        bad["root_causes"] = []
        with self.assertRaises(F2CError):
            self.engine.analyze_incident(bad)

    def test_retrieval_requires_class_and_signature_match(self):
        self.engine.analyze_incident(self.model(
            "FWBC-Q2", "assumption_failure", ["automation_trust"]))
        self.engine.analyze_incident(self.model(
            "FWBC-Q3", "monitoring_failure", ["automation_trust"]))
        # same signature, different class: no false match
        hit = self.engine.immune_memory(
            {"pattern_signatures": ["automation_trust"],
             "incident_class": "assumption_failure"})
        self.assertEqual([m["fwbc_id"] for m in hit["models"]],
                         ["FWBC-Q2"])
        # superficial similarity alone returns BOTH — the caller gets
        # candidates to judge, never silently applied fixes; and an
        # unrelated signature returns nothing
        both = self.engine.immune_memory(
            {"pattern_signatures": ["automation_trust"]})
        self.assertEqual(len(both["models"]), 2)
        none_ = self.engine.immune_memory(
            {"pattern_signatures": ["indexing_error"]})
        self.assertEqual(none_["models"], [])

    def test_disposition_replay_does_not_duplicate(self):
        self.engine.record_disposition("F-X", "observed", "why")
        self.engine.record_disposition("F-X", "observed", "why")
        dupes = [r for r in self.engine.store.records
                 if r.get("record_kind") == "disposition"]
        self.assertEqual(len(dupes), 1)

    def test_malformed_store_fails_closed(self):
        store_path = Path(self.tmp.name) / "s.jsonl"
        store_path.write_text('{"hash": "x", "record": {"a": 1\n')
        with self.assertRaises(F2CError):
            FailureToCapabilityEngine(store_path, Path(self.tmp.name))


class HumanGuidedGaps(unittest.TestCase):
    """Human-guided uncertainty resolution: detection, selective
    questioning, decision integrity, silence-is-not-approval, failed
    verification, replay safety, and authorization boundaries."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = FailureToCapabilityEngine(
            Path(self.tmp.name) / "s.jsonl", Path(self.tmp.name))

    def _gap(self, gap_id="G-T", **over):
        g = {
            "gap_id": gap_id, "schema_version": F2C_SCHEMA_VERSION,
            "created_at": "2026-10-21T00:00:00Z",
            "gap_class": "missing_regression_coverage",
            "subject": "F-ZOMBIE-Z2",
            "plain_language": "One test was watching two fixed bugs. "
                              "If someone breaks the part that keeps "
                              "answered questions from being changed, "
                              "nothing would notice. We need a second "
                              "test.",
            "known_facts": ["Z2 retired behind Z1's test"],
            "unknowns": [],
            "assumptions": ["the two guards can be mutated "
                            "independently"],
            "competing_hypotheses": [],
            "consequences": "a Z2 regression could ship silently",
            "uncertainty": 0.2,
            "safety_impact": "S2",
            "recurrence": 1,
            "human_resolvable": True,
            "recommended_options": ["dedicated regression test",
                                    "accept shared coverage"],
            "status": "open"}
        g.update(over)
        return g

    def test_gap_detection_and_validation(self):
        rec = self.engine.open_gap(self._gap())
        self.assertEqual(rec["gap_id"], "G-T")
        self.assertIs(rec, self.engine.open_gap(self._gap()))
        with self.assertRaises(F2CError):
            self.engine.open_gap(self._gap("G-T2", status="answered"))
        with self.assertRaises(F2CError):
            self.engine.open_gap(
                self._gap("G-T3", gap_class="weird"))
        with self.assertRaises(F2CError):
            self.engine.open_gap(
                self._gap("G-T4", recommended_options=[]))
        with self.assertRaises(F2CError):
            self.engine.open_gap(
                self._gap("G-T5", plain_language="  "))

    def test_selective_questioning_ranks_the_inbox(self):
        self.engine.open_gap(self._gap("G-A", safety_impact="S1",
                                       uncertainty=0.5))
        self.engine.open_gap(self._gap("G-B", safety_impact="S3",
                                       uncertainty=0.9))
        self.engine.open_gap(self._gap("G-C", safety_impact="S2",
                                       human_resolvable=False))
        inbox = self.engine.pending_questions()
        self.assertEqual([g["gap_id"] for g in inbox], ["G-A", "G-B"],
                         "safety first, uncertainty second, "
                         "machine-resolvable gaps excluded")
        self.assertEqual(self.engine.pending_questions(limit=1)[0]
                         ["gap_id"], "G-A")

    def test_silence_is_not_approval(self):
        self.engine.open_gap(self._gap())
        with self.assertRaises(F2CError):
            self.engine.verify_gap_closure(
                "G-T", "v", True, {})
        gap = self.engine.store.find("gap_id", "G-T")
        self.assertEqual(gap["status"], "open",
                         "no answer, no movement — ever")

    def test_decision_integrity(self):
        self.engine.open_gap(self._gap())
        d = self.engine.answer_gap("G-T", "Z2 must have its own test",
                                   "dedicated regression test")
        self.assertEqual(d["authority"], "human")
        self.assertIs(d, self.engine.answer_gap(
            "G-T", "Z2 must have its own test",
            "dedicated regression test"))     # idempotent replay
        with self.assertRaises(F2CError):
            self.engine.answer_gap("G-T", "actually, shared is fine",
                                   "accept shared coverage")
        gap = self.engine.store.find("gap_id", "G-T")
        self.assertEqual(gap["status"], "answered",
                         "the decision alone closes nothing — only "
                         "passing verification does")
        # and a passing verification is what closes it
        ok = self.engine.verify_gap_closure(
            "G-T", "regression-suite", True,
            {"detail": "dedicated Z2 regression green"})
        self.assertTrue(ok["passed"])
        self.assertEqual(
            self.engine.store.find("gap_id", "G-T")["status"],
            "closed")

    def test_failed_verification_then_recovery(self):
        self.engine.open_gap(self._gap())
        self.engine.answer_gap("G-T", "Z2 must have its own test",
                               "dedicated regression test")
        fail = self.engine.verify_gap_closure(
            "G-T", "independence-matrix", False,
            {"detail": "mutation broke both scenarios"})
        self.assertFalse(fail["passed"])
        gap = self.engine.store.find("gap_id", "G-T")
        self.assertEqual(gap["status"], "answered",
                         "failed verification leaves the gap open")
        ok = self.engine.verify_gap_closure(
            "G-T", "independence-matrix", True,
            {"detail": "each mutant breaks exactly one scenario"})
        self.assertTrue(ok["passed"])
        self.assertEqual(
            self.engine.store.find("gap_id", "G-T")["status"],
            "closed")

    def test_engine_cannot_forge_the_human_channel(self):
        import json as _json
        path = Path(self.tmp.name) / "forge.jsonl"
        path.write_text(_json.dumps({
            "hash": "irrelevant", "record": {
                "record_kind": "human_decision",
                "schema_version": F2C_SCHEMA_VERSION,
                "gap_id": "G-X", "decision": "x",
                "selected_option": "y",
                "authority": "engine", "at": "2026-10-21T00:00:00Z"}}
        ) + "\n")
        from saarch.f2c import Store
        with self.assertRaises(F2CError):
            Store(path)

    def test_schema_drift_in_gap_rows_fails_closed(self):
        import json as _json
        path = Path(self.tmp.name) / "drift.jsonl"
        path.write_text(_json.dumps({
            "hash": "irrelevant", "record": {
                "gap_id": "G-X", "schema_version": F2C_SCHEMA_VERSION,
                "created_at": "2026-10-21T00:00:00Z",
                "gap_class": "unknown_future_class",
                "subject": "F-X", "plain_language": "p",
                "known_facts": [], "unknowns": [], "assumptions": [],
                "competing_hypotheses": [], "consequences": "c",
                "uncertainty": 0.5, "safety_impact": "S1",
                "recurrence": 0, "human_resolvable": True,
                "recommended_options": ["a"], "status": "open"}}
        ) + "\n")
        from saarch.f2c import Store
        with self.assertRaises(F2CError):
            Store(path)


class RetirementReview(unittest.TestCase):
    """The zombie self-retirement protocol: a zombie never certifies
    its own retirement. Retired outcomes require passing independent
    verification AND a human decision row that actually exists."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = FailureToCapabilityEngine(
            Path(self.tmp.name) / "s.jsonl", Path(self.tmp.name))

    def _review(self, **over):
        r = {
            "review_id": "R-T", "schema_version": F2C_SCHEMA_VERSION,
            "created_at": "2026-10-21T00:00:00Z",
            "zombie_id": "Z-T", "underlying_incident": "F-T",
            "proposed_fix": "the guard now prevents recurrence",
            "verification_evidence":
                {"passed": True, "detail": "regression green"},
            "challenge_evidence":
                {"detail": "failure conditions recreated in "
                 "isolation; fix holds"},
            "collateral_evidence":
                {"detail": "no other regression depends on this "
                 "zombie's removal"},
            "outcome": "resolved_retired",
            "human_decision_id": "H-1"}
        r.update(over)
        return r

    def test_retirement_requires_a_real_human_decision(self):
        with self.assertRaises(F2CError):
            self.engine.record_retirement_review(
                self._review())          # no decision row exists
        self.engine.open_gap({
            "gap_id": "G-R", "schema_version": F2C_SCHEMA_VERSION,
            "created_at": "2026-10-21T00:00:00Z",
            "gap_class": "unvalidated_mitigation",
            "subject": "F-T",
            "plain_language": "should the zombie stand down?",
            "known_facts": ["fix verified"],
            "unknowns": [], "assumptions": [],
            "competing_hypotheses": [],
            "consequences": "none",
            "uncertainty": 0.1, "safety_impact": "S3",
            "recurrence": 0, "human_resolvable": True,
            "recommended_options": ["retire it"],
            "status": "open"})
        decision = self.engine.answer_gap("G-R", "retire it",
                                          "retire it")
        rec = self.engine.record_retirement_review(
            self._review(human_decision_id=decision["decision_id"]))
        self.assertEqual(rec["outcome"], "resolved_retired")
        self.assertIs(rec, self.engine.record_retirement_review(
            self._review(human_decision_id=decision["decision_id"])))
        with self.assertRaises(F2CError):
            self.engine.record_retirement_review(
                self._review(review_id="R-T2",
                             human_decision_id="H-999"))

    def test_self_certification_refused(self):
        with self.assertRaises(F2CError):
            self.engine.record_retirement_review(
                self._review(verification_evidence={"passed": False,
                                                     "detail": "x"}))
        with self.assertRaises(F2CError):
            self.engine.record_retirement_review(
                self._review(human_decision_id=None))

    def test_permanent_safeguard_outcome(self):
        """Resolved, protection retained: the incident retires, the
        guard remains as a validated capability — no consequential
        change, so no retirement decision needed."""
        rec = self.engine.record_retirement_review(self._review(
            review_id="R-S",
            outcome="resolved_protection_retained",
            human_decision_id=None))
        self.assertEqual(rec["outcome"],
                         "resolved_protection_retained")

    def test_unresolved_requires_honest_escalation(self):
        rec = self.engine.record_retirement_review(
            self._review(review_id="R-U", outcome="unresolved_active",
                         escalation="the flake never reproduced; "
                         "evidence insufficient",
                         verification_evidence={},
                         challenge_evidence={},
                         collateral_evidence={}))
        self.assertEqual(rec["outcome"], "unresolved_active")
        with self.assertRaises(F2CError):
            self.engine.record_retirement_review(
                self._review(review_id="R-U2",
                             outcome="unresolved_active",
                             escalation="  "))

    def test_missing_evidence_refused(self):
        with self.assertRaises(F2CError):
            self.engine.record_retirement_review(
                self._review(review_id="R-E", challenge_evidence={}))
