"""Benchmark and smoke-demo tests: reproducibility and the honest claims."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from benchmarks.uncertainty_loss import CORPUS, run  # noqa: E402


class BenchmarkTests(unittest.TestCase):
    def test_reproducible(self):
        self.assertEqual(run(), run(),
                         "same corpus + same policies must yield identical "
                         "results on every run")

    def test_claims_layer_retains_uncertainty_naive_loses_it(self):
        r = run()
        naive = r["naive"]["stage_uncertainty_retention"]
        claims = r["claims"]["stage_uncertainty_retention"]
        # naive flattens ambiguity by the end of the specify stage
        self.assertEqual(naive["specify"]["ambiguous_all"], 0.0)
        # the claims layer still flags it
        self.assertEqual(claims["certify"]["ambiguous_all"], 0.5)

    def test_false_certifications_naive_vs_claims(self):
        r = run()
        n_unresolved = sum(1 for c in CORPUS if c["pattern"] and not c["answer"])
        self.assertEqual(r["naive"]["false_certifications"], n_unresolved)
        self.assertEqual(r["claims"]["false_certifications"], 0)

    def test_resolving_questions_surfaced(self):
        r = run()
        n_ambiguous = sum(1 for c in CORPUS if c["pattern"])
        self.assertEqual(r["naive"]["resolving_questions_surfaced"], 0)
        self.assertEqual(r["claims"]["resolving_questions_surfaced"], n_ambiguous)

    def test_controls_certify_without_questions(self):
        r = run()
        n_control = sum(1 for c in CORPUS if not c["pattern"])
        self.assertEqual(r["claims"]["control_certified_without_questions"],
                         n_control)

    def test_results_json_written_and_matches(self):
        out = ROOT / "benchmarks" / "results" / "uncertainty-loss.json"
        self.assertTrue(out.is_file())
        self.assertEqual(json.loads(out.read_text()), run())


class SmokeDemoTests(unittest.TestCase):
    """The demos must actually run and show what the docs claim they show."""

    def run_demo(self, name, must_contain):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "smoke" / name)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        combined = proc.stdout
        for phrase in must_contain:
            self.assertIn(phrase, combined,
                          f"{name} must physically show: {phrase!r}")

    def test_demo_ambiguity(self):
        self.run_demo("demo_ambiguity.py", [
            "GATE: REFUSED",
            "THE RESOLVING QUESTION",
            "GATE: NOW CERTIFIED",
            "GATE: CERTIFIED",
        ])

    def test_demo_calibration(self):
        self.run_demo("demo_calibration.py", [
            "unknown-missing",
            "unknown-conflicted",
            "reconcile conflicting evidence",
            "band=certain",
        ])

    def test_demo_benchmark(self):
        self.run_demo("demo_benchmark.py", [
            "UNCERTAINTY-LOSS BENCHMARK",
            "FALSE certifications",
        ])

    def test_run_all(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "smoke" / "run_all.py")],
            capture_output=True, text=True, timeout=240)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertIn("All smoke demos completed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
