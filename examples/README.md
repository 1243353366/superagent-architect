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

**https://github.com/1243353366/saarch-demo-task-tracker** (private)

13 files pushed in a single commit via the GitHub REST API, using only the
Python standard library. Delivered as a **private repository**: generated
applications ship without authentication, so they stay private until
appropriate access controls exist.

---

# Demo 2: personal expense ledger — a materially different application

The first demo is a single-resource task tracker. To prove the engine, not a
template, the second live proof runs a materially different intake: a
financial-domain app with **two derived resources** (`expense`, `budget`),
11 endpoints (vs 6), a two-table schema, and different capabilities.

```bash
python3 -m saarch run \
  --dir examples/ledger-workdir \
  --slug personal-expense-ledger \
  --instructions "A personal expense ledger for tracking monthly spending." \
  --scope "Single user, local data only. exclude: bank feeds, online payments, multi-currency" \
  --capabilities "track expenses with an amount and a category; track budget goals with a monthly limit; mark expenses as reimbursable" \
  --guardrails "No external network calls. Data stays on the local machine. No analytics or telemetry." \
  --language python
```

Console output (real run):

```
DRAFT project 'personal-expense-ledger' saved to examples/ledger-workdir/project.json
SPECIFIED — v1 (profile: python-stdlib-sqlite)
  entities:   expense, budget
  endpoints:  11
  exclusions: 1
  assumptions recorded: 4
PLANNED — 9 tasks in dependency order (planning path: deterministic)
GENERATED 14 files -> examples/ledger-workdir/generated/personal-expense-ledger
  [PASS] compileall
  [PASS] generated-project-tests
  [PASS] secret-scan
READY — verification passed
```

## Same failure-injection standard, applied to demo 2

The acceptance gate's break → detect → fix → prove cycle was run against this
app too (real executions, from `ledger-workdir/audit.jsonl`):

1. **BREAK** — `def broken(:` appended to the generated `app/main.py`
2. **DETECT** — `saarch verify` failed honestly: `[FAIL] compileall`,
   `[FAIL] generated-project-tests` (checks executed in isolated sandboxes)
3. **FIX** — `saarch repair` regenerated and reached
   `READY — verification passed after repair` within budget
4. **PROVE** — repaired app served real HTTP: `GET /health -> 200`,
   `GET / -> 200`; the injected defect is gone from the source

## What is in this directory (demo 2)

- `generated-expense-ledger/` — the actual generated application (the run's
  output, caches and test databases removed). Run it: `python run.py`, test
  it: `python -m unittest discover -s tests -v`.
- `ledger-workdir/` — the resumable workspace exactly as the CLI left it:
  `project.json` (state machine, `plan_path` recorded), `audit.jsonl` (every
  stage, every isolated check, the repair attempt, and the delivery), and
  the generated output.

## Delivered to GitHub

**https://github.com/1243353366/saarch-demo-expense-ledger** (private) —
14 files pushed by `saarch deliver --github` via the REST API, stdlib only.
Private by the delivery layer's default: no authentication layer was
generated, so the repository is not published.
