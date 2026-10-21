"""Dependency-provenance check tests (incl. the Python 3.9 fallback)."""
import tempfile
import unittest
from pathlib import Path

from saarch.verifier import _stdlib_modules, check_dependency_provenance


class StdlibAllowlistTests(unittest.TestCase):
    def test_current_interpreter_allowlist_includes_stdlib(self):
        stdlib = _stdlib_modules()
        self.assertIn("sqlite3", stdlib)
        self.assertIn("json", stdlib)
        self.assertIn("sys", stdlib)          # builtin
        self.assertNotIn("flask", stdlib)

    def test_fallback_derivation_for_python_before_310(self):
        # simulate a pre-3.10 interpreter: no stdlib_module_names
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d)
            (fake / "sqlite3.py").write_text("")
            (fake / "json").mkdir()
            (fake / "config-3.9-x.py").write_text("")   # config artifacts
            (fake / "__pycache__").mkdir()
            stdlib = _stdlib_modules(stdlib_names=None,
                                     builtin_names=("sys", "_thread"),
                                     stdlib_dir=fake)
            self.assertIn("sqlite3", stdlib)
            self.assertIn("json", stdlib)
            self.assertIn("sys", stdlib)
            self.assertNotIn("__pycache__", stdlib)
            self.assertNotIn("flask", stdlib)


class ProvenanceCheckTests(unittest.TestCase):
    def _app(self, files):
        root = Path(tempfile.mkdtemp(prefix="saarch-prov-"))
        (root / "app").mkdir()
        (root / "requirements.txt").write_text(
            "# Intentionally empty: standard library only.\n")
        for rel, content in files.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        return root

    def test_stdlib_only_app_passes(self):
        root = self._app({
            "run.py": "from app.main import main\n",
            "app/main.py": "import json\nimport sqlite3\nfrom . import storage\n",
            "app/storage.py": "import threading\n",
        })
        r = check_dependency_provenance(root)
        self.assertTrue(r.passed and r.executed, r.detail)

    def test_third_party_import_fails(self):
        root = self._app({"app/main.py": "import flask\n"})
        r = check_dependency_provenance(root)
        self.assertFalse(r.passed)
        self.assertIn("flask", r.detail)

    def test_declared_dependency_fails(self):
        root = self._app({"app/main.py": "import json\n"})
        (root / "requirements.txt").write_text("flask==3.0.0\n")
        r = check_dependency_provenance(root)
        self.assertFalse(r.passed)
        self.assertIn("flask==3.0.0", r.detail)


if __name__ == "__main__":
    unittest.main()
