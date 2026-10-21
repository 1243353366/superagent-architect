"""The zombie ledger policy: tamper-evident, append-only, and no zombie
dies without a documented disposition."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.zombies import ZombieLedger          # noqa: E402


class LedgerPolicy(unittest.TestCase):

    def test_retirement_requires_disposition(self):
        L = ZombieLedger()
        z = L.raise_zombie("t", {}, {"ops": ["x"]})
        L.minimize(z.id, {"ops": ["y"]}, "d")
        L.mark_fixed(z.id, "fixed by guard")
        with self.assertRaises(ValueError):
            L.retire(z.id, "", "fix note", "2026-10-21")   # no test name
        L.retire(z.id, "tests.test_zombie_regressions.X::test_t",
                 "fix note", "2026-10-21")
        self.assertEqual(z.status, "retired")

    def test_lifecycle_order_is_enforced(self):
        L = ZombieLedger()
        z = L.raise_zombie("t", {}, {"ops": ["x"]})
        with self.assertRaises(ValueError):
            L.mark_fixed(z.id, "skip minimization")        # still walking
        L.minimize(z.id, {"ops": ["y"]}, "d")
        with self.assertRaises(ValueError):
            L.retire(z.id, "t", "n", "2026-10-21")         # not fixed yet

    def test_tampering_is_detectable(self):
        L = ZombieLedger()
        L.raise_zombie("original title", {}, {"ops": ["x"]})
        self.assertEqual(L.verify(), [])
        L.zombies[0].title = "FORGED TITLE"
        self.assertTrue(L.verify(), "forgery went undetected")
        self.assertIn("mutated after chaining", L.verify()[0])


class CommittedLedger(unittest.TestCase):

    def test_committed_ledger_is_verifiable_and_fully_retired(self):
        ledger = json.loads(
            (ROOT / "benchmarks/results/zombies.json").read_text())
        self.assertTrue(ledger, "ledger is empty")
        L = ZombieLedger()
        for r in ledger:
            z = L.raise_zombie(r["record"]["title"],
                               r["record"]["discovered"],
                               r["record"]["repro"],
                               r["record"]["original"])
            L.minimize(z.id, z.repro,
                       r["record"]["discovered"].get("retained_breach", ""))
            L.mark_fixed(z.id, r["record"]["disposition"]["fix_note"])
            L.retire(z.id,
                     r["record"]["disposition"]["regression_test"],
                     r["record"]["disposition"]["fix_note"],
                     r["record"]["disposition"]["retired_at"])
            self.assertEqual(r["record"]["status"], "retired")
        self.assertEqual(L.verify(), [])
        # every disposition names a regression test that really exists
        regressions = (ROOT / "tests/test_zombie_regressions.py").read_text()
        for r in ledger:
            name = r["record"]["disposition"]["regression_test"]
            leaf = name.split("::")[-1].split(".")[-1]
            self.assertIn(leaf, regressions,
                          f"disposition names missing test: {name}")


if __name__ == "__main__":
    unittest.main()
