"""Isolated-execution tests: sandboxing, scrubbed env, honest reporting."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from saarch.audit import AuditLog
from saarch.execution import (
    isolated_sandbox, run_isolated, run_check_isolated, _scrubbed_env)


class IsolatedSandboxTests(unittest.TestCase):
    def setUp(self):
        self.src = Path(tempfile.mkdtemp(prefix="saarch-exec-src-"))
        (self.src / "app").mkdir()
        (self.src / "app" / "main.py").write_text("print('hi')\n")
        (self.src / "app" / "cache.db").write_text("data")
        (self.src / "app" / "__pycache__").mkdir()
        (self.src / "app" / "__pycache__" / "x.pyc").write_text("junk")

    def test_sandbox_is_a_private_clean_copy(self):
        with isolated_sandbox(self.src / "app") as sandbox:
            self.assertNotEqual(sandbox, self.src)
            self.assertTrue((sandbox / "main.py").is_file())
            # caches and databases are excluded: execution starts clean
            self.assertFalse((sandbox / "cache.db").exists())
            self.assertFalse((sandbox / "__pycache__").exists())
            # writes inside the sandbox never touch the source
            (sandbox / "side_effect.txt").write_text("leaked?")
        self.assertFalse((self.src / "side_effect.txt").exists())
        self.assertTrue((self.src / "app" / "main.py").is_file())

    def test_missing_source_raises(self):
        with self.assertRaises(Exception):
            with isolated_sandbox(self.src / "nope"):
                pass

    def test_env_scrubbing(self):
        os.environ["SAARCH_TEST_SECRET"] = "topsecret"
        try:
            env = _scrubbed_env()
            self.assertNotIn("SAARCH_TEST_SECRET", env)
        finally:
            del os.environ["SAARCH_TEST_SECRET"]

    def test_run_isolated_no_secret_leak(self):
        os.environ["GITHUB_TOKEN"] = "ghp_should_not_leak"
        try:
            rc, out, err, timed_out = run_isolated(
                [sys.executable, "-c",
                 "import os; print('LEAK' if 'GITHUB_TOKEN' in os.environ else 'CLEAN')"],
                self.src / "app")
            self.assertEqual(rc, 0)
            self.assertIn("CLEAN", out)
            self.assertNotIn("ghp_should_not_leak", out + err)
        finally:
            del os.environ["GITHUB_TOKEN"]

    def test_run_check_isolated_records_audit(self):
        audit_dir = Path(tempfile.mkdtemp(prefix="saarch-exec-audit-"))
        audit = AuditLog(audit_dir)
        rc, out, err, timed_out = run_check_isolated(
            [sys.executable, "-c", "print('ok')"], self.src / "app",
            audit=audit, name="demo-check")
        self.assertEqual(rc, 0)
        entries = [e for e in audit.entries() if e["action"] == "isolated_check"]
        self.assertTrue(entries and entries[0]["check"] == "demo-check")


if __name__ == "__main__":
    unittest.main()
