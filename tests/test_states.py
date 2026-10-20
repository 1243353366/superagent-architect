"""State machine and persistence tests."""
import json
import tempfile
import unittest
from pathlib import Path

from saarch.models import (
    BLOCKED, DRAFT, GENERATING, PLANNED, READY, SPECIFIED, VERIFYING,
    Project, ProjectIntake, ValidationError)


def sample_intake():
    return ProjectIntake(
        instructions="A small task tracker for personal use.",
        scope="Single user. exclude: multi-tenant features",
        capabilities=["users can create tasks", "users can mark tasks done"],
        guardrails="No external network calls. Data stays local.",
        language="python")


class StateTests(unittest.TestCase):
    def test_happy_path_transitions(self):
        p = Project(state=DRAFT, intake=sample_intake())
        for a, b in ((DRAFT, SPECIFIED), (SPECIFIED, PLANNED),
                     (PLANNED, GENERATING), (GENERATING, VERIFYING),
                     (VERIFYING, READY)):
            p.transition(b)
        self.assertEqual(p.state, READY)
        self.assertEqual(p.blocked_reason, "")

    def test_illegal_transition_rejected(self):
        p = Project(state=DRAFT, intake=sample_intake())
        with self.assertRaises(ValidationError):
            p.transition(READY)
        with self.assertRaises(ValidationError):
            p.transition(VERIFYING)

    def test_blocked_records_reason(self):
        p = Project(state=DRAFT, intake=sample_intake())
        p.transition(BLOCKED, "intake invalid: language 'rust' not supported")
        self.assertIn("rust", p.blocked_reason)

    def test_invalid_language_rejected_at_intake(self):
        bad = sample_intake()
        bad.language = "rust"
        with self.assertRaises(ValidationError):
            bad.validate()

    def test_missing_input_rejected(self):
        bad = sample_intake()
        bad.scope = "  "
        with self.assertRaises(ValidationError):
            bad.validate()

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = Project(state=DRAFT, intake=sample_intake(), slug="task-tracker")
            p.transition(SPECIFIED)
            p.save(d)
            loaded = Project.load(d)
            self.assertEqual(loaded.state, SPECIFIED)
            self.assertEqual(loaded.slug, "task-tracker")
            self.assertEqual(loaded.intake.capabilities,
                             ["users can create tasks", "users can mark tasks done"])


if __name__ == "__main__":
    unittest.main()
