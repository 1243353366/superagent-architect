# Architecture — five layers

superagent-architect is organized as five layers. Each layer has real modules
behind it — this document maps every claimed capability to the code that
implements it.

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. CORE BUILDER      app generation · user workflow · spec      │
│    cli.py  ·  models.py  ·  spec_builder.py  ·  generator.py   │
├─────────────────────────────────────────────────────────────────┤
│ 2. AGENTIC ENGINE     planning · tool use · repair · refusal   │
│    generator.plan_tasks · provider.py · verifier.repair         │
│    policy.py (refusal authority) · challenge.py (resistance)   │
├─────────────────────────────────────────────────────────────────┤
│ 3. ENGINEERING ENGINE  schema · migrations · backend · UI ·   │
│    test                    generator.py templates:              │
│    storage (SQLite + versioned migrations) · api/main (JSON    │
│    backend) · ui (web UI) · validation · tests (unittest)     │
├─────────────────────────────────────────────────────────────────┤
│ 4. EXECUTION LAYER    sandboxing · real verification ·         │
│    bounded retries     verifier.py: subprocess isolation with  │
│    timeouts, honest skip reporting, repair budget + failure-    │
│    signature stagnation detection                              │
├─────────────────────────────────────────────────────────────────┤
│ 5. DELIVERY LAYER     GitHub integration · export ·            │
│    versioning · iterative edits                                │
│    delivery.py (REST push, zip export) · project.json +         │
│    audit.jsonl (versioned, resumable state)                    │
└─────────────────────────────────────────────────────────────────┘
```

## 1. Core builder — app generation, user workflow, project specification

The user-facing surface and the source of truth.

| Concern | Module | What it actually does |
|---|---|---|
| User workflow | `saarch/cli.py` | `init · spec · plan · generate · verify · repair · serve · deliver · run · status`; stable exit-code contract (0–4) |
| Project specification | `saarch/models.py` | the five-input `ProjectIntake`; the derived `ProjectSpec` (entities, endpoints, exclusions, security requirements, testing criteria, assumptions, risks); the validated state machine; JSON persistence |
| Specification derivation | `saarch/spec_builder.py` | inputs → structured spec: entities from capabilities, endpoints from entities, exclusions from scope, mandatory guardrails always included |
| App generation | `saarch/generator.py` | renders a complete coherent project from one authoritative spec: imports, routes, schemas, migrations, tests, docs all derive from it |

The user supplies exactly five inputs. The data model, API shape, folder
structure, and test strategy are derived, and every assumption is recorded.

## 2. Agentic engine — planning, tool use, autonomous repair, refusal authority

The decision layer. The three enforced behaviors live here because they are
decisions, not code-gen.

| Concern | Module | What it actually does |
|---|---|---|
| Planning | `generator.plan_tasks` | dependency-ordered build plan (9 tasks) before any file is written |
| Tool use | `saarch/provider.py` | model-provider abstraction; LiteLLM integration point for entity enrichment; deterministic path always runs first, provider use is recorded |
| Autonomous repair | `verifier.repair` | strategy ladder (full regen → minimal regen), bounded by repair budget and stagnation detection |
| Refusal authority | `saarch/policy.py` | prohibited work → `REFUSED` (exit 3), non-overridable — no flag bypasses it |
| Constructive resistance | `saarch/challenge.py` | contradictory/underspecified input → `CHALLENGED` (exit 4); proceeds only with recorded acceptance, and guardrails win over conflicting capabilities |

## 3. Engineering engine — database schemas, migrations, backend, UI, testing

The artifacts the tool engineers. All emitted from templates that share the
spec as their single source of truth.

- **Database schema** — `app/storage.py` (generated): SQLite tables with
  primary keys, types, defaults, and indexes per resource.
- **Migrations** — versioned, ordered `MIGRATIONS` applied by `Database.migrate()`
  with a `schema_version` table; explicit over implicit schema mutation.
- **Backend** — `app/api.py` + `app/main.py`: JSON CRUD, input validation at
  the boundary (deny-by-default), consistent error responses, health check.
- **UI** — `app/ui.py`: served single page, no build step, no CDN.
- **Testing** — `tests/test_app.py`: real HTTP integration suite (CRUD,
  validation, 404/405, persistence across restart), stdlib unittest only.

## 4. Execution layer — sandboxing, real verification, bounded retries

Nothing is believed that was not executed.

- **Isolation** — every check runs in a subprocess with a hard timeout inside
  the generated project directory; the host workspace is never executed
  against. E2B slots into the same interface as an alternative backend.
- **Real verification** — `verifier.verify`: `compileall`, the generated
  project's own test suite, and a credential scan. Checks that cannot run are
  reported `SKIP`, never `PASS`.
- **Bounded retries** — the repair loop is capped by a repair budget AND by
  failure-signature stagnation: the same failure repeating across different
  strategies stops the loop early, writes a diagnosis, and proposes a better
  specification (`proposal.md`) instead of burning budget on repetition.

## 5. Delivery layer — GitHub integration, export, versioning, iterative edits

You end up owning portable artifacts, not a platform dependency.

- **GitHub integration** — `delivery.py`: full repository creation and commit
  delivery over the REST API with the standard library only; token read from
  `GITHUB_TOKEN` at call time, never persisted.
- **Export** — repository-ready zip (`export_zip`), always available.
- **Versioning** — `project.json` (full spec + state) and the append-only
  `audit.jsonl` trail record every stage, decision, and check.
- **Iterative edits** — the state machine is resumable: `READY` re-opens for
  re-verification, `BLOCKED` re-opens for repair, `CHALLENGED` resolves by
  acceptance. Change inputs, re-run the pipeline, compare audit trails.

## How the layers meet the acceptance test

`scripts/acceptance.py` exercises all five layers in one real run:
core builder (five inputs → spec → generation), agentic engine (plan, refusal
gate, challenge gate), engineering engine (the emitted app with schema,
migrations, backend, UI, tests), execution layer (serve over HTTP, inject a
defect, verification must detect it, repair must fix it within budget, prove
it serves again), and delivery layer (zip export). CI runs it on every push
and publishes the live report at
<https://1243353366.github.io/superagent-architect/>.
