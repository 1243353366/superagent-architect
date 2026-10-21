"""C-suite-failure-ledger: failing runs leave durable evidence; green
runs leave nothing. The F-SUITE-FLAKE incident stays open, but its
next occurrence will arrive with a captured name and output."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)

from saarch.suite_ledger import run_with_ledger   # noqa: E402


def boom_case():
    """Probe cases are built at call time so test discovery can never
    collect them and fail the real suite."""
    class Boom(unittest.TestCase):
        def test_always_fails(self):
            self.assertEqual(1, 2, "deliberate ledger probe failure")
    return Boom


def green_case():
    class Green(unittest.TestCase):
        def test_green(self):
            self.assertTrue(True)
    return Green


class SuiteLedger(unittest.TestCase):

    def test_failing_run_writes_exactly_one_row_with_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "led.jsonl"
            out = run_with_ledger([boom_case()], ledger)
            self.assertEqual(out["failed"], 1)
            self.assertEqual(out["ledger_rows"], 1)
            row = json.loads(ledger.read_text())
            self.assertTrue(row["failures"][0]["id"]
                            .endswith("test_always_fails"))
            self.assertIn("deliberate ledger probe failure",
                          row["failures"][0]["error"])
            self.assertIn("python", row)

    def test_green_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "led.jsonl"
            out = run_with_ledger([green_case()], ledger)
            self.assertEqual(out["failed"], 0)
            self.assertFalse(ledger.exists(),
                             "green runs must not create ledger rows")

    def test_repeated_failures_append_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "led.jsonl"
            run_with_ledger([boom_case()], ledger)
            run_with_ledger([boom_case()], ledger)
            self.assertEqual(len(ledger.read_text().splitlines()), 2,
                             "evidence accumulates; never rewritten")
