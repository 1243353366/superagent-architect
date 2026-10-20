"""The saarch command line: five inputs in, verified repository out.

Project lifecycle (validated, resumable states):
    DRAFT -> SPECIFIED -> PLANNED -> GENERATING -> VERIFYING -> READY
      -> any step may -> BLOCKED (diagnosed) or CHALLENGED (needs acceptance)
      -> prohibited input -> REFUSED (non-overridable)

Exit codes (stable contract for scripting and CI):
    0  success (including READY)
    1  verification failed (after repair) — see diagnosis/proposal.md
    2  invalid input, bad state, or missing prerequisites
    3  REFUSED — prohibited work, non-overridable
    4  CHALLENGED — contradictory/underspecified input awaiting acceptance
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .audit import AuditLog
from .challenge import detect_challenges, resolve_conflicts
from .delivery import GitHubDelivery, export_zip, get_github_token
from .generator import generate, plan_tasks
from .models import (
    Assumption, BLOCKED, CHALLENGED, DRAFT, GENERATING, PLANNED, READY,
    REFUSED, SPECIFIED, VERIFYING,
    Project, ProjectIntake, ValidationError,
)
from .policy import screen_intake
from .provider import get_provider
from .spec_builder import build_spec, slugify
from .verifier import repair, verify


def _workdir(args) -> Path:
    p = Path(args.dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_or_fail(workdir: Path) -> Project:
    try:
        return Project.load(workdir)
    except ValidationError as exc:
        print(f"error: {exc}\nrun 'saarch init' first to create a project")
        raise SystemExit(2)


def _print_checks(report) -> None:
    for c in report.checks:
        state = "PASS" if c.passed else ("SKIP" if not c.executed else "FAIL")
        print(f"  [{state}] {c.name}")
        if not c.passed and c.detail:
            detail = c.detail.strip()
            if len(detail) > 1200:
                detail = "…" + detail[-1200:]
            print(f"         {detail}")


# ---------------------------------------------------------------- commands

def cmd_init(args) -> None:
    workdir = _workdir(args)
    if (workdir / "project.json").exists() and not getattr(args, "force", False):
        print("error: project already exists here (use --force to overwrite)")
        raise SystemExit(2)
    intake = ProjectIntake(
        instructions=args.instructions,
        scope=args.scope,
        capabilities=[c.strip() for c in args.capabilities.split(";") if c.strip()],
        guardrails=args.guardrails,
        language=args.language,
    )
    project = Project(state=DRAFT, intake=intake)
    try:
        intake.validate()
    except ValidationError as exc:
        project.transition(BLOCKED, f"intake invalid: {exc}")
        project.save(workdir)
        AuditLog(workdir).append("intake_rejected", reason=str(exc))
        print(f"BLOCKED — intake invalid:\n  {exc}")
        raise SystemExit(2)

    project.slug = slugify(args.slug or intake.instructions)[:60] or "project"

    # --- principled refusal: prohibited work is refused categorically.
    # No flag bypasses this — by design.
    findings = screen_intake(intake)
    if findings:
        project.policy_findings = findings
        project.transition(REFUSED, "; ".join(findings))
        project.save(workdir)
        AuditLog(workdir).append("intake_refused", findings=findings)
        print("REFUSED — prohibited work, categorically:")
        for f in findings:
            print(f"  - {f}")
        print("This refusal cannot be accepted away or retried with other inputs.")
        raise SystemExit(3)

    project.save(workdir)
    AuditLog(workdir).append("intake_accepted", slug=project.slug,
                             language=intake.language,
                             capabilities=intake.normalized_capabilities())
    print(f"DRAFT project '{project.slug}' saved to {workdir / 'project.json'}")


def cmd_spec(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)

    if project.state == REFUSED:
        print(f"REFUSED — {project.refusal_reason}")
        print("Refusals are non-overridable; start a new project instead.")
        raise SystemExit(3)
    if project.state == SPECIFIED:
        print("already SPECIFIED (run 'saarch plan')")
        return

    # principled refusal re-screen (defense in depth; also catches edits)
    findings = screen_intake(project.intake)
    if findings:
        project.policy_findings = findings
        project.transition(REFUSED, "; ".join(findings))
        project.save(workdir)
        audit.append("intake_refused", findings=findings, stage="spec")
        print("REFUSED — prohibited work, categorically:")
        for f in findings:
            print(f"  - {f}")
        raise SystemExit(3)

    # --- constructive resistance: challenge contradictory/underspecified input
    accept = bool(getattr(args, "accept_assumptions", False))
    challenges = detect_challenges(project.intake)
    project.challenges = challenges
    gates = [c for c in challenges if c.severity == "gate"]
    if gates and not accept and project.state == DRAFT:
        project.transition(CHALLENGED)
        project.save(workdir)
        audit.append("challenged",
                     challenges=[{k: getattr(c, k)
                                  for k in ("id", "kind", "message")} for c in gates])
        print("CHALLENGED — the inputs contradict or underspecify the request:")
        for c in gates:
            print(f"  [{c.kind}] {c.message}")
            print(f"           evidence: {c.evidence}")
            print(f"           resolution: {c.resolution}")
        print("\n  To proceed, either edit the inputs (saarch init --force …),")
        print("  or accept recorded assumptions — guardrails win over conflicting")
        print("  capabilities:  saarch spec --accept-assumptions")
        raise SystemExit(4)

    if project.state == CHALLENGED:
        project.transition(SPECIFIED)
    else:
        project.transition(SPECIFIED)

    kept, dropped = (resolve_conflicts(project.intake, challenges) if gates
                     else (project.intake.normalized_capabilities(), []))
    project.spec = build_spec(project, provider=get_provider(), audit=audit,
                              capabilities_override=(kept or None))
    if accept:
        project.challenges_accepted = True
        for cap in dropped:
            project.spec.assumptions.append(Assumption(
                f"Capability dropped by guardrail priority (accepted by user): “{cap}”"))
        for c in challenges:
            project.spec.assumptions.append(Assumption(
                f"Accepted challenge ({c.id}): {c.message} — resolution recorded."))
        audit.append("challenges_accepted", dropped=dropped,
                     accepted=[c.id for c in challenges if c.severity == "gate"])
    for c in challenges:
        if c.severity == "note":
            project.spec.assumptions.append(Assumption(
                f"Recorded challenge ({c.id}): {c.message}"))
    project.save(workdir)

    s = project.spec
    print(f"SPECIFIED — v{s.version} (profile: {s.profile})")
    print(f"  entities:   {', '.join(e.name for e in s.entities)}")
    print(f"  endpoints:  {len(s.endpoints)}")
    print(f"  exclusions: {len(s.exclusions)}")
    if dropped:
        print(f"  DROPPED by guardrail priority: {'; '.join(dropped)}")
    print(f"  assumptions recorded: {len(s.assumptions)}")
    for q in s.questions:
        print(f"  QUESTION: {q.text}")


def cmd_plan(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    if project.state == PLANNED:
        print("already PLANNED (run 'saarch generate')")
        return
    project.transition(PLANNED)
    project.plan = plan_tasks()
    project.save(workdir)
    AuditLog(workdir).append("planned", tasks=[t.id for t in project.plan])
    print(f"PLANNED — {len(project.plan)} tasks in dependency order")


def cmd_generate(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state not in (GENERATING, VERIFYING):
        project.transition(GENERATING)
    out = generate(project, workdir, audit)
    project.save(workdir)
    n = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"GENERATED {n} files -> {out}")


def cmd_verify(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state in (GENERATING, READY):
        project.transition(VERIFYING)
    elif project.state != VERIFYING:
        print(f"error: generate before verifying (state is {project.state}); "
              "run 'saarch generate' first")
        raise SystemExit(2)
    project.verification = verify(project, workdir, audit)
    project.save(workdir)
    _print_checks(project.verification)
    if project.verification.passed:
        project.transition(READY)
        project.save(workdir)
        print("READY — verification passed")
    else:
        project.transition(BLOCKED, "verification failed (run 'saarch repair')")
        project.save(workdir)
        print("BLOCKED — verification failed")
        raise SystemExit(1)


def cmd_repair(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state == BLOCKED:
        project.transition(VERIFYING)  # re-open a diagnosed project for repair
    if project.state != VERIFYING:
        print("nothing to repair (run 'saarch generate' then 'saarch verify')")
        raise SystemExit(2)

    # --- adaptive persistence: strategy ladder + stagnation detection
    report, outcome, proposal_path = repair(project, workdir, audit)
    project.verification = report
    if report.passed:
        project.transition(READY)
        project.save(workdir)
        _print_checks(report)
        print("READY — verification passed after repair")
    else:
        if outcome == "stagnation-diagnosed":
            reason = ("repair stopped: the same failure persisted across different "
                      "repair strategies; a diagnosis and a proposed specification "
                      "revision were written instead of repeating the attempt")
        else:
            reason = "repair budget exhausted; verification still failing"
        project.transition(BLOCKED, reason)
        project.save(workdir)
        _print_checks(report)
        print(f"BLOCKED — {reason}")
        if proposal_path:
            print(f"Diagnosis and proposal: {proposal_path}")
        raise SystemExit(1)


def cmd_serve(args) -> None:
    """Launch the generated application and open it in the browser."""
    import socket
    import subprocess as sp
    import webbrowser

    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    app_dir = workdir / "generated" / project.slug
    if not (app_dir / "run.py").is_file():
        print("error: nothing to serve yet — run 'saarch run …' or "
              "'saarch generate' first")
        raise SystemExit(2)

    port = args.port
    if port == 0:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

    env = dict(os.environ)
    env["APP_HOST"] = "127.0.0.1"
    env["APP_PORT"] = str(port)
    env.setdefault("APP_DB", "app.db")
    proc = sp.Popen([sys.executable, "run.py"], cwd=str(app_dir), env=env)
    url = f"http://127.0.0.1:{port}"
    print(f"serving {url} (Ctrl+C here to stop)")
    if not args.no_open:
        print(f"launching {url} in your browser")
        webbrowser.open(url)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print("\nstopped")


def cmd_deliver(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state != READY:
        print("error: project is not READY; run the pipeline first "
              "('saarch run' or 'saarch verify')")
        raise SystemExit(2)
    generated = workdir / "generated" / project.slug
    if args.github:
        token = get_github_token()
        if not token:
            print("error: GitHub delivery requires the GITHUB_TOKEN "
                  "environment variable. Falling back to zip export.")
            args.github = False
        else:
            client = GitHubDelivery(token)
            result = client.deliver(
                generated,
                repo_name=args.repo_name or project.slug,
                description=(project.spec.instructions or project.slug)[:200].strip())
            audit.append("delivered_github", repo=result["repo_url"],
                         files=result["files"])
            print(f"DELIVERED — {result['files']} files -> {result['repo_url']}")
            return
    if not args.github:
        archive = export_zip(generated, workdir / "export")
        audit.append("delivered_zip", archive=str(archive))
        print(f"DELIVERED — zip archive at {archive}")


def cmd_run(args) -> None:
    workdir = _workdir(args)
    if not (workdir / "project.json").exists():
        ns = argparse.Namespace(
            dir=args.dir, instructions=args.instructions, scope=args.scope,
            capabilities=args.capabilities, guardrails=args.guardrails,
            language=args.language, slug=args.slug, force=False)
        cmd_init(ns)
    project = _load_or_fail(workdir)
    accept = bool(getattr(args, "accept_assumptions", False))
    for step in (cmd_spec, cmd_plan, cmd_generate, cmd_verify):
        step(argparse.Namespace(dir=args.dir, accept_assumptions=accept))
        project = _load_or_fail(workdir)
        if project.state == CHALLENGED:
            print("\nPipeline paused at the challenge gate (see above). "
                  "Constructive resistance does not auto-accept: re-run with "
                  "--accept-assumptions, or edit the inputs.")
            raise SystemExit(4)
        if project.state == BLOCKED:
            print(f"BLOCKED at verification; attempting bounded repair "
                  f"(budget {project.repair_budget})")
            try:
                cmd_repair(argparse.Namespace(dir=args.dir))
            except SystemExit:
                print(f"pipeline stopped: {project.blocked_reason}")
                raise
    if args.deliver:
        cmd_deliver(argparse.Namespace(
            dir=args.dir, github=args.github,
            repo_name=getattr(args, "repo_name", "")))


def cmd_status(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    print(f"state:  {project.state}")
    print(f"slug:   {project.slug}")
    if project.state == REFUSED:
        print(f"refused: {project.refusal_reason}")
        return
    if project.challenges:
        label = (" (accepted)" if project.challenges_accepted
                 else " (awaiting decision)")
        print(f"challenges{label}:")
        for c in project.challenges:
            print(f"  [{c.severity}/{c.kind}] {c.id}: {c.message}")
    if project.blocked_reason:
        print(f"reason: {project.blocked_reason}")
    if project.spec:
        print(f"spec:   {len(project.spec.entities)} entities, "
              f"{len(project.spec.endpoints)} endpoints")
    if project.verification:
        print("checks:")
        _print_checks(project.verification)


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="saarch",
        description="SUPERAGENT: Autonomous Application Architect & Repository "
                    "Generator — five inputs in, a verified application out.",
        epilog="exit codes: 0 ok · 1 verification failed · 2 invalid input/state "
               "· 3 REFUSED (prohibited) · 4 CHALLENGED (needs acceptance)")
    p.add_argument("--version", action="version", version=f"saarch {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    intake = argparse.ArgumentParser(add_help=False)
    intake.add_argument("--instructions", required=True,
                        help="what the application must accomplish (input 1)")
    intake.add_argument("--scope", required=True,
                        help="boundaries; lines like 'exclude: X' become exclusions (input 2)")
    intake.add_argument("--capabilities", required=True,
                        help="semicolon-separated capabilities (input 3)")
    intake.add_argument("--guardrails", required=True,
                        help="security/privacy/operational restrictions (input 4)")
    intake.add_argument("--language", required=True,
                        help="primary implementation language (input 5; python supported)")
    intake.add_argument("--slug", default="", help="project name (derived if empty)")

    pi = sub.add_parser("init", parents=[intake], help="capture the five inputs")
    pi.add_argument("--dir", default=".", help="project workspace directory")
    pi.add_argument("--force", action="store_true", help="overwrite existing project")
    pi.set_defaults(func=cmd_init)

    ps = sub.add_parser("spec", help="derive the structured specification "
                                     "(challenge gate applies)")
    ps.add_argument("--dir", default=".")
    ps.add_argument("--accept-assumptions", action="store_true",
                    help="accept gate-level challenges; guardrails win over "
                         "conflicting capabilities, and acceptance is recorded")
    ps.set_defaults(func=cmd_spec)

    for name, help_ in (("plan", "produce the dependency-ordered build plan"),
                        ("generate", "render the full application"),
                        ("verify", "run real checks in isolation"),
                        ("repair", "adaptive repair: stops repeating failed "
                                   "approaches, diagnoses, proposes a better spec"),
                        ("status", "show project state")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--dir", default=".")
        sp.set_defaults(func=getattr(sys.modules[__name__], f"cmd_{name}"))

    psv = sub.add_parser("serve", help="launch the generated app in your browser")
    psv.add_argument("--dir", default=".")
    psv.add_argument("--port", type=int, default=0,
                     help="port to serve on (default: pick a free port)")
    psv.add_argument("--no-open", action="store_true",
                     help="do not open the browser automatically")
    psv.set_defaults(func=cmd_serve)

    pd = sub.add_parser("deliver", help="export zip or push to GitHub")
    pd.add_argument("--dir", default=".")
    pd.add_argument("--github", action="store_true",
                    help="push to GitHub (requires GITHUB_TOKEN)")
    pd.add_argument("--repo-name", default="")
    pd.set_defaults(func=cmd_deliver)

    pr = sub.add_parser("run", parents=[intake], help="full pipeline in one command")
    pr.add_argument("--dir", default=".")
    pr.add_argument("--deliver", action="store_true", help="deliver after READY")
    pr.add_argument("--github", action="store_true",
                    help="deliver to GitHub (requires GITHUB_TOKEN)")
    pr.add_argument("--accept-assumptions", action="store_true",
                    help="accept gate-level challenges when running the pipeline")
    pr.set_defaults(func=cmd_run)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
