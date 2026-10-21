"""Patch integrity (born from incident F-CI-ANCHOR-2026).

On 2026-10-21 a workflow patch failed to apply (anchor mismatch) but the
operator chain continued; a commit message claimed a CI step that did
not exist. The failure class: a failed patch that silently continues.

Hard rules enforced here:
- Abort if the expected anchor is missing or ambiguous — never
  silently continue.
- Postcondition verification: the resulting workflow is structurally
  parsed (real grammar validation, not a text search) and the required
  step must exist in the correct job, in the correct position.
- Claim-to-artifact consistency: stated changes are compared against
  actual repository state before anything is committed.
- No write happens unless every check passes; a failed patch leaves
  the file bit-identical.

The validator is a strict parser for the GitHub Actions subset this
repository uses (jobs at one indent level, steps as '- name:'/'- uses:'
items). Anything outside that grammar fails closed. The authoritative
end-to-end validation is GitHub running the workflow in CI; this module
guarantees the structure is correct BEFORE that.
"""
import hashlib
from pathlib import Path
from typing import List, Optional

__all__ = ["PatchAborted", "ClaimMismatch", "apply_patch_or_abort",
           "validate_workflow", "parse_workflow_structure",
           "patch_workflow", "claim_matches_state"]


class PatchAborted(RuntimeError):
    """The anchor is missing/ambiguous, or the patched workflow fails
    postcondition validation. Nothing was written."""


class ClaimMismatch(RuntimeError):
    """Stated changes do not match the actual artifacts. Do not commit."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def apply_patch_or_abort(src: str, anchor: str, replacement: str) -> str:
    """Apply a textual patch ONLY when the anchor occurs exactly once.
    Zero occurrences: abort. Two or more: abort (ambiguous). This is the
    loud, failing replacement for str.replace's silent no-op — the exact
    behavior that let the original incident through."""
    if not anchor:
        raise PatchAborted("empty anchor — refusing to patch")
    count = src.count(anchor)
    if count == 0:
        raise PatchAborted(
            "anchor not found — aborting rather than silently "
            "continuing (incident F-CI-ANCHOR-2026)")
    if count > 1:
        raise PatchAborted(f"anchor occurs {count} times — ambiguous, "
                           "aborting")
    return src.replace(anchor, replacement, 1)


def parse_workflow_structure(text: str) -> dict:
    """Strict structural parse of the GitHub Actions grammar used in
    this repository. Returns {"jobs": {job: [step labels in order]}}.
    Job and step structure is enforced; nested values (runs, matrices,
    heredoc blocks) are consumed as opaque content. Anything outside
    the grammar raises — never a best-effort guess."""
    jobs: dict = {}
    in_jobs = False
    current_job = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if "\t" in raw:
            raise PatchAborted(f"line {lineno}: tabs are not valid here")
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            if not (stripped.endswith(":") or ":" in stripped):
                raise PatchAborted(f"line {lineno}: expected top-level "
                                   f"'key:' or 'key: value', got "
                                   f"{stripped!r}")
            key = stripped.split(":", 1)[0].strip()
            in_jobs = (key == "jobs")
            current_job = None
            continue
        if not in_jobs and indent in (2, 4):
            continue                      # nested config (on:, etc.)
        if indent == 2:
            if not stripped.endswith(":"):
                raise PatchAborted(f"line {lineno}: expected a job "
                                   f"definition, got {stripped!r}")
            current_job = stripped[:-1]
            jobs[current_job] = []
            continue
        if current_job is None:
            raise PatchAborted(f"line {lineno}: job content before any "
                               "job was declared")
        if indent == 6 and stripped.startswith("- "):
            item = stripped[2:]
            if item.startswith("name:"):
                jobs[current_job].append(item[len("name:"):].strip())
            elif item.startswith("uses:"):
                jobs[current_job].append(item.strip())
            elif ":" in item:
                jobs[current_job].append(item.split(":", 1)[0] + ":…")
            else:
                raise PatchAborted(f"line {lineno}: unrecognized step "
                                   f"item {item[:40]!r} — failing closed")
            continue
        if indent >= 4:
            continue                      # job-level keys, run blocks,
                                           # matrices: opaque content
        raise PatchAborted(f"line {lineno}: indent {indent} outside the "
                           f"understood grammar: {stripped[:40]!r}")
    if not jobs:
        raise PatchAborted("no jobs found — refusing to claim this is "
                           "a valid workflow")
    return {"jobs": jobs}


def validate_workflow(text: str,
                      required_step: Optional[str] = None,
                      job_name: Optional[str] = None,
                      before_step: Optional[str] = None) -> dict:
    """Structurally validate the workflow and, when asked, assert the
    required step exists inside the named job, positioned before
    before_step. Presence in the file is not placement; placement is
    not validity."""
    doc = parse_workflow_structure(text)
    if required_step is None:
        return doc
    if job_name not in doc["jobs"]:
        raise PatchAborted(f"job {job_name!r} not present in {list(doc['jobs'])}")
    names = doc["jobs"][job_name]
    if required_step not in names:
        raise PatchAborted(
            f"required step {required_step!r} missing from job "
            f"{job_name!r} — postcondition failed, aborting")
    if before_step is not None:
        if before_step not in names:
            raise PatchAborted(f"reference step {before_step!r} missing")
        if names.index(required_step) > names.index(before_step):
            raise PatchAborted(
                f"step {required_step!r} must run BEFORE "
                f"{before_step!r} — wrong placement, aborting")
    return doc


def patch_workflow(path: Path, anchor: str, replacement: str,
                   required_step: str, job_name: str,
                   before_step: str) -> str:
    """The full safe path: read, abort-or-apply, structurally parse,
    assert the postcondition, and only then write. Any failure leaves
    the file bit-identical."""
    path = Path(path)
    before = path.read_text()
    patched = apply_patch_or_abort(before, anchor, replacement)
    validate_workflow(patched, required_step, job_name, before_step)
    path.write_text(patched)          # reached only if all checks pass
    return _sha(patched)


def claim_matches_state(claims: List[dict]) -> str:
    """Compare stated changes against the ACTUAL repository state before
    committing. Each claim: {"file", "must_contain": [snippets]}.
    Any mismatch: ClaimMismatch — the commit must not proceed. This is
    the check that would have caught the original incident: the commit
    message claimed a workflow step that was not in the repository."""
    problems = []
    for claim in claims:
        path = Path(claim["file"])
        if not path.is_file():
            problems.append(f"{claim['file']}: file missing")
            continue
        content = path.read_text()
        for snippet in claim.get("must_contain", []):
            if snippet not in content:
                problems.append(
                    f"{claim['file']}: missing {snippet[:60]!r}")
    if problems:
        raise ClaimMismatch(
            "claim-to-artifact mismatch — stated changes are not in the "
            f"repository: {problems}")
    return "claims verified against artifacts"
