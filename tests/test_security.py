"""Security and honesty tests: no secrets in output, honest skip reporting,
and CLI end-to-end smoke test."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saarch.verifier import CheckResult, scan_secrets

BAD = {
    "leaky.py": 'API_KEY = "sk-live-abcdef123456"\n',
    "clean.py": 'TITLE_MAX = 200\n',
    "env.py": "password = 'values-go-here-changeme'\n",
}


class SecurityTests(unittest.TestCase):
    def test_secret_scan_detects_hardcoded_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in BAD.items():
                (root / name).write_text(content, encoding="utf-8")
            result = scan_secrets(root)
            self.assertTrue(result.executed)
            self.assertFalse(result.passed)
            self.assertIn("leaky.py", result.detail)
            self.assertIn("hardcoded secret", result.detail)

    def test_secret_scan_passes_clean_tree_with_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.py").write_text(
                "import os\nTOKEN = os.environ.get('API_TOKEN')\n", encoding="utf-8")
            (root / ".env.example").write_text(
                "# placeholders only\nAPI_TOKEN=\n", encoding="utf-8")
            result = scan_secrets(root)
            self.assertTrue(result.passed, msg=result.detail)

    def test_skipped_checks_are_never_reported_passed(self):
        r = CheckResult(name="optional-lint", passed=False, executed=False,
                        detail="tool not installed")
        self.assertFalse(r.passed)


class CliSmokeTests(unittest.TestCase):
    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "saarch", "run",
                 "--dir", tmp,
                 "--instructions", "A personal habit tracker.",
                 "--scope", "Single user. exclude: social features",
                 "--capabilities", "log daily habits",
                 "--guardrails", "Data stays local.",
                 "--language", "python"],
                capture_output=True, text=True, timeout=300,
                cwd=Path(__file__).resolve().parents[1])
            out = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, msg=out[-2000:])
            self.assertIn("READY", out)
            # the generated project really is there and its state is persisted
            self.assertTrue((Path(tmp) / "project.json").exists())
            self.assertTrue((Path(tmp) / "generated" / "a-personal-habit-tracker"
                             / "run.py").exists())

    def test_cli_rejects_unsupported_language_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "saarch", "init",
                 "--dir", tmp,
                 "--instructions", "Something in rust",
                 "--scope", "x",
                 "--capabilities", "build it",
                 "--guardrails", "none",
                 "--language", "rust"],
                capture_output=True, text=True, timeout=60,
                cwd=Path(__file__).resolve().parents[1])
            out = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("BLOCKED", out)
            self.assertIn("not supported", out)


if __name__ == "__main__":
    unittest.main()
