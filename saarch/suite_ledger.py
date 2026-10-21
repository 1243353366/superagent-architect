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


def discover_suite(ledger_path, pattern="test*.py",
                    tests_dir=None):
    """Discover and run the FULL suite, timing every test. On failure,
    one ledger row with the context G-002 needs to be narrowed or
    reproduced: failing ids and full errors, the complete test order,
    per-test durations, and environment. Green runs leave no row."""
    import platform
    import time
    loader = unittest.TestLoader()
    root = Path(__file__).resolve().parents[1]
    tests_dir = Path(tests_dir) if tests_dir else root / "tests"
    # top_level_dir must be the repository root, or modules import as
    # 'test_x' instead of 'tests.test_x' and cross-module imports
    # create duplicate module objects (26 false failures in the first
    # capture runs — recorded in the ledger, root-caused, fixed)
    suite = loader.discover(str(tests_dir),
                             pattern=pattern,
                             top_level_dir=str(tests_dir.parent))
    # timing must not bypass TestSuite.run: class and module fixtures
    # (setUpClass) are handled by the SUITE, not by bare test calls —
    # the first harness version called tests directly and failed 26
    # fixture-dependent tests every run (root-caused from its own
    # ledger rows, fixed by running the real suite with a timed
    # result class)
    import time as _time

    class TimedResult(unittest.TextTestResult):
        timings = []

        def startTest(self, test):
            self._t0 = _time.time()
            super().startTest(test)

        def _stamp(self, test):
            self.timings.append(
                {"id": test.id(),
                 "seconds": round(_time.time() - self._t0, 4)})

        def addSuccess(self, test):
            self._stamp(test)
            super().addSuccess(test)

        def addFailure(self, test, err):
            self._stamp(test)
            super().addFailure(test, err)

        def addError(self, test, err):
            self._stamp(test)
            super().addError(test, err)

        def addSkip(self, test, reason):
            self._stamp(test)
            super().addSkip(test, reason)

    runner = unittest.TextTestRunner(
        stream=io.StringIO(), verbosity=0, resultclass=TimedResult)
    result = runner.run(suite)
    durations = result.timings
    n_failed = len(result.failures) + len(result.errors)
    row = None
    if n_failed:
        row = {"ts": datetime.now(timezone.utc).isoformat(),
               "python": sys.version.split()[0],
               "platform": platform.platform(),
               "mode": "discover",
               "tests_run": result.testsRun,
               "failures": [{"id": t.id(), "error": e[-2000:]}
                            for t, e in
                            result.failures + result.errors],
               "durations": durations,
               "slowest": sorted(durations,
                                 key=lambda d: -d["seconds"])[:10]}
        path = Path(ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return {"ran": result.testsRun, "failed": n_failed,
            "ledger_row": bool(row),
            "slowest": sorted(durations,
                              key=lambda d: -d["seconds"])[:5]}


if __name__ == "__main__":
    # CLI: python -m saarch.suite_ledger tests.test_x [more...]
    if sys.argv[1:] == ["discover"]:
        out = discover_suite(
            Path("benchmarks/results/suite-failures.jsonl"))
    else:
        out = run_with_ledger(
            sys.argv[1:],
            Path("benchmarks/results/suite-failures.jsonl"))
    print(json.dumps(out))
    sys.exit(1 if out["failed"] else 0)
