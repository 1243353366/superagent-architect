"""Behavior 3: adaptive persistence.

The repair loop is a strategy ladder with stagnation detection: it stops
repeating the same failing approach (even with budget remaining), diagnoses
the failure, and writes a proposal for a better specification.
"""
import unittest
from pathlib import Path
from unittest import mock

from saarch.audit import AuditLog
from saarch.generator import generate, plan_tasks
from saarch.models import CheckResult, DRAFT, GENERATING, Project, ProjectIntake
from saarch.spec_builder import build_spec
from saarch.verifier import (
    OUTCOME_PASSED, OUTCOME_STAGNATION_DIAGNOSED,
    _failure_signature, diagnose, repair, verify)


def make_project():
    p = Project(
        state=DRAFT, slug="adaptive-demo", repair_budget=3,
        intake=ProjectIntake(
            instructions="A personal task tracker for daily planning.",
            scope="single user",
            capabilities=["create tasks", "mark tasks done", "delete tasks"],
            guardrails="data local", language="python"))
    p.intake.validate()
    p.spec = build_spec(p)
    p.plan = plan_tasks()
    return p


def failing_report(detail="SyntaxError: injected defect in app/main.py"):
    return mock.Mock(
        passed=False,
        checks=[CheckResult("compileall", passed=False, executed=True,
                           detail=detail)])


class SignatureTests(unittest.TestCase):
    def test_signature_is_stable_across_noise(self):
        r1 = failing_report("File app/main.py, line 12\nSyntaxError: bad token")
        r2 = failing_report("File app/main.py, line 45\nSyntaxError: bad token")
        self.assertEqual(_failure_signature(r1), _failure_signature(r2))

    def test_signature_distinguishes_failures(self):
        self.assertNotEqual(_failure_signature(failing_report()),
                            _failure_signature(failing_report("TimeoutExpired")))

    def test_diagnosis_categories(self):
        self.assertEqual(
            diagnose(failing_report("ModuleNotFoundError: no module 'x'"))["category"],
            "environment")
        self.assertEqual(
            diagnose(failing_report("SyntaxError: bad token"))["category"],
            "generation-defect")
        self.assertEqual(
            diagnose(failing_report("AssertionError: list was empty"))["category"],
            "behavior-mismatch")


class RepairLoopTests(unittest.TestCase):
    def test_repairs_a_real_injected_defect(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            audit = AuditLog(workdir)
            p = make_project()
            p.transition("SPECIFIED")
            p.transition("PLANNED")
            p.transition(GENERATING)
            generate(p, workdir, audit)
            main_py = workdir / "generated" / p.slug / "app" / "main.py"
            main_py.write_text(
                main_py.read_text(encoding="utf-8")
                + "\n\ndef broken(:\n    pass\n", encoding="utf-8")

            report = verify(p, workdir, audit)
            self.assertFalse(report.passed)          # detection worked

            p.transition("VERIFYING")
            report, outcome, proposal = repair(p, workdir, audit)
            self.assertEqual(outcome, OUTCOME_PASSED)
            self.assertTrue(report.passed)            # fix within limits
            self.assertIsNone(proposal)
            self.assertNotIn("def broken(:",
                             main_py.read_text(encoding="utf-8"))

    def test_stops_repeating_and_diagnoses(self):
        """Same failure signature twice -> stop, diagnose, propose —
        even though the repair budget would allow another attempt."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            audit = AuditLog(workdir)
            p = make_project()
            p.transition("SPECIFIED")
            p.transition("PLANNED")
            p.transition(GENERATING)
            generate(p, workdir, audit)
            p.transition("VERIFYING")

            with mock.patch("saarch.verifier.verify",
                            side_effect=lambda *a, **k: failing_report()):
                report, outcome, proposal = repair(p, workdir, audit)

            self.assertEqual(outcome, OUTCOME_STAGNATION_DIAGNOSED)
            self.assertFalse(report.passed)
            self.assertEqual(p.repairs_used, 1, "must stop before budget (3)")
            self.assertIsNotNone(proposal)
            self.assertTrue(Path(proposal).exists())
            text = Path(proposal).read_text(encoding="utf-8")
            self.assertIn("Diagnosis", text)
            self.assertIn("generation-defect", text)
            self.assertIn("Stagnation detected", text)
            strategies = {e["strategy"] for e in p.repair_log}
            self.assertEqual(strategies, {"regenerate-full"})

    def test_budget_exhaustion_also_diagnoses(self):
        """Non-repeating failures run to the budget, then diagnose honestly."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            audit = AuditLog(workdir)
            p = make_project()
            p.repair_budget = 1
            p.transition("SPECIFIED")
            p.transition("PLANNED")
            p.transition(GENERATING)
            generate(p, workdir, audit)
            p.transition("VERIFYING")

            counter = {"n": 0}

            def flaky(*a, **k):
                counter["n"] += 1
                return failing_report(f"different failure #{counter['n']}")

            with mock.patch("saarch.verifier.verify", side_effect=flaky):
                report, outcome, proposal = repair(p, workdir, audit)

            self.assertEqual(outcome, "budget-exhausted")
            self.assertEqual(p.repairs_used, 1)
            self.assertTrue(Path(proposal).exists())


if __name__ == "__main__":
    unittest.main()
