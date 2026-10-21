"""The gate bypass lab (Z4-style): every known bypass technique
against the release gate, each required to be caught. Containment
is proven, not assumed — a technique that slips through becomes a
new experiment here, and the coupling is reported honestly."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.release_gate import CLAIMS, gate_ok   # noqa: E402


class GateBypassLab(unittest.TestCase):

    def test_bypass_markerless_commit(self):
        ok, why = gate_ok("add feature, bump version",
                          workflow_text="release_gate.py",
                          claims_path=CLAIMS)
        self.assertFalse(ok)
        self.assertIn("F-GATE-CHAIN", why)

    def test_bypass_marker_hidden_inside_a_word(self):
        ok, _ = gate_ok("xclaim_matches_statex",
                        workflow_text="release_gate.py",
                        claims_path=CLAIMS)
        self.assertFalse(ok)

    def test_bypass_empty_message(self):
        self.assertFalse(gate_ok("",
                                 workflow_text="release_gate.py",
                                 claims_path=CLAIMS)[0])

    def test_bypass_removed_ci_step(self):
        ok, why = gate_ok("fix (claim_matches_state)",
                         workflow_text="jobs:\n  test:\n",
                         claims_path=CLAIMS)
        self.assertFalse(ok)
        self.assertIn("bypass", why)

    def test_bypass_missing_claims_file(self):
        ok, why = gate_ok("fix (claim_matches_state)",
                         workflow_text="release_gate.py",
                         claims_path=ROOT / "does-not-exist.json")
        self.assertFalse(ok)
        self.assertIn("trust", why)

    def test_bypass_forged_marker_with_false_claims(self):
        """The misleading-success class: the marker is pasted in, but
        the claims do not match the repository."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "claims.json"
            bad.write_text(json.dumps([
                {"file": "saarch/f2c.py",
                 "must_contain":
                 ["THIS STRING DOES NOT EXIST 9931"]}]))
            ok, why = gate_ok("fix (claim_matches_state)",
                              workflow_text="release_gate.py",
                              claims_path=bad)
        self.assertFalse(ok)
        self.assertIn("attestation", why)

    def test_honest_gated_commit_allowed(self):
        """Baseline: the real workflow, the real committed claims, an
        honest marker — the gate must allow exactly this."""
        ok, why = gate_ok("fix (claim_matches_state)")
        self.assertTrue(ok, why)
