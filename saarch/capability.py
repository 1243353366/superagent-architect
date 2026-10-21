"""Capability envelopes (schema v1.0): what an operation is responsible
for, what it is technically capable of, and what it is authorized to do
right now are three different things, and only the third is enforceable.

An envelope has:
- responsible_for: the duty, descriptive — the role. Never a permission.
- capabilities: the technical CEILING under this envelope: which paths
  may be read, which may be written, which exact commands may run, a
  wall-clock budget, and network: false (no egress, ever).
- grants: the subset authorized RIGHT NOW. An envelope with no grants
  can do nothing. Grants are issued only by a human ApprovalAuthority.

The EnvelopeEnforcer sits in the execution layer: every read, write,
exec, and budget tick passes through it, and every decision (allow,
deny, request, approve, revoke) is audit-logged on the envelope.

The operation can request an expansion. It cannot grant one. The
approve/revoke methods live on ApprovalAuthority, an object the
operation context never holds — the same structural separation the
trusted core already uses against the engine.
"""
import fnmatch
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

CAPABILITY_SCHEMA_VERSION = "1.0"


class EnvelopeError(ValueError):
    """Malformed envelope, request, or approval — fail closed."""


class EnvelopeViolation(RuntimeError):
    """An operation stepped outside its authorized envelope. Denied and
    audit-logged, never silently coerced."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("/")


class CapabilityEnvelope:
    def __init__(self, envelope_id: str, operation: str,
                 responsible_for: str, capabilities: Dict[str, object]):
        if not envelope_id or not operation or not responsible_for:
            raise EnvelopeError("envelope_id, operation, and "
                                "responsible_for are required")
        caps = capabilities or {}
        for key in caps:
            if key not in ("read_paths", "write_paths", "commands",
                           "budget_seconds", "network"):
                raise EnvelopeError(f"unknown capability {key!r} — fail "
                                    "closed")
        if caps.get("network"):
            raise EnvelopeError("network egress is not grantable; "
                                "envelopes are always default-deny")
        for key in ("read_paths", "write_paths", "commands"):
            if key in caps and not isinstance(caps[key], list):
                raise EnvelopeError(f"{key} must be a list of patterns")
        budget = caps.get("budget_seconds", 0)
        if not isinstance(budget, (int, float)) or budget <= 0:
            raise EnvelopeError("budget_seconds must be a positive "
                                "number — compute is never free")
        self.envelope_id = envelope_id
        self.operation = operation
        self.responsible_for = responsible_for
        self.capabilities = caps
        self.granted: List[str] = []
        self.granted_by: Optional[str] = None
        self.granted_at: Optional[str] = None
        self.budget_spent = 0.0
        self.audit: List[Dict[str, object]] = []
        self._requests: Dict[str, Dict[str, object]] = {}
        self._note("envelope_created", ceiling=sorted(caps.keys()))

    # ------------------------------------------------------------- audit
    def _note(self, event: str, **fields) -> None:
        self.audit.append({"event": event, "at": _now(), **fields})

    def to_record(self) -> dict:
        return {"envelope_id": self.envelope_id,
                "schema_version": CAPABILITY_SCHEMA_VERSION,
                "operation": self.operation,
                "responsible_for": self.responsible_for,
                "capabilities": self.capabilities,
                "granted": list(self.granted),
                "granted_by": self.granted_by,
                "granted_at": self.granted_at,
                "budget_spent": round(self.budget_spent, 3),
                "expansion_requests": dict(self._requests),
                "audit": list(self.audit)}

    # ----------------------------------------------------- pattern checks
    def _ceiling_allows(self, kind: str, target: str) -> bool:
        patterns = self.capabilities.get(kind, [])
        if kind == "commands":
            return any(list(c) == list(target) for c in patterns)
        return any(fnmatch.fnmatch(_norm(target), _norm(p))
                   for p in patterns)

    def _grant_allows(self, kind: str, target: str) -> bool:
        """Two independent checks: the human granted this capability
        group (authorization right now), AND the target is inside the
        group's ceiling (technical limit). Neither alone suffices."""
        if not self.granted:
            return False                    # no human grant: nothing runs
        if kind not in self.granted:
            return False                    # group not activated
        return self._ceiling_allows(kind, target)


class ApprovalAuthority:
    """The human channel. Operations never receive this object. It
    holds registered human approvers; engine/model identifiers are
    refused by construction."""

    def __init__(self):
        self._humans: List[str] = []
        self._note = None

    def register_human(self, human_id: str) -> None:
        if not human_id or not human_id.startswith("human:"):
            raise EnvelopeError("approver ids must look like 'human:<id>'")
        self._humans.append(human_id)

    def grant(self, envelope: CapabilityEnvelope,
              capabilities: List[str], human_id: str) -> None:
        self._require_human(human_id)
        for cap in capabilities:
            if cap not in envelope.capabilities:
                raise EnvelopeError(
                    f"cannot grant {cap!r}: it is not within this "
                    "envelope's ceiling — a grant may expand authority "
                    "only up to the ceiling, never past it")
        envelope.granted = sorted(set(capabilities))
        envelope.granted_by = human_id
        envelope.granted_at = _now()
        envelope._note("grant", by=human_id,
                       granted=list(envelope.granted))

    def revoke(self, envelope: CapabilityEnvelope, human_id: str) -> None:
        self._require_human(human_id)
        envelope.granted = []
        envelope.granted_by = None
        envelope.granted_at = None
        envelope._note("revoke", by=human_id)

    def approve_expansion(self, envelope: CapabilityEnvelope,
                          request_id: str, human_id: str) -> None:
        """The ONLY way a pending expansion becomes live authority."""
        self._require_human(human_id)
        req = envelope._requests.get(request_id)
        if req is None:
            raise EnvelopeError(f"unknown expansion request {request_id!r}")
        if req["status"] != "pending":
            raise EnvelopeError(f"request {request_id} is "
                                f"{req['status']!r}, not pending")
        for cap in req["requested"]:
            if cap not in envelope.capabilities:
                raise EnvelopeError(
                    f"requested {cap!r} exceeds the ceiling — denied "
                    "even by a human without a new envelope")
        req["status"] = "approved"
        req["approved_by"] = human_id
        req["approved_at"] = _now()
        envelope.granted = sorted(set(envelope.granted) |
                                  set(req["requested"]))
        envelope.granted_by = human_id
        envelope.granted_at = req["approved_at"]
        envelope._note("expansion_approved", request=request_id,
                       by=human_id, requested=req["requested"],
                       granted_now=list(envelope.granted))

    def _require_human(self, human_id: str) -> None:
        if human_id in self._humans:
            return
        raise EnvelopeError(
            f"{human_id!r} is not a registered human approver — the "
            "model cannot grant its own expansion, and neither can "
            "anything that calls itself human")


class EnvelopeEnforcer:
    """The execution-layer choke point. An operation must go through the
    enforcer for every file read, file write, command execution, and
    budget tick."""

    def __init__(self, envelope: CapabilityEnvelope):
        self.envelope = envelope

    # -------------------------------------------------------- operations
    def read(self, path: str) -> bytes:
        from pathlib import Path
        target = _norm(path)
        if not self.envelope._grant_allows("read_paths", target):
            self._deny("read", target)
        data = Path(target).read_bytes()
        self._tick()
        return data

    def write(self, path: str, data: bytes) -> None:
        from pathlib import Path
        target = _norm(path)
        if not self.envelope._grant_allows("write_paths", target):
            self._deny("write", target)
        Path(target).write_bytes(data)
        self._tick()

    def exec(self, command: List[str], timeout: Optional[int] = None):
        import subprocess
        cmd = [str(c) for c in command]
        if not self.envelope._grant_allows("commands", cmd):
            self._deny("exec", " ".join(cmd))
        remaining = self.envelope.capabilities["budget_seconds"] - \
            self.envelope.budget_spent
        if remaining <= 0:
            self._deny("budget", "exhausted")
        limit = timeout if timeout is not None else int(remaining) + 1
        started = time.monotonic()
        proc = subprocess.run(cmd, timeout=limit, capture_output=True,
                              text=True, env={"PATH": "/usr/bin:/bin"})
        self.envelope.budget_spent += time.monotonic() - started
        self.envelope._note("exec_allowed", command=cmd,
                            exit_code=proc.returncode)
        return proc

    def _tick(self) -> None:
        self.envelope.budget_spent += 0.001
        if self.envelope.budget_spent > \
                self.envelope.capabilities["budget_seconds"]:
            self._deny("budget", "exhausted")

    def _deny(self, kind: str, detail: str) -> None:
        self.envelope._note("deny", kind=kind, target=detail)
        raise EnvelopeViolation(
            f"outside envelope: {kind} {detail!r} — responsible_for="
            f"{self.envelope.responsible_for!r} does not confer "
            "authority; request an expansion through the human channel")

    # ------------------------------------------------------- expansion
    def request_expansion(self, requested: List[str],
                          reason: str) -> str:
        if not requested or not reason:
            raise EnvelopeError("an expansion needs capabilities and a "
                                "reason")
        for cap in requested:
            if cap not in self.envelope.capabilities:
                # still recorded: humans may decide the ceiling itself
                # was too small and issue a new envelope
                self.envelope._note("expansion_exceeds_ceiling",
                                    capability=cap)
        request_id = f"req-{len(self.envelope._requests) + 1}"
        self.envelope._requests[request_id] = {
            "requested": list(requested), "reason": reason,
            "status": "pending", "requested_at": _now(),
            "requested_by": self.envelope.operation}
        self.envelope._note("expansion_requested", request=request_id,
                            requested=list(requested))
        return request_id
