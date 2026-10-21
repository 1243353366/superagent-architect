"""Stage E: verification, bounded repair, and adaptive persistence.

Verification runs REAL subprocesses with a timeout inside the generated
project directory; a check is "passed" only if the command actually exited
successfully, and checks that cannot run are reported as skipped — never as
passed.

Adaptive persistence (the third enforced behavior):
  repair is a strategy ladder, not a retry loop. Each attempt uses a
  *different* approach (full regeneration, then minimal generation), and
  failure signatures are tracked across attempts. When the same signature
  repeats, the loop stops — it does not burn the remaining budget repeating
  what already demonstrably failed — and instead produces a diagnosis and
  a concrete proposal for a better specification (proposal.md).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .audit import AuditLog
from .execution import run_check_isolated
from .generator import generate
from .models import CheckResult, Project, VerificationReport

CHECK_TIMEOUT_SECONDS = 240

OUTCOME_PASSED = "passed"
OUTCOME_BUDGET_EXHAUSTED = "budget-exhausted"
OUTCOME_STAGNATION_DIAGNOSED = "stagnation-diagnosed"

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

    # Isolated execution: every executable check runs inside a throwaway
    # snapshot of the generated project (execution.py) — a scrubbed
    # environment, a hard timeout, and no writes to the real tree.
    rc, out, err, timed_out = run_check_isolated(
        [sys.executable, "-m", "compileall", "-q", "app"], app_dir,
        audit=audit, name="compileall")
    checks.append(CheckResult(
        "compileall", passed=(rc == 0 and not timed_out), executed=True,
        detail=(err or out or "")[-2000:] or "clean"))

    rc, out, err, timed_out = run_check_isolated(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        app_dir, audit=audit, name="generated-project-tests")
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


# ------------------------------------------------- adaptive persistence

def _failure_signature(report: VerificationReport) -> str:
    """Stable signature of *how* verification is failing.

    Normalizes paths, numbers, and timings out of failure output so the same
    underlying failure yields the same signature across attempts.
    """
    parts = []
    for c in sorted(report.checks, key=lambda c: c.name):
        if c.executed and not c.passed:
            norm = re.sub(r"\d+", "N", (c.detail or ""))[-400:]
            norm = re.sub(r"/[^\s:]+/", "<path>/", norm)
            parts.append(f"{c.name}::{norm}")
    if not parts:
        return "no-failure"
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


_DIAGNOSIS_RULES = [
    ("environment",
     r"(ModuleNotFoundError|ImportError|timeout|TimeoutExpired|PermissionError|OSError)",
     "The failure is environmental, not architectural: a dependency, tool, "
     "permission, or resource the execution environment does not provide."),
    ("generation-defect",
     r"(SyntaxError|IndentationError|NameError|AttributeError|TabError)",
     "The generator itself produced invalid code. This is a tool bug, not a "
     "specification problem."),
    ("test-fixture",
     r"(fixture|setupclass|setUP|ImportError.*test)",
     "The generated tests cannot initialize; likely a schema or import "
     "mismatch between modules."),
]


def diagnose(report: VerificationReport) -> dict:
    """Classify the failure and propose the next specification-level move."""
    detail = "\n".join(c.detail for c in report.checks if not c.passed)
    for category, pattern, summary in _DIAGNOSIS_RULES:
        if re.search(pattern, detail, re.IGNORECASE):
            return {"category": category, "summary": summary}
    return {
        "category": "behavior-mismatch",
        "summary": "The generated code runs but does not satisfy the derived "
                   "acceptance criteria. The specification's requirements and "
                   "the implementation disagree; changing the code alone has "
                   "already failed, so the specification is the thing to revise.",
    }


def _proposal_steps(diagnosis: dict, project: Project) -> List[str]:
    cat = diagnosis["category"]
    if cat == "behavior-mismatch":
        return [
            "Simplify the capabilities to the smallest set that still meets "
            "your actual goal (each capability currently maps to a resource "
            "with CRUD + tests).",
            "Rewrite any capability that mixes several responsibilities into "
            "one-line, single-verb statements.",
            "Re-run: saarch spec && saarch generate && saarch verify.",
            "If the minimal build (first resource only) passed while the full "
            "build failed, re-add resources one capability at a time to "
            "isolate the breaking requirement.",
        ]
    if cat == "environment":
        return [
            "Run verification in an environment with the missing dependency "
            "or permission named in the failure output above.",
            "Check the Python version (this profile requires >= 3.9).",
            "Re-run: saarch verify.",
        ]
    if cat == "generation-defect":
        return [
            "This is a bug in superagent-architect's templates, not in your "
            "specification. File an issue with proposal.md attached.",
            "Until fixed, the minimal build may still be usable: saarch "
            "generate && saarch verify with fewer capabilities.",
        ]
    return ["Review the failing checks above and revise the specification."]


def write_proposal(project: Project, workdir: Path,
                   report: VerificationReport, signature: str,
                   attempts_used: int, diagnosis: dict) -> Path:
    """Write the proposed better specification after stagnation."""
    failing = [c for c in report.checks if not c.passed]
    lines = [
        "# Diagnosis and proposed specification revision",
        "",
        f"Project: **{project.slug}** — state: {project.state}",
        f"Repair attempts used: {attempts_used} (strategies tried: "
        + ", ".join(sorted({entry.get("strategy", "?") for entry in project.repair_log})) + ")",
        f"Stagnation detected: failure signature `{signature}` repeated across "
        "different repair strategies. Repeating the same approach stopped, "
        "per the adaptive-persistence rule.",
        "",
        "## Diagnosis",
        "",
        f"**Category:** {diagnosis['category']}",
        "",
        diagnosis["summary"],
        "",
        "## Failing checks (actual output)",
        "",
    ]
    for c in failing:
        lines.append(f"### {c.name}")
        lines.append("```")
        lines.append((c.detail or "").strip()[-1500:])
        lines.append("```")
    lines += ["", "## Proposed next steps", ""]
    lines += [f"{i}. {step}" for i, step in enumerate(_proposal_steps(diagnosis, project), 1)]
    lines += [
        "",
        "---",
        "This proposal was generated because repeating the failing build was "
        "no longer useful. The pipeline is resumable: revise the inputs, "
        "then run `saarch spec --accept-assumptions` (if challenges appear) "
        "and continue.",
    ]
    path = Path(workdir) / "proposal.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def repair(project: Project, workdir: Path,
          audit: Optional[AuditLog] = None) -> Tuple[VerificationReport, str, Optional[Path]]:
    """Adaptive, bounded repair.

    Strategy ladder: attempt 1 regenerates fully; attempt 2+ generates the
    minimal core. Between attempts, failure signatures are compared; when the
    same signature repeats consecutively (max_repeat_failures), the loop stops
    early — even with budget remaining — and produces a diagnosis plus a
    concrete proposed revision (proposal.md).
    """
    report = verify(project, workdir, audit)
    signatures: List[str] = [_failure_signature(report)]

    while (not report.passed
           and project.repairs_used < project.repair_budget):
        # stagnation check: the last two observations failed the same way
        if (len(signatures) >= 2
                and signatures[-1] == signatures[-2]
                and signatures[-1] != "no-failure"):
            if audit:
                audit.append("repair_stagnation_detected",
                             signature=signatures[-1],
                             repairs_used=project.repairs_used,
                             budget=project.repair_budget)
            diagnosis = diagnose(report)
            proposal = write_proposal(project, workdir, report,
                                      signatures[-1], project.repairs_used, diagnosis)
            if audit:
                audit.append("diagnosed", category=diagnosis["category"],
                             proposal=str(proposal))
            return report, OUTCOME_STAGNATION_DIAGNOSED, proposal

        strategy = ("regenerate-minimal"
                    if project.repairs_used >= 1 else "regenerate-full")
        project.repairs_used += 1
        if audit:
            audit.append("repair_attempt",
                         attempt=project.repairs_used, strategy=strategy,
                         previous_signature=signatures[-1])
        generate(project, workdir, audit, minimal=(strategy == "regenerate-minimal"))
        report = verify(project, workdir, audit)
        report.attempts = project.repairs_used + 1
        project.repair_log.append({
            "attempt": project.repairs_used, "strategy": strategy,
            "signature": _failure_signature(report),
            "passed": report.passed,
        })
        signatures.append(_failure_signature(report))

    if report.passed:
        return report, OUTCOME_PASSED, None
    # budget exhausted without stagnation repeating — still diagnose honestly
    diagnosis = diagnose(report)
    proposal = write_proposal(project, workdir, report,
                              signatures[-1], project.repairs_used, diagnosis)
    if audit:
        audit.append("repair_budget_exhausted",
                     budget=project.repair_budget,
                     diagnosis=diagnosis["category"],
                     proposal=str(proposal))
    return report, OUTCOME_BUDGET_EXHAUSTED, proposal
