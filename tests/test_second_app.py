"""Generality test: a second, materially different application.

The first proof app is a single-resource task tracker. This test runs the
full vertical slice on a materially different intake — a personal expense
ledger with TWO derived resources (expense, budget), a different domain, and
different capabilities — and verifies it for real: the generated project's
own test suite executes in an isolated sandbox.
"""
import tempfile
import unittest
from pathlib import Path

from saarch.audit import AuditLog
from saarch.generator import generate
from saarch.models import (
    DRAFT, PLANNED, READY, SPECIFIED, VERIFYING, Project, ProjectIntake)
from saarch.planner import ScriptedPlanningProvider, plan_project
from saarch.spec_builder import build_spec
from saarch.verifier import verify


SECOND_APP_INPUTS = dict(
    instructions="A personal expense ledger for tracking monthly spending.",
    scope=("Single user, local data only. "
           "exclude: bank feeds, online payments, multi-currency"),
    capabilities=["track expenses with an amount and a category",
                  "track budget goals with a monthly limit",
                  "mark expenses as reimbursable"],
    guardrails=("No external network calls. Data stays on the local machine. "
                "No analytics or telemetry."),
    language="python",
)


class SecondAppPipelineTests(unittest.TestCase):
    """Full pipeline on the second app: two resources, real verification."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="saarch-second-"))
        cls.audit = AuditLog(cls.workdir)
        cls.project = Project(
            state=DRAFT, slug="personal-expense-ledger",
            intake=ProjectIntake(**SECOND_APP_INPUTS))
        cls.project.intake.validate()

    def test_01_spec_derives_two_resources(self):
        self.project.transition(SPECIFIED)
        self.project.spec = build_spec(self.project, audit=self.audit)
        names = [e.name for e in self.project.spec.entities]
        self.assertIn("expense", names)
        self.assertIn("budget", names)
        self.assertEqual(len(names), 2)
        # materially different API surface: two full CRUD route sets + health
        self.assertEqual(len(self.project.spec.endpoints), 11)
        paths = {e.path for e in self.project.spec.endpoints}
        self.assertIn("/api/expenses", paths)
        self.assertIn("/api/budgets", paths)

    def test_02_plan_with_llm_refinement_path(self):
        self.project.transition(PLANNED)
        # scripted provider proves the LLM path is real code: a valid
        # refinement is adopted and labeled, anything else falls back
        provider = ScriptedPlanningProvider({
            "title_overrides": [{"id": "docs",
                                 "title": "Ledger README with two-resource examples"}],
            "add_tasks": [{"id": "seed_ledger",
                           "title": "Seed opening ledger balance",
                           "depends_on": ["storage"]}],
            "notes": "added ledger seeding for the two-resource model"})
        tasks, path, notes = plan_project(self.project, provider=provider,
                                          audit=self.audit)
        self.assertTrue(path.startswith("llm:"))
        self.project.plan = tasks
        self.project.plan_path = path
        self.assertIn("seed_ledger", [t.id for t in tasks])

    def test_03_generate_and_verify_for_real(self):
        self.project.transition("GENERATING")
        out = generate(self.project, self.workdir, self.audit)
        # two tables in the generated schema: material difference from demo 1
        storage = (out / "app" / "storage.py").read_text(encoding="utf-8")
        self.assertIn("expense", storage)
        self.assertIn("budget", storage)
        self.project.transition(VERIFYING)
        self.project.verification = verify(self.project, self.workdir, self.audit)
        for c in self.project.verification.checks:
            self.assertTrue(c.executed, f"{c.name} did not run")
        self.assertTrue(self.project.verification.passed,
                        msg="\n".join(f"{c.name}: {c.detail}"
                                      for c in self.project.verification.checks
                                      if not c.passed))
        self.project.transition(READY)
        # verification ran in isolation: no sandbox artifacts in the real tree
        self.assertFalse(list(self.workdir.rglob("*.sandbox*")))


if __name__ == "__main__":
    unittest.main()
