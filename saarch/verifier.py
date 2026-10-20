"""Stage E: verification and bounded repair.

Every check runs in a *real* subprocess with a timeout inside the generated
project directory. Nothing is inferred from the builder's claims; a check is
"passed" only if the command actually exited successfully. Checks that cannot
run are reported as skipped, never as passed.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List

from .audit import AuditLog
from .models import CheckResult, Project, VerificationReport

CHECK_TIMEOUT_SECONDS = 240

# Patterns that look like hardcoded credentials. The generated tree must be
# free of these (placeholder files like .env.example are allowed to name keys
# without values).
SECRET_PATTERNS = [
    (r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\b\s*[=:]\s*['\"][^'\"]{6,}['\"]",
     "hardcoded secret assignment"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "embedded private key"),
]
_PLACEHOLDER_ALLOWLIST = ("values", "changeme", "replace-me", "example", "<your")


def _run(cmd: List[str], cwd: Path) -> tuple:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            timeout=CHECK_TIMEOUT_SECONDS)
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", (exc.stderr or "") + f"\n[timeout after {CHECK_TIMEOUT_SECONDS}s]", True
    except OSError as exc:
        return 127, "", str(exc), False


def scan_secrets(root: Path) -> CheckResult:
    """Scan generated files for hardcoded credentials."""
    findings = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in (".py", ".md", ".txt", ".yml",
                                               ".yaml", ".json", ".example", ""):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            for m in re.finditer(pattern, text):
                if any(p in m.group(0).lower() for p in _PLACEHOLDER_ALLOWLIST):
                    continue
                findings.append(f"{f.relative_to(root)}: {label}")
    if findings:
        return CheckResult("secret-scan", passed=False, executed=True,
                           detail="; ".join(findings))
    return CheckResult("secret-scan", passed=True, executed=True,
                       detail="no hardcoded credentials found")


def verify(project: Project, workdir: Path, audit: AuditLog | None = None) -> VerificationReport:
    """Run the available checks against the generated project."""
    app_dir = Path(workdir) / "generated" / project.slug
    if not app_dir.is_dir():
        return VerificationReport(attempts=1, checks=[
            CheckResult("generate-output", passed=False, executed=True,
                        detail="generated project directory missing")])

    checks: List[CheckResult] = []

    rc, out, err, timed_out = _run([sys.executable, "-m", "compileall", "-q", "app"], app_dir)
    checks.append(CheckResult(
        "compileall", passed=(rc == 0 and not timed_out), executed=True,
        detail=(err or out or "")[-2000:] or "clean"))

    rc, out, err, timed_out = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], app_dir)
    passed = (rc == 0 and not timed_out)
    tail = "\n".join((out or "").strip().splitlines()[-12:])
    checks.append(CheckResult(
        "generated-project-tests", passed=passed, executed=True,
        detail=tail or (err or "")[-2000:]))

    checks.append(scan_secrets(app_dir))

    report = VerificationReport(attempts=1, checks=checks)
    if audit:
        audit.append("verified",
                     passed=report.passed,
                     checks=[{k: getattr(c, k) for k in ("name", "passed", "executed")}
                             for c in checks])
    return report


def repair(project: Project, workdir: Path, audit: AuditLog | None = None) -> VerificationReport:
    """Bounded repair loop.

    The deterministic generator makes regeneration the safest repair: the
    templates are the single source of truth and regeneration cannot drift.
    LLM-assisted repair is an integration point (see provider.py), not a
    claim about this build. The loop is bounded by project.repair_budget and
    reports honestly when the budget is exhausted.
    """
    from .generator import generate

    report = verify(project, workdir, audit)
    while (not report.passed
           and project.repairs_used < project.repair_budget):
        project.repairs_used += 1
        if audit:
            failing = [c.name for c in report.checks if c.executed and not c.passed]
            audit.append("repair_attempt", attempt=project.repairs_used, failing=failing)
        generate(project, workdir, audit)
        report = verify(project, workdir, audit)
        report.attempts = project.repairs_used + 1
    if not report.passed and project.repairs_used >= project.repair_budget:
        if audit:
            audit.append("repair_budget_exhausted", budget=project.repair_budget)
    return report
