"""Suite-failure ledger: when a test run fails, its evidence must
outlive the run. Capability C-suite-failure-ledger — the antibody for
the F-SUITE-FLAKE incident, whose five intermittent failures vanished
between runs because nothing persisted the failing test's name and
output. Green runs leave no row; failing runs append
{timestamp, python, tests, failures: [{id, error}]} to a JSONL ledger.
"""
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


def run_with_ledger(test_names, ledger_path):
    """Run the given tests (names like 'module.Class.test', module
    names, or unittest.TestCase classes); on failure append one row to
    the ledger. Returns a summary dict; the row content is the point:
    a durable, greppable record of WHAT failed and HOW."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in test_names:
        if isinstance(name, type) and issubclass(name, unittest.TestCase):
            suite.addTests(loader.loadTestsFromTestCase(name))
        else:
            suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(
        stream=io.StringIO(), verbosity=0).run(suite)
    n_failed = len(result.failures) + len(result.errors)
    rows = 0
    if n_failed:
        row = {"ts": datetime.now(timezone.utc).isoformat(),
               "python": sys.version.split()[0],
               "tests": [n if isinstance(n, str) else n.__name__
                         for n in test_names],
               "failures": [{"id": t.id(), "error": e[-2000:]}
                            for t, e in result.failures + result.errors]}
        path = Path(ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        rows = 1
    return {"ran": result.testsRun, "failed": n_failed,
            "ledger_rows": rows}


if __name__ == "__main__":
    # CLI: python -m saarch.suite_ledger tests.test_x [more...]
    out = run_with_ledger(
        sys.argv[1:],
        Path("benchmarks/results/suite-failures.jsonl"))
    print(json.dumps(out))
    sys.exit(1 if out["failed"] else 0)
