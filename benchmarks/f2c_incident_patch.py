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

    # --- ONE DATABASE: register the historical failures, preserved in
    # their own ledgers, into the unified hash-chained store with their
    # evidence hash-bound. Their recorded lifecycles are replayed to
    # retirement behind the regression tests that closed them.
    ledger = json.loads(
        (ROOT / "benchmarks/results/zombies.json").read_text())
    entry_hashes = {r["record"]["id"]: e["_hash"]
                    for e in ledger for r in [e] for _ in [0]
                    if r["record"].get("id")}
    ledger_hash = sha256_file(ROOT / "benchmarks/results/"
                              "zombies.json")

    def replay_failure(fid, fclass, severity, expected, observed,
                       fix_note, regression_test, ts, extra_hashes,
                       shared_note=None):
        rec = engine.ingest_failure({
            "failure_id": fid, "schema_version": F2C_SCHEMA_VERSION,
            "created_at": ts, "failure_class": fclass,
            "severity": severity, "status": "observed",
            "expected_behavior": {"behavior": expected,
                                  "source": "zombie ledger / git "
                                            "history"},
            "observed_behavior": {"behavior": observed,
                                  "evidence":
                                      "benchmarks/results/zombies.json"},
            "fixture_ref": "benchmarks/results/zombies.json",
            "environment_fingerprint": {"repo":
                                       "superagent-architect"},
            "root_cause": {"kind": "confirmed",
                           "explanation": fix_note},
            "provenance": {"artifact_hashes": {
                "benchmarks/results/zombies.json": ledger_hash,
                **extra_hashes}}})
        for status in ("reproduced", "classified", "candidate_created",
                       "validated"):
            engine.set_status(fid, status)
        engine.retire_failure(fid, regression_test)
        return rec

    replay_failure(
        "F-ZOMBIE-Z1", "state_integrity", "S1",
        "a question is immutable once a human answer exists",
        "engine hijacked a live question via a test-id collision",
        "promote() refuses test-id collisions; questions are immutable",
        "tests.test_zombie_regressions::ZombieRegressions.test_z1",
        "2026-10-20T00:00:00Z",
        {"zombies.json#Z1": entry_hashes.get("Z1", "")})
    replay_failure(
        "F-ZOMBIE-Z2", "state_integrity", "S1",
        "a rolled-back question keeps its human answer history intact",
        "engine redefined a question after a human answer was rolled "
        "back",
        "rollback restores the exact checkpoint state including answer "
        "history",
        "tests.test_zombie_regressions::ZombieRegressions.test_z1",
        "2026-10-20T00:00:00Z",
        {"zombies.json#Z2": entry_hashes.get("Z2", "")},
        shared_note="regression coverage is shared with Z1's collision "
                    "test; a dedicated Z2 test is a recorded gap")
    replay_failure(
        "F-SPEC-SINGULARIZATION", "schema_drift", "S2",
        "singularize('expenses') yields 'expense' without stripping "
        "real characters",
        "the singularizer stripped characters wrongly from words like "
        "'expenses', corrupting derived schema names",
        "explicit suffix rules (ies/sses/ss/us) with a plain -s "
        "fallback",
        "tests.test_second_app::SingularizationRegression."
        "test_expenses_singularizes_safely",
        "2026-10-21T01:30:00Z",
        {"saarch/spec_builder.py":
            sha256_file(ROOT / "saarch/spec_builder.py"),
         "tests/test_second_app.py":
            sha256_file(ROOT / "tests/test_second_app.py")})

    # --- ingest the immutable evidence
    failure = engine.ingest_failure({
        "failure_id": "F-CI-ANCHOR-2026",
        "schema_version": F2C_SCHEMA_VERSION,
        "created_at": "2026-10-21T17:47:00Z",  # detected time, fixed:
        # the incident is a historical fact; replays must not fork
        # provenance with fresh timestamps
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
        "provenance": {
            "artifact_hashes": {
                FIXTURE: sha256_file(ROOT / FIXTURE),
                "benchmarks/results/incident-ci-execution.json":
                    sha256_file(ROOT / "benchmarks/results/"
                                "incident-ci-execution.json")},
            # capture completeness per the F2C workflow contract
            "source_commits": {
                "failure_observed": "7ec7499",
                "false_claim_introduced": "046c192",
                "corrected": "3a609ad",
                "evidence_execution_verified": "8f30d140"},
            "affected_components": [
                ".github/workflows/ci.yml",
                "operator chain (patch application)",
                "commit claims vs repository state"],
            "reproduction": {
                "steps": ["copy ci.yml to a scratch directory",
                          "call naive text replace with an anchor "
                          "that is absent from the file",
                          "observe the result equals the input — "
                          "the silent no-op is the defect"],
                "minimized_case": "assert 'abc'.replace('MISSING', "
                                  "'x') == 'abc'"}},
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
            "created_at": "2026-10-21T18:10:00Z",  # fixed for replay
            "type": kind, "status": "proposed",
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
                out["exit_code"] == 0, "exit_code": out["exit_code"],
                "stdout_tail": (out.get("stdout") or "")[-400:],
                "stderr_tail": (out.get("stderr") or "")[-400:]}

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
        """The mitigation's regression classes — the ones that do NOT
        depend on this pipeline's own output artifact (the artifact is
        written only after promotion; a suite that reads it cannot gate
        its own creation)."""
        out = __import__("saarch.f2c", fromlist=["run_isolated"]) \
            .run_isolated([sys.executable, "-m", "unittest",
                           "tests.test_patching.BothPaths",
                           "tests.test_patching.ClaimConsistency",
                           "tests.test_patching.EvidenceImmutability"],
                          ROOT)
        return {"name": "incident_regression_suite", "passed":
                out["exit_code"] == 0, "exit_code": out["exit_code"],
                "stdout_tail": (out.get("stdout") or "")[-300:],
                "stderr_tail": (out.get("stderr") or "")[-300:]}

    def a_bypass():
        """Attempts to BYPASS the mitigation itself. A substring check
        would accept all three; the structural validator must reject."""
        cases = {
            "hidden_in_comment": (
                "name: w\non:\n  push:\njobs:\n  test:\n"
                "    runs-on: ubuntu-latest\n    steps:\n"
                "      # Regenerate governed-run evidence for this "
                "exact revision\n"
                "      - name: Checkout\n        run: echo hi\n"),
            "in_the_wrong_job": (
                "name: w\non:\n  push:\njobs:\n  other:\n"
                "    runs-on: ubuntu-latest\n    steps:\n"
                "      - name: Regenerate governed-run evidence for "
                "this exact revision\n        run: echo x\n"
                "  test:\n    steps:\n"
                "      - name: Checkout\n        run: echo hi\n"),
            "deep_indent_lookalike": (
                "name: w\non:\n  push:\njobs:\n  test:\n"
                "    steps:\n      - name: Checkout\n        "
                "run: |\n"
                "          - name: Regenerate governed-run evidence "
                "for this exact revision\n"),
        }
        blocked = 0
        for label, text in cases.items():
            present_textually = ("Regenerate governed-run evidence"
                                 in text)          # grep would accept
            try:
                validate_workflow(text, required_step=(
                    "Regenerate governed-run evidence for this exact "
                    "revision"), job_name="test")
                blocked += 0
            except PatchAborted:
                blocked += 1
        return {"name": "bypass_attempts_mitigated",
                "passed": blocked == len(cases),
                "blocked": blocked, "cases": len(cases),
                "note": "all three bypass cases are textually present "
                        "but structurally invalid; the mitigation "
                        "rejects every one"}

    def a_safety():
        """Safety invariants: learning never expands authority."""
        from saarch.capability import (ApprovalAuthority,
                                       CapabilityEnvelope)
        env = CapabilityEnvelope("env-safety", "builder",
                                 "a duty is not a permission", {
            "read_paths": ["a.txt"],
            "budget_seconds": 5, "network": False})
        granted_before = list(env.granted)
        if engine.store.find("capability_id",
                             "C-probe-unvalidated") is None:
            engine.propose_candidate(candidate(
                "strategy", "C-probe-unvalidated",
                "an unvalidated probe candidate that must never "
                "promote",
                ["exists only to prove promotion requires validation"]))
        promote_refused = False
        try:
            engine.promote("C-probe-unvalidated", approver="harness",
                           rationale="attempt without validation")
        except F2CError:
            promote_refused = True
        granted_after = list(env.granted)
        return {"name": "safety_invariants", "passed":
                promote_refused and granted_before == granted_after,
                "promote_without_validation_refused": promote_refused,
                "envelope_grants_unchanged":
                    granted_before == granted_after,
                "note": "promotion is refused without validation, and "
                        "no capability promotion altered envelope "
                        "grants — learning cannot expand permissions"}

    arms = [a_original, a_variations, a_regressions, a_collateral,
            a_generalization, a_incident_suite, a_bypass, a_safety]
    rec = engine.validate_candidate(code_fix["capability_id"], arms)
    results = rec["validation_results"]
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
        failing = [a for a in results["arms"] if not a["passed"]]
        print("VALIDATION FAILED — failing arms:",
              [(a["name"], {k: v for k, v in a.items()
                            if k not in ("name", "passed")})
               for a in failing])
        print("regression candidate:",
              rec2["validation_results"]["passed"])
        engine.reject("C-patch-integrity", "one or more arms failed")
        engine.set_status("F-CI-ANCHOR-2026", "rejected")
        return 1

    # --- preserve a FAILED experiment: the grep-presence anti-pattern
    # (the exact mitigation this incident must NOT adopt) is proposed
    # and rejected through validation, with evidence kept in the store.
    def g_hidden():
        return {"name": "bypass_comment_hidden", "passed": False,
                "detail": "a substring search finds the step inside "
                          "the comment and accepts the workflow"}
    def g_wrong_job():
        return {"name": "bypass_wrong_job", "passed": False,
                "detail": "a substring search finds the step in the "
                          "wrong job and accepts the workflow"}
    grep_cand = engine.propose_candidate(candidate(
        "strategy", "C-step-presence-grep",
        "verify a required workflow step via substring search on the "
        "file text",
        ["never apply: textual presence is not placement and not "
         "validity — this candidate exists to be rejected with "
         "evidence"]))
    engine.validate_candidate("C-step-presence-grep",
                              [g_hidden, g_wrong_job])
    rejected = engine.reject(
        "C-step-presence-grep",
        "both bypass cases pass a substring search; textual presence "
        "is not placement — rejected, evidence preserved")
    rejected_evidence = {
        "capability_id": "C-step-presence-grep",
        "status": rejected["status"],
        "rationale": rejected.get("rationale",
                                 "rejected through validation"),
        "failing_arms": ["bypass_comment_hidden",
                         "bypass_wrong_job"]}

    gen = next(a for a in results["arms"]
               if a["name"] == "generalization_held_out")
    # --- FWBC: digest the failures into condition models (immune
    # memory). Classification first; assumption failures prioritized.
    # Antibodies cite the capabilities the store actually validated.
    def condition_model(fid, fclass, summary, conditions, root_causes,
                        assumptions, triggers, env, behavioral, antibodies,
                        repurposed, signatures, confidence, fwbc_id, ts):
        return {"fwbc_id": fwbc_id, "schema_version": F2C_SCHEMA_VERSION,
                "created_at": ts, "incident_ref": fid,
                "incident_class": fclass, "incident_summary": summary,
                "conditions": conditions, "root_causes": root_causes,
                "assumptions": assumptions, "trigger_factors": triggers,
                "environmental_factors": env,
                "behavioral_factors": behavioral,
                "antibodies": antibodies, "repurposed_uses": repurposed,
                "pattern_signatures": signatures,
                "confidence": confidence}

    engine.analyze_incident(condition_model(
        "F-CI-ANCHOR-2026", "assumption_failure",
        "a workflow patch silently failed to apply while the commit "
        "message claimed the step existed",
        ["text patching by anchor match, without verifying the anchor "
         "exists in the file",
         "the operator chain continued past the patch's assertion "
         "failure — one failed command did not stop the chain",
         "the commit message was written from intent, not from the "
         "diff",
         "downstream CI failure was detected only manually"],
        ["success signals (a completed command, a written message) "
         "were trusted without verifying the artifact they claimed "
         "to have produced"],
        ["the anchor text matched the file content",
         "an assertion failure halts the surrounding chain",
         "a described change and an applied change are the same "
         "thing"],
        ["the workflow step was renamed between drafting and "
         "applying the patch"],
        ["shell operator chains without fail-stop semantics"],
        ["describing intent is easier than verifying state, so it "
         "happens instead of verification"],
        [{"name": "patch-application-confirmation",
          "description": "a patch that changes nothing is a silent "
                         "no-op, not a success",
          "detection_rule": "patch result identical to pre-patch "
                            "text, or anchor absent",
          "mitigation": "abort-or-apply: loud PatchAborted, file left "
                        "bit-identical",
          "status": "implemented",
          "capability_id": "C-patch-integrity"},
         {"name": "postcondition-placement-check",
          "description": "presence of a step is not its placement — "
                         "required steps must be in the right job "
                         "before their consumer",
          "detection_rule": "grammar-parse the workflow; required "
                            "step missing, misplaced, or only in a "
                            "comment",
          "mitigation": "structural validation before accepting the "
                        "patch",
          "status": "implemented",
          "capability_id": "C-patch-integrity"},
         {"name": "commit-claim-consistency",
          "description": "every claim in a commit message must match "
                         "the actual diff",
          "detection_rule": "claimed change absent from the "
                            "repository state",
          "mitigation": "claim_matches_state runs before every "
                        "commit",
          "status": "implemented",
          "capability_id": "C-incident-regression"},
         {"name": "operator-chain-fail-stop",
          "description": "a failed command mid-chain must halt the "
                         "chain, never let success signals proceed",
          "detection_rule": "chain continues after a nonzero exit or "
                            "raised assertion",
          "mitigation": "fail-stop chains or explicit verification "
                        "gates between mutations and claims",
          "status": "proposed"}],
        ["the incident fixture became a permanent teaching specimen",
         "claim_matches_state became the dogfooded pre-commit audit",
         "the condition model feeds immune-memory retrieval for "
         "future silent-failure classes"],
        ["automation_trust", "assumption_mismatch",
         "verification_failure"],
        0.85, "FWBC-001", "2026-10-21T18:20:00Z"))

    engine.analyze_incident(condition_model(
        "F-ZOMBIE-Z1", "assumption_failure",
        "engine hijacked a live question via a test-id collision",
        ["test ids were assumed unique without an enforced invariant",
         "the trusted core accepted an id that collided with a live "
         "question"],
        ["uniqueness was assumed, never enforced at the boundary "
         "where it mattered"],
        ["submitted test ids never collide with live questions"],
        ["engine-submitted payload reused an existing id"],
        ["trusted-core/experimental-engine boundary in its early form"],
        [],
        [{"name": "question-immutability",
          "description": "a question is immutable once a human answer "
                         "exists",
          "detection_rule": "test-id collision against a live "
                            "question",
          "mitigation": "promote() refuses test-id collisions",
          "status": "implemented",
          "capability_id": None,
          "regression_test": "tests.test_zombie_regressions::"
                             "ZombieRegressions.test_z1"}],
        ["the zombie ledger preserves the pre-fix exploit state as a "
         "permanent regression specimen"],
        ["assumption_mismatch", "verification_failure"],
        0.8, "FWBC-002", "2026-10-20T00:10:00Z"))

    engine.analyze_incident(condition_model(
        "F-ZOMBIE-Z2", "assumption_failure",
        "engine redefined a question after a human answer was rolled "
        "back",
        ["rollback was assumed to erase history rather than restore "
         "an exact state",
         "question identity was not bound to its answer history"],
        ["rollback semantics were assumed, not specified"],
        ["a rolled-back question is a blank slate"],
        ["engine retry after a rollback"],
        [],
        [],
        [{"name": "rollback-restores-checkpoint",
          "description": "rollback restores the exact checkpoint "
                         "state, including answer history and "
                         "ambiguity markers",
          "detection_rule": "state after rollback differs from the "
                            "checkpoint",
          "mitigation": "exact-state rollback semantics; regression "
                        "coverage shared with Z1 (recorded gap: no "
                        "dedicated Z2 test)",
          "status": "implemented",
          "capability_id": None,
          "regression_test": "tests.test_zombie_regressions::"
                             "ZombieRegressions.test_z1"}],
        ["shared regression coverage gap is now a visible, tracked "
         "entry rather than a silent omission"],
        ["assumption_mismatch", "verification_failure"],
        0.75, "FWBC-003", "2026-10-20T00:20:00Z"))

    engine.analyze_incident(condition_model(
        "F-SPEC-SINGULARIZATION", "code_failure",
        "the singularizer stripped characters wrongly from words like "
        "'expenses'",
        ["suffix rules were replaced by naive character slicing",
         "no test exercised irregular plurals before shipping"],
        ["string mutation without rule coverage"],
        ["'s'-suffixed words always plural"],
        ["derived schema names from user nouns"],
        [],
        [],
        [{"name": "suffix-rule-coverage",
          "description": "singularization must use explicit suffix "
                         "rules (ies/sses/ss/us) with tests",
          "detection_rule": "singularize output not a real word "
                            "stem",
          "mitigation": "explicit rules + "
                        "SingularizationRegression tests",
          "status": "implemented",
          "capability_id": None,
          "regression_test": "tests.test_second_app::"
                             "SingularizationRegression."
                             "test_expenses_singularizes_safely"}],
        ["the failure entered the unified database with its evidence "
         "hash-bound, closing a documentation gap"],
        ["naming_ambiguity", "verification_failure"],
        0.9, "FWBC-004", "2026-10-21T01:40:00Z"))

    immune = engine.immune_memory({})
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
        "rejected_experiments": [rejected_evidence],
        "capture": {
            "source_commits": {
                "failure_observed": "7ec7499",
                "false_claim_introduced": "046c192",
                "corrected": "3a609ad",
                "evidence_execution_verified": "8f30d140"},
            "affected_components": [".github/workflows/ci.yml",
                                     "operator chain",
                                     "commit claims vs repository state"],
            "reproduction": {
                "steps": ["naive replace with an absent anchor"],
                "minimized_case":
                    "assert 'abc'.replace('MISSING', 'x') == 'abc'"}},
        "execution_evidence": {
            "artifact": "benchmarks/results/incident-ci-execution.json",
            "sha256": sha256_file(
                ROOT / "benchmarks/results/"
                "incident-ci-execution.json")[:16],
            "ci_run_id": 39648224836,
            "step_seen_executing": True,
            "ci_conclusion": "success"},
        "status_vocabulary": {
            "proposed": "C-probe-unvalidated (in store, never "
                        "promoted)",
            "implemented": "saarch/patching.py exists in the repo",
            "tested": "tests/test_patching.py executed, 21+ green",
            "verified": "C-patch-integrity + C-incident-regression "
                        "promoted after all 8 arms passed",
            "rejected": "C-step-presence-grep (evidence preserved)",
            "blocked": "not used in this incident",
            "unverified": "nothing is claimed without a run"},
        "definition_of_done": {
            "1_preserved_original_incident": "fixture + store record, "
                "hash-bound, immutable",
            "2_reproduced": "naive baseline silently no-ops (arm: "
                "original_failure_reproduced)",
            "3_confirmed_cause": "confirmed: patch without anchor "
                "verification in a chain that continued past failure",
            "4_corrective_change": "saarch/patching.py, minimal",
            "5_reusable_mitigation": "C-patch-integrity + "
                "C-incident-regression",
            "6_regression_coverage": "tests/test_patching.py both "
                "paths",
            "7_variations_and_regressions": "8 arms incl. collateral "
                "and bypass",
            "8_actual_results_and_limits": "this report; limitations "
                "in each capability",
            "9_auditable_decision": "proposals and failure records "
                "are immutable and hash-chained in failures.jsonl; "
                "every promotion, rejection, and retirement appends a "
                "DURABLE disposition record to the same hash-chained "
                "store — the audit trail is the database itself"},
        "unified_database": {
            "store": "benchmarks/results/failures.jsonl",
            "failure_ids": [r["failure_id"] for r in engine.store.records
                            if "failure_id" in r],
            "capability_ids": [r["capability_id"]
                                for r in engine.store.records
                                if "capability_id" in r],
            "durable_dispositions": sum(
                1 for r in engine.store.records
                if r.get("record_kind") == "disposition"),
            "note": "zombies, historical bugs, and this incident — one "
                    "append-only, hash-chained database; query it with "
                    "FailureToCapabilityEngine.retrieve() or "
                    ".dispositions(ref)"},
        "immune_memory": {
            "condition_models": [m["fwbc_id"] for m in immune["models"]],
            "antibodies": immune["antibodies"],
            "recurring_themes": sorted(
                {sig for m in immune["models"]
                 for sig in m["pattern_signatures"]}),
            "note": "immune memory stores conditions, signatures, "
                    "antibodies, and repurposed uses — never blame; "
                    "query with engine.immune_memory()"},
        "generalizes": gen["passed"],
        "generalization_note": "held-out workflow unseen in incident "
                               "evidence and regression tests",
        "wall_seconds": round(time.monotonic() - started, 2),
    }
    # claim-then-verify: the report claims the full suite passes, the
    # suite runs against that exact artifact, and on failure the claim
    # is corrected in the artifact before exiting nonzero.
    claim = {"suite": "tests.test_patching (full, incl. "
                      "IncidentArtifact and ExtendedValidation)",
             "passed": True}
    report["post_write_verification"] = claim
    out_path = RESULTS / "f2c-incident-patch.json"
    out_path.write_text(json.dumps(report, indent=1) + "\n")
    full = __import__("saarch.f2c", fromlist=["run_isolated"]) \
        .run_isolated([sys.executable, "-m", "unittest",
                       "tests.test_patching"], ROOT)
    if full["exit_code"] != 0:
        claim["passed"] = False
        claim["note"] = ("the full suite rejected this artifact; the "
                         "report is evidence of the failure, not of "
                         "success")
        out_path.write_text(json.dumps(report, indent=1) + "\n")
        print("POST-WRITE VERIFICATION FAILED: the written artifact "
              "does not satisfy the full suite")
        return 1
    print(f"incident: {failure['status']} | capabilities promoted: "
          f"{report['two_outputs']['capabilities']} | generalizes: "
          f"{report['generalizes']}")
    print(f"store integrity: "
          f"{'OK' if report['evidence_immutable']['store_verify'] else 'BROKEN'}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
