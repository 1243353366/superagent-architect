"""Human-governed project lifecycle (schema v1.0).

Human intent defines the boundaries. Agents execute within them.
Independent controls enforce them. Evidence decides the release.

A Project moves through a validated state machine:
    draft -> clarified -> building -> built -> verified -> approved
              -> released        (+ blocked / rejected from any live state)

Guards are structural, not procedural:
- Building requires a human-granted capability envelope.
- 'verified' requires an evidence bundle produced by the independent
  Verifier; the builder cannot forge it because the verifier hashes the
  canonical evidence and the release gate recomputes it.
- 'approved'/'released' require a scoped human approval from the
  HumanGovernor, an object untrusted code never holds, tied to the
  contract version AND the exact evidence hash. A material contract
  change bumps the version and voids every approval. A tampered evidence
  bundle changes its hash and voids approvals the same way.

Untrusted inputs (intent text, generated code, tool output) are data.
An injected instruction in the objective is executed by nothing.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

GOVERNANCE_SCHEMA_VERSION = "1.0"

STATES = ("draft", "clarified", "building", "built", "verified",
          "approved", "released", "blocked", "rejected")
LIVE = ("draft", "clarified", "building", "built", "verified", "approved")
APPROVAL_SCOPES = ("release", "deploy", "permission_expansion",
                   "destructive_operation", "sensitive_access")


class GovernanceError(ValueError):
    """Malformed contract, illegal transition, or invalid evidence."""


class ReleaseBlocked(RuntimeError):
    """The release gate refused. This is the safe outcome."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def evidence_hash(evidence: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(evidence).encode()).hexdigest()


class Contract:
    """A versioned statement of human intent. Explicit instructions and
    AI assumptions never mix: anything not stated by the human must be
    listed as an assumption with an open question, and building cannot
    start while questions remain."""

    def __init__(self, contract_id: str, objective: str, must_do: List[str],
                 must_not_do: List[str], may_access: List[str],
                 safety_constraints: List[str],
                 acceptance_criteria: List[str],
                 approval_required: List[str], assumptions: List[str],
                 open_questions: List[str], version: int = 1,
                 created_by: str = "human"):
        for name, val in [("contract_id", contract_id), ("objective", objective)]:
            if not val or not str(val).strip():
                raise GovernanceError(f"{name} is required")
        for name, val in [("must_do", must_do),
                          ("acceptance_criteria", acceptance_criteria),
                          ("safety_constraints", safety_constraints)]:
            if not val:
                raise GovernanceError(f"{name} must be a non-empty list — "
                                      "an unbounded build is refused")
        if created_by != "human":
            raise GovernanceError("contracts originate from humans only")
        if "network" in may_access:
            raise GovernanceError("network access is not grantable in a "
                                  "contract (deny-by-default)")
        self.contract_id = contract_id
        self.version = version
        self.created_by = created_by
        self.created_at = _now()
        self.objective = objective          # untrusted data, never instructions
        self.must_do = list(must_do)
        self.must_not_do = list(must_not_do)
        self.may_access = list(may_access)
        self.safety_constraints = list(safety_constraints)
        self.acceptance_criteria = list(acceptance_criteria)
        self.approval_required = list(approval_required)
        self.assumptions = list(assumptions)
        self.open_questions = list(open_questions)

    def to_record(self) -> dict:
        return {k: getattr(self, k) for k in
                ("contract_id", "version", "created_by", "created_at",
                 "objective", "must_do", "must_not_do", "may_access",
                 "safety_constraints", "acceptance_criteria",
                 "approval_required", "assumptions", "open_questions")}


class Project:
    """The validated state machine. Transitions happen only through the
    guarded methods; each guard demands an artifact the previous stage
    produced (a grant, an evidence bundle, a scoped approval)."""

    def __init__(self, contract: Contract):
        self.contract = contract
        self.state = "draft"
        self.envelope_granted_by: Optional[str] = None
        self.evidence: Optional[Dict[str, Any]] = None
        self.audit: List[Dict[str, Any]] = []

    def _note(self, event: str, **fields) -> None:
        self.audit.append({"event": event, "at": _now(),
                           "state": self.state, **fields})

    def _require(self, state: str) -> None:
        if self.state != state:
            raise GovernanceError(
                f"project is {self.state!r}, not {state!r} — stages cannot "
                "be skipped or reordered")

    def clarify(self) -> None:
        """Building cannot begin while human questions are open; ambiguity
        is never silently interpreted as permission."""
        self._require("draft")
        if self.contract.open_questions:
            raise GovernanceError(
                "open questions remain; ambiguity is not permission — "
                f"unresolved: {self.contract.open_questions}")
        self.state = "clarified"
        self._note("clarified")

    def start_build(self, granted_by: str) -> None:
        """Requires a human-granted envelope; the grantee id must be a
        registered human, checked by the caller (the HumanGovernor)."""
        self._require("clarified")
        if not granted_by or not granted_by.startswith("human:"):
            raise GovernanceError("building requires a HUMAN-granted "
                                  f"envelope; {granted_by!r} is not one")
        self.envelope_granted_by = granted_by
        self.state = "building"
        self._note("build_started", granted_by=granted_by)

    def record_build(self) -> None:
        self._require("building")
        self.state = "built"
        self._note("build_recorded")

    def record_verification(self, evidence: Dict[str, Any]) -> None:
        """Only the independent verifier's evidence moves the project to
        'verified'. The evidence carries its own hash; the release gate
        recomputes it, so post-hoc tampering voids approvals."""
        self._require("built")
        for key in ("requirement_tests", "adversarial", "revision"):
            if key not in evidence:
                raise GovernanceError(f"evidence missing {key!r}")
        if not evidence["requirement_tests"].get("passed"):
            raise GovernanceError("requirements not satisfied — the "
                                  "builder's claim of done is not evidence")
        if not evidence["adversarial"].get("all_blocked"):
            raise GovernanceError("an adversarial attempt was NOT blocked "
                                  "— verification fails closed")
        self.evidence = evidence
        self.state = "verified"
        self._note("verified", evidence_hash=evidence_hash(evidence))

    def record_approval(self, approval: Dict[str, Any]) -> None:
        self._require("verified")
        self._check_approval(approval)
        self.state = "approved"
        self._note("approved", by=approval["by"], scope=approval["scope"])

    def _check_approval(self, approval: Dict[str, Any]) -> None:
        if self.evidence is None:
            raise GovernanceError("nothing to approve: no evidence")
        if approval.get("contract_id") != self.contract.contract_id or \
                approval.get("contract_version") != self.contract.version:
            raise GovernanceError(
                "approval is for a different contract version — material "
                "changes void prior approvals")
        if approval.get("evidence_hash") != evidence_hash(self.evidence):
            raise GovernanceError(
                "approval references different evidence — stale or tampered")
        if approval.get("scope") != "release":
            raise GovernanceError(
                f"scope {approval.get('scope')!r} does not authorize "
                "release; approvals are narrow, not blanket")

    def record_release(self, revision: str) -> dict:
        self._require("approved")
        if revision != self.evidence.get("revision"):
            raise GovernanceError("release revision must match the "
                                  "verified evidence revision exactly")
        self.state = "released"
        self._note("released", revision=revision)
        return {"state": "released", "contract_version":
                self.contract.version, "evidence_hash":
                evidence_hash(self.evidence), "revision": revision}

    def block(self, reason: str) -> None:
        if self.state not in LIVE:
            raise GovernanceError(f"cannot block from {self.state!r}")
        self._note("blocked", reason=reason)
        self.state = "blocked"


class Verifier:
    """Independent of the builder: it runs the requirement tests and the
    adversarial suite itself and produces the evidence bundle. The
    builder cannot call it with forged results — it accepts outcomes of
    ITS OWN runs, not claims."""

    def __init__(self, repo_root):
        import subprocess
        from saarch.execution import _scrubbed_env
        self._subprocess = subprocess
        self._scrub = _scrubbed_env

    def run(self, command: List[str], cwd) -> Dict[str, Any]:
        proc = self._subprocess.run([str(c) for c in command], cwd=cwd,
                                    env=self._scrub(), timeout=120,
                                    capture_output=True, text=True)
        return {"command": " ".join(str(c) for c in command),
                "exit_code": proc.returncode,
                "passed": proc.returncode == 0,
                "stdout_tail": proc.stdout[-300:]}

    def build_evidence(self, requirement_run: Dict[str, Any],
                      adversarial_results: List[Dict[str, Any]],
                      regression_run: Dict[str, Any],
                      revision: str) -> Dict[str, Any]:
        all_blocked = all(r.get("blocked") for r in adversarial_results)
        passed = requirement_run["passed"] and regression_run["passed"]
        return {"schema_version": GOVERNANCE_SCHEMA_VERSION,
                "contract_version": None,     # set by the caller
                "requirement_tests": requirement_run,
                "regressions": regression_run,
                "adversarial": {"attempts": adversarial_results,
                                "all_blocked": all_blocked},
                "requirements_satisfied": passed and all_blocked,
                "revision": revision, "generated_at": _now()}


class HumanGovernor:
    """The human authority for consequential decisions. Scoped approvals
    only: a release approval never authorizes deployment or anything
    else. Untrusted code never receives this object."""

    def __init__(self):
        self._humans: List[str] = []
        self.approvals: List[Dict[str, Any]] = []

    def register_human(self, human_id: str) -> None:
        if not human_id.startswith("human:"):
            raise GovernanceError("human ids look like 'human:<id>'")
        self._humans.append(human_id)

    def approve(self, contract_id: str, contract_version: int,
                scope: str, evidence_hash_str: str,
                human_id: str, rationale: str) -> Dict[str, Any]:
        if human_id not in self._humans:
            raise GovernanceError(f"{human_id!r} is not a registered "
                                  "human — an agent cannot approve")
        if scope not in APPROVAL_SCOPES:
            raise GovernanceError(f"unknown approval scope {scope!r}")
        approval = {"contract_id": contract_id,
                    "contract_version": contract_version,
                    "scope": scope, "evidence_hash": evidence_hash_str,
                    "by": human_id, "rationale": rationale,
                    "at": _now()}
        self.approvals.append(approval)
        return approval

    def approvals_for(self, contract_id: str, scope: str) -> List[dict]:
        return [a for a in self.approvals
                if a["contract_id"] == contract_id and a["scope"] == scope]


class ReleaseGate:
    """The last independent control. It recomputes the evidence hash,
    checks the scoped approval, and matches the revision. Anything
    missing: ReleaseBlocked. No silent fallback, no partial release."""

    def attempt(self, project: Project, governor: HumanGovernor,
                revision: str) -> dict:
        if project.state != "approved":
            raise ReleaseBlocked(f"project is {project.state!r}, not "
                                 "approved — release refused")
        if project.evidence is None:
            raise ReleaseBlocked("no verified evidence")
        if not project.evidence.get("requirements_satisfied"):
            raise ReleaseBlocked("requirements not satisfied")
        if not project.evidence["adversarial"]["all_blocked"]:
            raise ReleaseBlocked("an adversarial attempt was not blocked")
        match = [a for a in governor.approvals_for(
                    project.contract.contract_id, "release")
                 if a["evidence_hash"] == evidence_hash(project.evidence)
                 and a["contract_version"] == project.contract.version]
        if not match:
            raise ReleaseBlocked(
                "no valid release approval for this contract version and "
                "this exact evidence — stale or missing approval")
        return project.record_release(revision)
