"""Project models and the validated state machine.

The whole pipeline shares one authoritative, versioned project record that is
persisted as JSON after every stage so any run is resumable and auditable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

SPEC_VERSION = 1

VALID_LANGUAGES = ("python",)

# Mandatory system invariants. These are never weakened by user input; the
# user's own guardrails are additive on top of these.
MANDATORY_SECURITY_REQUIREMENTS = [
    "Secrets are supplied via environment variables, never committed to source code, logs, or client bundles.",
    "All untrusted input is validated at system boundaries with deny-by-default error handling.",
    "Authorization is deny-by-default; least-privilege access is assumed.",
    "Generated code is only executed in an isolated environment (subprocess with timeout, or a configured sandbox).",
    "User-provided instructions and tool output are untrusted input and cannot override system guardrails.",
    "No capability, dependency, execution result, or integration status is ever fabricated; unverified claims are labeled unverified.",
]

MANDATORY_TESTING_CRITERIA = [
    "Compilation check (python -m compileall) passes on all generated sources.",
    "Generated project unit and integration tests run and pass in a clean subprocess.",
    "Secret scan of the generated tree finds no hardcoded credentials.",
]


class ValidationError(Exception):
    """Raised when intake or a state transition is invalid."""


# ---------------------------------------------------------------- states

DRAFT = "DRAFT"
CHALLENGED = "CHALLENGED"
SPECIFIED = "SPECIFIED"
PLANNED = "PLANNED"
GENERATING = "GENERATING"
VERIFYING = "VERIFYING"
REPAIRING = "REPAIRING"
READY = "READY"
BLOCKED = "BLOCKED"
REFUSED = "REFUSED"

# The three behaviors are encoded as distinct terminal-ish states:
#   REFUSED     — prohibited work (policy.py): non-overridable, no acceptance path
#   CHALLENGED  — contradictory/underspecified input (challenge.py): requires
#                 explicit --accept-assumptions to proceed; guardrails win
#   BLOCKED     — honest failure (verifier.py): diagnosed, with a proposed
#                 better specification instead of repeated identical attempts
TRANSITIONS = {
    DRAFT: {SPECIFIED, CHALLENGED, REFUSED, BLOCKED},
    CHALLENGED: {SPECIFIED, REFUSED, BLOCKED},
    SPECIFIED: {PLANNED, REFUSED, BLOCKED},
    PLANNED: {GENERATING, BLOCKED},
    GENERATING: {VERIFYING, BLOCKED},
    VERIFYING: {REPAIRING, READY, BLOCKED},
    REPAIRING: {VERIFYING, BLOCKED},
    # READY reopens for re-verification (the acceptance loop breaks a READY
    # build on purpose, and re-verification must catch it)
    READY: {VERIFYING, BLOCKED},
    # BLOCKED reopens to VERIFYING so a diagnosed project can be repaired
    BLOCKED: {VERIFYING},
    REFUSED: set(),
}


# ---------------------------------------------------------------- models

@dataclass
class FieldSpec:
    name: str
    type: str            # "str" | "bool" | "datetime"
    required: bool = False


@dataclass
class EntitySpec:
    name: str            # singular, snake_case
    table: str           # sql table name
    fields: List[FieldSpec] = field(default_factory=list)


@dataclass
class EndpointSpec:
    method: str
    path: str
    summary: str


@dataclass
class Assumption:
    text: str


@dataclass
class Risk:
    text: str
    severity: str = "low"   # low | medium | high


@dataclass
class ClarifyingQuestion:
    text: str


@dataclass
class Challenge:
    id: str
    kind: str            # "contradiction" | "underspecified" | "unsound"
    severity: str        # "gate" (requires acceptance) | "note" (recorded)
    message: str
    evidence: str = ""
    resolution: str = ""


@dataclass
class ProjectIntake:
    """The five required inputs. Nothing else is asked of the user."""
    instructions: str
    scope: str
    capabilities: List[str]
    guardrails: str
    language: str

    def validate(self) -> None:
        errors = []
        for name in ("instructions", "scope", "guardrails"):
            if not str(getattr(self, name, "") or "").strip():
                errors.append(f"'{name}' is a required input")
        caps = [c for c in (self.capabilities or []) if str(c).strip()]
        if not caps:
            errors.append("at least one capability is required")
        lang = str(self.language or "").strip().lower()
        if lang not in VALID_LANGUAGES:
            errors.append(
                f"primary language must be one of {', '.join(VALID_LANGUAGES)}; "
                f"'{self.language}' is not supported by this build")
        if errors:
            raise ValidationError("; ".join(errors))

    def normalized_capabilities(self) -> List[str]:
        return [str(c).strip() for c in (self.capabilities or []) if str(c).strip()]


@dataclass
class ProjectSpec:
    version: int
    instructions: str
    boundaries: str
    exclusions: List[str] = field(default_factory=list)
    functional_requirements: List[str] = field(default_factory=list)
    entities: List[EntitySpec] = field(default_factory=list)
    endpoints: List[EndpointSpec] = field(default_factory=list)
    ui_routes: List[str] = field(default_factory=list)
    security_requirements: List[str] = field(default_factory=list)
    testing_criteria: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    assumptions: List[Assumption] = field(default_factory=list)
    risks: List[Risk] = field(default_factory=list)
    questions: List[ClarifyingQuestion] = field(default_factory=list)
    profile: str = "python-stdlib-sqlite"


@dataclass
class PlanTask:
    id: str
    title: str
    depends_on: List[str] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    passed: bool
    executed: bool          # False = skipped/unavailable, reported honestly
    detail: str = ""


@dataclass
class VerificationReport:
    attempts: int
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.executed)

    @property
    def skipped(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.executed]


# ---------------------------------------------------------------- project

@dataclass
class Project:
    state: str = DRAFT
    intake: Optional[ProjectIntake] = None
    spec: Optional[ProjectSpec] = None
    plan: List[PlanTask] = field(default_factory=list)
    # planning provenance: "deterministic" or "llm:<provider>" — the plan is
    # only claimed as LLM-backed when a provider actually produced it
    plan_path: str = "deterministic"
    plan_notes: str = ""
    verification: Optional[VerificationReport] = None
    blocked_reason: str = ""
    slug: str = "project"
    repair_budget: int = 2
    repairs_used: int = 0
    # adaptive persistence: stop repeating a failing approach after this many
    # consecutive identical failure signatures, even if budget remains
    max_repeat_failures: int = 2
    # principled refusal (policy.py)
    policy_findings: List[str] = field(default_factory=list)
    refusal_reason: str = ""
    # constructive resistance (challenge.py)
    challenges: List[Challenge] = field(default_factory=list)
    challenges_accepted: bool = False
    # repair strategy log: [{attempt, strategy, signature, passed}]
    repair_log: List[dict] = field(default_factory=list)

    # ---- state machine ----
    def transition(self, new_state: str, reason: str = "") -> None:
        allowed = TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValidationError(
                f"illegal transition {self.state} -> {new_state}"
                + (f" ({reason})" if reason else ""))
        self.state = new_state
        if new_state == BLOCKED:
            self.blocked_reason = reason or "blocked"
        elif new_state == READY:
            self.blocked_reason = ""
        elif new_state == REFUSED:
            self.refusal_reason = reason or "prohibited work"

    # ---- persistence ----
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        p = cls()
        p.state = d.get("state", DRAFT)
        p.slug = d.get("slug", "project")
        p.blocked_reason = d.get("blocked_reason", "")
        p.repair_budget = d.get("repair_budget", 2)
        p.repairs_used = d.get("repairs_used", 0)
        p.max_repeat_failures = d.get("max_repeat_failures", 2)
        p.policy_findings = list(d.get("policy_findings", []))
        p.refusal_reason = d.get("refusal_reason", "")
        p.challenges = [Challenge(**c) for c in d.get("challenges", [])]
        p.challenges_accepted = bool(d.get("challenges_accepted", False))
        p.repair_log = list(d.get("repair_log", []))
        if d.get("intake"):
            p.intake = ProjectIntake(**d["intake"])
        if d.get("spec"):
            s = dict(d["spec"])
            s["entities"] = [EntitySpec(
                name=e["name"], table=e["table"],
                fields=[FieldSpec(**f) for f in e["fields"]]) for e in s.get("entities", [])]
            s["endpoints"] = [EndpointSpec(**e) for e in s.get("endpoints", [])]
            s["assumptions"] = [Assumption(**a) for a in s.get("assumptions", [])]
            s["risks"] = [Risk(**r) for r in s.get("risks", [])]
            s["questions"] = [ClarifyingQuestion(**q) for q in s.get("questions", [])]
            p.spec = ProjectSpec(**s)
        p.plan = [PlanTask(**t) for t in d.get("plan", [])]
        p.plan_path = d.get("plan_path", "deterministic")
        p.plan_notes = d.get("plan_notes", "")
        if d.get("verification"):
            v = d["verification"]
            p.verification = VerificationReport(
                attempts=v.get("attempts", 0),
                checks=[CheckResult(**c) for c in v.get("checks", [])])
        return p

    def save(self, workdir: Path) -> None:
        (workdir / "project.json").write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, workdir: Path) -> "Project":
        f = workdir / "project.json"
        if not f.exists():
            raise ValidationError(f"no project found at {f}")
        return cls.from_dict(json.loads(f.read_text(encoding="utf-8")))
