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

    def test_gate_passes_from_a_clean_environment(self):
        """CI invokes the gate as `python scripts/release_gate.py`
        with the script dir on sys.path — not the repo root. The
        gate caught its own environment-dependent import by blocking
        CI run 39660706405; this test pins the fix."""
        import subprocess
        out = subprocess.run(
            [sys.executable,
             str(ROOT / "scripts/release_gate.py")],
            capture_output=True, text=True,
            cwd=str(ROOT),
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
        self.assertEqual(out.returncode, 0,
                         f"gate failed from clean env: {out.stdout} "
                         f"{out.stderr}")
