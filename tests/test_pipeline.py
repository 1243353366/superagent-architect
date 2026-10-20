"""Full vertical-slice pipeline test: five inputs -> verified project -> export.

The verification step runs REAL subprocesses (compileall + the generated
project's own unittest suite), so this test proves the end-to-end claim.
"""
import tempfile
import unittest
from pathlib import Path

from saarch.audit import AuditLog
from saarch.delivery import export_zip
from saarch.generator import generate, plan_tasks
from saarch.models import (
    DRAFT, READY, Project, ProjectIntake)
from saarch.spec_builder import build_spec
from saarch.verifier import verify


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="saarch-test-"))
        cls.project = Project(
            state=DRAFT,
            slug="personal-task-tracker",
            intake=ProjectIntake(
                instructions="A personal task tracker for daily planning.",
                scope="Single user, local data. exclude: team collaboration, sharing",
                capabilities=["create tasks with a title and notes",
                             "mark tasks as done",
                             "delete tasks"],
                guardrails="No external network calls. Data stays on the local machine.",
                language="python"))
        cls.project.intake.validate()
        cls.audit = AuditLog(cls.workdir)

    def test_01_spec_derivation(self):
        self.project.transition("SPECIFIED")
        self.project.spec = build_spec(self.project, audit=self.audit)
        spec = self.project.spec
        self.assertEqual(spec.entities[0].name, "task")
        self.assertTrue(any(e.path == "/api/tasks" for e in spec.endpoints))
        self.assertEqual(spec.exclusions, ["team collaboration, sharing"])
        # mandatory guardrails are always present and additive
        joined = " ".join(spec.security_requirements)
        self.assertIn("environment variables", joined)
        self.assertIn("Data stays on the local machine.", joined)
        # assumptions are recorded, not silent
        self.assertGreaterEqual(len(spec.assumptions), 3)

    def test_02_plan_dependency_order(self):
        self.project.transition("PLANNED")
        self.project.plan = plan_tasks()
        ids = {t.id for t in self.project.plan}
        self.assertIn("storage", ids)
        # storage depends on models; tests depend on server
        by_id = {t.id: t for t in self.project.plan}
        self.assertIn("models", by_id["storage"].depends_on)
        self.assertIn("server", by_id["tests"].depends_on)

    def test_03_generation_and_verification(self):
        self.project.transition("GENERATING")
        out = generate(self.project, self.workdir, self.audit)
        expected = ["run.py", "README.md", ".env.example", ".gitignore",
                    "Makefile", "requirements.txt",
                    "app/main.py", "app/storage.py", "app/api.py",
                    "app/resources.py", "app/validation.py", "app/ui.py",
                    "tests/test_app.py"]
        for rel in expected:
            self.assertTrue((out / rel).is_file(), f"missing {rel}")
        self.project.transition("VERIFYING")
        self.project.verification = verify(self.project, self.workdir, self.audit)
        for c in self.project.verification.checks:
            self.assertTrue(c.executed, f"{c.name} did not run")
        self.assertTrue(self.project.verification.passed,
                        msg="\n".join(f"{c.name}: {c.detail}"
                                      for c in self.project.verification.checks
                                      if not c.passed))
        self.project.transition(READY)

    def test_04_zip_export(self):
        archive = export_zip(self.workdir / "generated" / self.project.slug,
                             self.workdir / "export")
        self.assertTrue(archive.exists())
        self.assertGreater(archive.stat().st_size, 1000)

    def test_05_audit_trail_records_every_stage(self):
        actions = [e["action"] for e in self.audit.entries()]
        for expected in ("spec_built", "generated", "verified"):
            self.assertIn(expected, actions)


if __name__ == "__main__":
    unittest.main()
