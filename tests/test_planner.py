"""Planning tests: deterministic floor, LLM refinement, honest fallback.

No network is used: the LLM path is exercised through the scripted provider,
and the HTTP provider's fallback behavior is tested with unreachable
endpoints.
"""
import os
import tempfile
import unittest
from pathlib import Path

from saarch.audit import AuditLog
from saarch.generator import plan_tasks
from saarch.models import DRAFT, PLANNED, Project, ProjectIntake
from saarch.planner import (
    HTTPLLMProvider, PlanningUnavailable, ScriptedPlanningProvider,
    get_planning_provider, plan_project, validate_plan,
)
from saarch.spec_builder import build_spec


def _project():
    p = Project(state=DRAFT, slug="test-app", intake=ProjectIntake(
        instructions="A personal expense ledger for tracking spending.",
        scope="Single user, local data only. exclude: bank integration",
        capabilities=["track expenses with amount and category",
                      "record budget goals with a monthly limit"],
        guardrails="No external network calls. Data stays on the local machine.",
        language="python"))
    p.intake.validate()
    p.transition("SPECIFIED")
    p.spec = build_spec(p)
    p.transition(PLANNED)
    return p


class ValidatePlanTests(unittest.TestCase):
    def setUp(self):
        self.baseline = plan_tasks()

    def test_baseline_is_valid(self):
        tasks, errors = validate_plan(
            [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
             for t in self.baseline], self.baseline)
        self.assertEqual(errors, [])
        self.assertEqual({t.id for t in tasks}, {t.id for t in self.baseline})

    def test_missing_baseline_task_rejected(self):
        cand = [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
                for t in self.baseline if t.id != "tests"]
        tasks, errors = validate_plan(cand, self.baseline)
        self.assertIn("missing baseline tasks: tests", errors)

    def test_cycle_rejected(self):
        cand = [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
                for t in self.baseline]
        cand.append({"id": "extra", "title": "Extra", "depends_on": ["extra2"]})
        cand.append({"id": "extra2", "title": "Extra 2", "depends_on": ["extra"]})
        tasks, errors = validate_plan(cand, self.baseline)
        self.assertTrue(any("cycle" in e or "unknown" in e for e in errors))

    def test_unknown_dependency_rejected(self):
        cand = [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
                for t in self.baseline]
        cand.append({"id": "extra", "title": "Extra", "depends_on": ["nope"]})
        tasks, errors = validate_plan(cand, self.baseline)
        self.assertIn("task extra depends on unknown task nope", errors)

    def test_topological_order(self):
        # reversed order with valid deps must still come out dependency-ordered
        cand = [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
                for t in self.baseline]
        ordered, errors = validate_plan(list(reversed(cand)), self.baseline)
        self.assertEqual(errors, [])
        pos = {t.id: i for i, t in enumerate(ordered)}
        for t in ordered:
            for d in t.depends_on:
                self.assertLess(pos[d], pos[t.id],
                                f"{d} must precede {t.id}")


class PlanProjectTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="saarch-plan-"))
        self.audit = AuditLog(self.workdir)
        self.project = _project()

    def _entries(self, action):
        return [e for e in self.audit.entries() if e["action"] == action]

    def test_deterministic_without_provider(self):
        tasks, path, notes = plan_project(self.project, provider=None,
                                          audit=self.audit)
        self.assertEqual(path, "deterministic")
        self.assertEqual(tasks, plan_tasks())
        self.assertTrue(self._entries("planned"))

    def test_valid_llm_refinement_is_used(self):
        refined = [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
                   for t in plan_tasks()]
        refined.append({"id": "seed_data", "title": "Seed starting ledger rows",
                        "depends_on": ["storage"]})
        provider = ScriptedPlanningProvider(
            {"tasks": refined, "notes": "added a data-seeding task"})
        tasks, path, notes = plan_project(self.project, provider=provider,
                                          audit=self.audit)
        self.assertTrue(path.startswith("llm:"))
        self.assertIn("seed_data", [t.id for t in tasks])
        self.assertIn("data-seeding", notes)

    def test_invalid_llm_plan_falls_back_honestly(self):
        # drops a baseline task -> must fall back to deterministic
        broken = [{"id": t.id, "title": t.title, "depends_on": t.depends_on}
                  for t in plan_tasks() if t.id != "docs"]
        provider = ScriptedPlanningProvider({"tasks": broken, "notes": "bad"})
        tasks, path, notes = plan_project(self.project, provider=provider,
                                          audit=self.audit)
        self.assertEqual(path, "deterministic")
        self.assertEqual(tasks, plan_tasks())
        fallback = self._entries("planning_fallback")
        self.assertTrue(fallback and "missing baseline tasks" in
                        str(fallback[0].get("errors")))

    def test_unavailable_provider_falls_back_honestly(self):
        provider = HTTPLLMProvider(url="http://127.0.0.1:1/v1/chat/completions",
                                   model="x", timeout=1)
        tasks, path, notes = plan_project(self.project, provider=provider,
                                          audit=self.audit)
        self.assertEqual(path, "deterministic")
        fallback = self._entries("planning_fallback")
        self.assertTrue(fallback)

    def test_config_env_var_forces_deterministic(self):
        os.environ["SAARCH_PLANNER"] = "deterministic"
        try:
            self.assertIsNone(get_planning_provider())
        finally:
            del os.environ["SAARCH_PLANNER"]


class HTTPLLMProviderTests(unittest.TestCase):
    def test_not_configured_without_env(self):
        url, model = os.environ.get("SAARCH_LLM_URL"), os.environ.get("SAARCH_MODEL")
        try:
            os.environ.pop("SAARCH_LLM_URL", None)
            os.environ.pop("SAARCH_MODEL", None)
            self.assertFalse(HTTPLLMProvider.configured())
            with self.assertRaises(PlanningUnavailable):
                HTTPLLMProvider().refine_plan("ctx", plan_tasks())
        finally:
            if url: os.environ["SAARCH_LLM_URL"] = url
            if model: os.environ["SAARCH_MODEL"] = model

if __name__ == "__main__":
    unittest.main()
