# Verification report — demo run (personal task tracker)

Date: 2026-10-20 · Environment: Python 3.11.17, Linux · Runner: local subprocess isolation

Every item below was actually executed. Skipped checks would be listed as
`[SKIP]` with the reason; there were none in this run.

## Checks executed by `saarch verify`

| Check | Result | Evidence |
|---|---|---|
| `compileall` | PASS | `python -m compileall -q app` exited 0 in the generated project directory |
| `generated-project-tests` | PASS | the generated project's own unittest suite ran in a clean subprocess: **7 tests, OK** (output below) |
| `secret-scan` | PASS | pattern scan of the generated tree found no hardcoded credentials |

## Generated project test output (executed)

```
test_crud_roundtrip (test_app.EndToEndTests.test_crud_roundtrip) ... ok
test_health (test_app.EndToEndTests.test_health) ... ok
test_method_not_allowed (test_app.EndToEndTests.test_method_not_allowed) ... ok
test_persistence_across_restart (test_app.EndToEndTests.test_persistence_across_restart) ... ok
test_ui_page_served (test_app.EndToEndTests.test_ui_page_served) ... ok
test_unknown_resource_and_route (test_app.EndToEndTests.test_unknown_resource_and_route) ... ok
test_validation_rejects_bad_payloads (test_app.EndToEndTests.test_validation_rejects_bad_payloads) ... ok

----------------------------------------------------------------------
Ran 7 tests in 1.025s

OK
```

The suite starts a real HTTP server on an ephemeral port and exercises CRUD
over HTTP, input validation, 404/405 handling, the served UI, and persistence
across a simulated server restart.

## What was NOT executed in this run

Stated plainly, per the honesty policy:

- No LLM provider was configured; entity/field derivation was deterministic
  (recorded in the audit trail as `provider: deterministic derivation`).
- No linting or type-checking tool ran (not installed in the runner); these
  checks would be reported as skipped, not passed.
- The generated application was not deployed anywhere; GitHub delivery pushed
  source only.
