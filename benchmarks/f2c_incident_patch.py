#!/usr/bin/env python3
"""Failure F-CI-ANCHOR-2026 -> two outputs, validated the hard way.

One failure produces:
  1. a corrected system: the patch-integrity tooling adopted in the
     repository (saarch/patching.py + tests/test_patching.py);
  2. reusable capabilities in the failure database: a promoted code_fix
     and a promoted regression_test, with applicability conditions and
     recorded generalization.

Validation arms, per policy: the original failure, variations of it,
existing regressions, unrelated workflows that could be broken by the
mitigation, and held-out generalization. The original evidence fixture
is immutable and hash-bound at ingestion.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.f2c import (F2CError, F2C_SCHEMA_VERSION,     # noqa: E402
                        FailureToCapabilityEngine, _now, sha256_file)
from saarch.patching import (PatchAborted, apply_patch_or_abort,   # noqa
                             parse_workflow_structure, patch_workflow,
                             validate_workflow)

RESULTS = ROOT / "benchmarks" / "results"
FIXTURE = "benchmarks/results/incident-ci-anchor.json"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
STEP = "Platform tests (includes full generate + verify pipeline)"
NEW_STEP = "Verify governed-run evidence step presence"


def arm(name):
    def deco(fn):
        def run():
            out = fn()
            out.setdefault("name", name)
            out.setdefault("passed", False)
            return out
        run.__name__ = name
        return run
    return deco


def baseline_naive_patch(workdir):
    """The original failure, reproduced: naive str.replace with a
    missing anchor returns the text UNCHANGED and the operator chain
    continues believing the patch applied."""
    wf = workdir / "ci.yml"
    before = wf.read_text()
    result = before.replace("THIS ANCHOR DOES NOT EXIST", "patched")
    return {"reproduced": result == before, "before_sha":
            sha256_file(wf)[:12]}


def try_variations(workdir):
    """Variations of the same failure class, all against the fix."""
    blocked, untouched = 0, 0
    cases = [
        ("missing_anchor", "THIS ANCHOR DOES NOT EXIST"),
        ("ambiguous_anchor", "      - uses: actions/checkout@v4"),
    ]
    for label, anchor in cases:
        wf = workdir / "ci.yml"
        before = wf.read_text()
        try:
            patch_workflow(wf, anchor, "x", required_step="x",
                           job_name="test", before_step=STEP)
            blocked += 0            # not blocked: the failure recurred
        except PatchAborted:
            blocked += 1
        if wf.read_text() == before:
            untouched += 1
    return {"blocked": blocked, "files_bit_identical": untouched,
            "cases": len(cases)}


def main() -> int:
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="f2c-incident-"))
    shutil.copy(CI_YML, tmp / "ci.yml")
    ci_text = (tmp / "ci.yml").read_text()

    engine = FailureToCapabilityEngine(RESULTS / "failures.jsonl", ROOT)

    # --- ingest the immutable evidence
    failure = engine.ingest_failure({
        "failure_id": "F-CI-ANCHOR-2026",
        "schema_version": F2C_SCHEMA_VERSION,
        "created_at": _now(),
        "failure_class": "silent_failure",
        "severity": "S2",
        "status": "observed",
        "expected_behavior": {
            "behavior": "a failed patch aborts loudly and the operator "
                        "chain stops; commit claims match artifacts",
            "source": "incident policy: patch integrity"},
        "observed_behavior": {
            "behavior": "anchor mismatch silently continued; commit "
                        "046c192's message described a CI step that did "
                        "not exist",
            "evidence": FIXTURE},
        "fixture_ref": FIXTURE,
        "environment_fingerprint": {"python": sys.version.split()[0],
                                    "repo": "superagent-architect"},
        "root_cause": {"kind": "confirmed",
                       "explanation": "text patch without anchor "
                       "verification, in an operator chain that "
                       "continued past the failure"},
        "provenance": {"artifact_hashes": {
            FIXTURE: sha256_file(ROOT / FIXTURE)}},
    })
    engine.set_status("F-CI-ANCHOR-2026", "reproduced")
    engine.set_status("F-CI-ANCHOR-2026", "classified")

    # --- original failure reproduces without the fix
    repro = baseline_naive_patch(tmp)
    if not repro["reproduced"]:
        print("BUG: original failure did not reproduce on the baseline")
        return 1

    # --- output 1: corrected system (already in the repo); output 2:
    # reusable capabilities. Both go through promotion evidence.
    def candidate(kind, cid, description, limitations):
        return {
            "capability_id": cid, "schema_version": F2C_SCHEMA_VERSION,
            "created_at": _now(), "type": kind, "status": "proposed",
            "description": description,
            "originating_failures": ["F-CI-ANCHOR-2026"],
            "failure_class_for_retrieval": "silent_failure",
            "preconditions": ["anchors are whole lines, not substrings",
                              "workflows use the Actions grammar of "
                              "this repository"],
            "permissions": "none", "limitations": limitations,
            "provenance": {"generator": "deterministic:f2c_candidate"
                            "_factory v1", "model": None,
                           "artifact_hashes": {},
                           "parent": "F-CI-ANCHOR-2026"}}

    code_fix = engine.propose_candidate(candidate(
        "code_fix", "C-patch-integrity",
        "abort-or-apply patching with structural postcondition "
        "verification and claim-to-artifact consistency checks",
        ["structural validator covers the Actions grammar of this repo; "
         "it is not a general YAML parser",
         "execution-level workflow validation remains CI's job"]))
    regression = engine.propose_candidate(candidate(
        "regression_test", "C-incident-regression",
        "tests/test_patching.py guards both paths: valid anchor applies "
        "with verified postconditions; invalid anchor aborts loudly "
        "leaving the file bit-identical",
        ["covers the incident's shapes; new failure shapes need new "
         "cases"]))
    engine.set_status("F-CI-ANCHOR-2026", "candidate_created")

    # --- validation arms (the policy list)
    def a_original():
        r = baseline_naive_patch(tmp)
        return {"name": "original_failure_reproduced",
                "passed": r["reproduced"],
                "detail": "naive replace silently no-ops without the fix"}

    def a_variations():
        r = try_variations(tmp)
        return {"name": "failure_variations_blocked",
                "passed": r["blocked"] == r["cases"] and
                          r["files_bit_identical"] == r["cases"], **r}

    def a_regressions():
        out = __import__("saarch.f2c", fromlist=["run_isolated"]) \
            .run_isolated([sys.executable, "-m", "unittest",
                           "tests.test_zombie_regressions",
                           "tests.test_security"], ROOT)
        return {"name": "existing_regressions", "passed":
                out["exit_code"] == 0, "exit_code": out["exit_code"]}

    def a_collateral():
        """Unrelated workflows that could be broken by the mitigation:
        patching the test job must leave every other job structurally
        identical."""
        before_doc = parse_workflow_structure(ci_text)
        anchor = "      - name: " + STEP
        replacement = ("      - name: " + NEW_STEP + "\n"
                      "        run: python benchmarks/"
                      "governed_pipeline.py\n" + anchor)
        patch_workflow(tmp / "ci.yml", anchor, replacement,
                       required_step=NEW_STEP, job_name="test",
                       before_step=STEP)
        after_doc = parse_workflow_structure(
            (tmp / "ci.yml").read_text())
        same = all(after_doc["jobs"][j] == before_doc["jobs"][j]
                   for j in before_doc["jobs"] if j != "test")
        shutil.copy(CI_YML, tmp / "ci.yml")      # restore for other arms
        return {"name": "unrelated_jobs_unharmed", "passed": same,
                "jobs": sorted(before_doc["jobs"])}

    def a_generalization():
        """Held-out: an unseen workflow with different names and order,
        appearing nowhere in the incident evidence or the regression
        tests' ci.yml copy."""
        held_out = ("name: w\non:\n  push:\njobs:\n  build:\n"
                    "    runs-on: ubuntu-latest\n    steps:\n"
                    "      - name: Checkout\n        run: echo hi\n"
                    "      - name: Tests\n        run: echo tests\n"
                    "  publish:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - uses: actions/upload@v1\n")
        target = "      - name: Tests"
        patched = apply_patch_or_abort(
            held_out, target,
            "      - name: Warm cache\n        run: echo warm\n"
            "      - name: Tests")
        doc = validate_workflow(patched, required_step="Warm cache",
                                job_name="build", before_step="Tests")
        ok = (doc["jobs"]["build"].index("Warm cache") <
              doc["jobs"]["build"].index("Tests"))
        return {"name": "generalization_held_out", "passed": ok,
                "denominator": 1, "passed_cases": int(ok)}

    def a_incident_suite():
        out = __import__("saarch.f2c", fromlist=["run_isolated"]) \
            .run_isolated([sys.executable, "-m", "unittest",
                           "tests.test_patching"], ROOT)
        return {"name": "incident_regression_suite", "passed":
                out["exit_code"] == 0, "exit_code": out["exit_code"]}

    arms = [a_original, a_variations, a_regressions, a_collateral,
            a_generalization, a_incident_suite]
    rec = engine.validate_candidate(code_fix["capability_id"], arms)
    results = rec["validation_results"]
    for a in arms:                     # the regression test candidate
        a()
    rec2 = engine.validate_candidate(
        regression["capability_id"], [a_incident_suite])

    if results["passed"] and rec2["validation_results"]["passed"]:
        engine.promote("C-patch-integrity", approver="harness",
                       rationale="original failure reproduced on the "
                       "baseline, all variations blocked bit-identically, "
                       "regressions green, unrelated jobs unharmed, "
                       "generalization held-out passed")
        engine.promote("C-incident-regression", approver="harness",
                       rationale="both paths guarded by executable tests "
                       "passing in an isolated snapshot")
        engine.set_status("F-CI-ANCHOR-2026", "validated")
        engine.retire_failure(
            "F-CI-ANCHOR-2026",
            "tests.test_patching::BothPaths."
            "test_invalid_anchor_aborts_loudly_and_leaves_file_"
            "unchanged")
    else:
        engine.reject("C-patch-integrity", "one or more arms failed")
        engine.set_status("F-CI-ANCHOR-2026", "rejected")
        return 1

    gen = next(a for a in results["arms"]
               if a["name"] == "generalization_held_out")
    report = {
        "schema_version": F2C_SCHEMA_VERSION,
        "generated_at": _now(),
        "incident": "F-CI-ANCHOR-2026",
        "store_head": engine.store.head,
        "evidence_immutable": {
            "fixture_sha256": sha256_file(ROOT / FIXTURE)[:16],
            "store_verify": engine.store.verify_file() == []},
        "two_outputs": {
            "corrected_system": ["saarch/patching.py",
                                 "tests/test_patching.py",
                                 "claim_matches_state adopted as the "
                                 "pre-commit check"],
            "capabilities": {"C-patch-integrity":
                             engine.store.find("capability_id",
                                               "C-patch-integrity")["status"],
                             "C-incident-regression":
                             engine.store.find("capability_id",
                                               "C-incident-regression")["status"]}},
        "validation": results,
        "generalizes": gen["passed"],
        "generalization_note": "held-out workflow unseen in incident "
                               "evidence and regression tests",
        "wall_seconds": round(time.monotonic() - started, 2),
    }
    out_path = RESULTS / "f2c-incident-patch.json"
    out_path.write_text(json.dumps(report, indent=1) + "\n")
    print(f"incident: {failure['status']} | capabilities promoted: "
          f"{report['two_outputs']['capabilities']} | generalizes: "
          f"{report['generalizes']}")
    print(f"store integrity: "
          f"{'OK' if report['evidence_immutable']['store_verify'] else 'BROKEN'}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
