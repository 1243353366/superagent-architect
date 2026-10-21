"""C-gate-as-precondition: the release gate fails commits that were
not produced through the claim check. This is the regression test
that retires F-GATE-CHAIN — the failure that shipped despite a
failed gate."""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)

from scripts.release_gate import gate_ok    # noqa: E402


class ReleaseGate(unittest.TestCase):

    def test_markerless_commit_blocked(self):
        ok, why = gate_ok("add feature, bump version")
        self.assertFalse(ok)
        self.assertIn("F-GATE-CHAIN", why,
                      "the refusal must name the incident it comes "
                      "from — lessons are traceable")

    def test_gated_commit_allowed(self):
        ok, why = gate_ok(
            "fix bug\n\nclaims verified against the repository "
            "before this commit (claim_matches_state)")
        self.assertTrue(ok)

    def test_gate_fails_closed_on_empty_message(self):
        ok, _ = gate_ok("")
        self.assertFalse(ok)
