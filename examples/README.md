# Demo: personal task tracker, generated and verified end-to-end

This directory is **not a mockup**. Everything here was produced by running the
pipeline in this repository, and every claim below has execution evidence:

```bash
export PYTHONPATH="$PWD"
python3 -m saarch run \
  --dir examples/demo-workdir \
  --instructions "A personal task tracker for daily planning." \
  --scope "Single user, local data only. exclude: team collaboration, sharing, cloud sync" \
  --capabilities "create tasks with a title and notes; mark tasks as done; delete tasks" \
  --guardrails "No external network calls. Data stays on the local machine. No telemetry." \
  --language python \
  --slug personal-task-tracker
```

Console output (real run):

```
DRAFT project 'personal-task-tracker' saved to examples/demo-workdir/project.json
SPECIFIED — v1 (profile: python-stdlib-sqlite)
  entities:   task
  endpoints:  6
  exclusions: 1
  assumptions recorded: 4
PLANNED — 9 tasks in dependency order
GENERATED 14 files -> examples/demo-workdir/generated/personal-task-tracker
  [PASS] compileall
  [PASS] generated-project-tests
  [PASS] secret-scan
READY — verification passed
```

## What is in this directory

- `generated-task-tracker/` — the actual generated application (the run's
  output, with `__pycache__` and the test database removed). Run it:
  `python run.py`, test it: `python -m unittest discover -s tests -v`.
- `audit-trail.jsonl` — the append-only audit log of the run: intake, spec,
  plan, generation, verification.
- `verification-report.md` — the executed checks and their evidence.
- `demo-workdir/` — the resumable workspace (project state machine + audit
  + generated output), exactly as the CLI left it.

## Delivered to GitHub

The same generated application was delivered to a live repository by the
tool's own delivery layer (Stage F):

**https://github.com/1243353366/saarch-demo-task-tracker**

13 files pushed in a single commit via the GitHub REST API, using only the
Python standard library.
