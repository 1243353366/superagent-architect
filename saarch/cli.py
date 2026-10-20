"""The saarch command line: five inputs in, verified repository out.

Project lifecycle (validated, resumable states):
    DRAFT -> SPECIFIED -> PLANNED -> GENERATING -> VERIFYING -> READY
                       (any step may -> BLOCKED, with a recorded reason)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .audit import AuditLog
from .delivery import GitHubDelivery, export_zip, get_github_token
from .generator import generate, plan_tasks
from .models import (
    BLOCKED, DRAFT, GENERATING, PLANNED, READY, SPECIFIED, VERIFYING,
    Project, ProjectIntake, ValidationError,
)
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
        # blocked immediately, with the batched questions answered by the error
        project.transition(BLOCKED, f"intake invalid: {exc}")
        project.save(workdir)
        AuditLog(workdir).append("intake_rejected", reason=str(exc))
        print(f"BLOCKED — intake invalid:\n  {exc}")
        raise SystemExit(2)
    project.slug = slugify(args.slug or intake.instructions)[:60] or "project"
    project.save(workdir)
    AuditLog(workdir).append("intake_accepted", slug=project.slug,
                             language=intake.language,
                             capabilities=intake.normalized_capabilities())
    print(f"DRAFT project '{project.slug}' saved to {workdir / 'project.json'}")


def cmd_spec(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state == SPECIFIED:
        print("already SPECIFIED (run 'saarch plan')")
        return
    project.transition(SPECIFIED)
    project.spec = build_spec(project, provider=get_provider(), audit=audit)
    project.save(workdir)
    s = project.spec
    print(f"SPECIFIED — v{s.version} (profile: {s.profile})")
    print(f"  entities:   {', '.join(e.name for e in s.entities)}")
    print(f"  endpoints:  {len(s.endpoints)}")
    print(f"  exclusions: {len(s.exclusions)}")
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
    if project.state in (GENERATING, VERIFYING):
        pass  # regeneration is idempotent and allowed
    else:
        project.transition(GENERATING)
    out = generate(project, workdir, audit)
    project.save(workdir)
    n = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"GENERATED {n} files -> {out}")


def cmd_verify(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state == GENERATING:
        project.transition(VERIFYING)
    elif project.state != VERIFYING:
        print("error: generate before verifying (state is "
              f"{project.state}); run 'saarch generate' first")
        raise SystemExit(2)
    project.verification = verify(project, workdir, audit)
    project.save(workdir)
    _print_checks(project.verification)
    if project.verification.passed:
        project.transition(READY)
        project.save(workdir)
        print("READY — verification passed")
    else:
        project.transition(BLOCKED,
                           "verification failed (run 'saarch repair' or inspect details)")
        project.save(workdir)
        print("BLOCKED — verification failed")
        raise SystemExit(1)


def cmd_repair(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    if project.state == BLOCKED:
        project.transition(VERIFYING)  # re-enter the loop
    if project.state != VERIFYING:
        print("nothing to repair (run 'saarch generate' then 'saarch verify')")
        return
    project.verification = repair(project, workdir, audit)
    if project.verification.passed:
        project.transition(READY)
        project.save(workdir)
        _print_checks(project.verification)
        print("READY — verification passed after repair")
    else:
        project.transition(BLOCKED,
                           "repair budget exhausted; verification still failing")
        project.save(workdir)
        _print_checks(project.verification)
        print("BLOCKED — repair budget exhausted; failures reported above")
        raise SystemExit(1)


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
        # fabricate an init call with the CLI args provided on `run`
        ns = argparse.Namespace(
            dir=args.dir, instructions=args.instructions, scope=args.scope,
            capabilities=args.capabilities, guardrails=args.guardrails,
            language=args.language, slug=args.slug, force=False)
        cmd_init(ns)
    project = _load_or_fail(workdir)
    audit = AuditLog(workdir)
    for step in (cmd_spec, cmd_plan, cmd_generate, cmd_verify):
        step(argparse.Namespace(dir=args.dir))
        project = _load_or_fail(workdir)
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
            repo_name=getattr(args, "repo_name", None)))


def cmd_status(args) -> None:
    workdir = _workdir(args)
    project = _load_or_fail(workdir)
    print(f"state:  {project.state}")
    print(f"slug:   {project.slug}")
    if project.blocked_reason:
        print(f"reason: {project.blocked_reason}")
    if project.spec:
        print(f"spec:   {len(project.spec.entities)} entities, "
              f"{len(project.spec.endpoints)} endpoints")
    if project.verification:
        print("checks:")
        _print_checks(project.verification)


# ---------------------------------------------------------------- entry

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="saarch",
        description="SUPERAGENT: Autonomous Application Architect & Repository Generator "
                    "— five inputs in, a verified application repository out.")
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

    for name, help_ in (("spec", "derive the structured specification"),
                        ("plan", "produce the dependency-ordered build plan"),
                        ("generate", "render the full application"),
                        ("verify", "run real checks in isolation"),
                        ("repair", "bounded repair loop"),
                        ("status", "show project state")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--dir", default=".")
        sp.set_defaults(func=getattr(sys.modules[__name__], f"cmd_{name}"))

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
    pr.set_defaults(func=cmd_run)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
