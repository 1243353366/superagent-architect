"""Failure-to-Capability Engine (F2C schema v1.0).

Lifecycle: failure -> reproduction -> classification -> candidate ->
validation -> promotion or rejection.

Design rules, enforced by code:
- One canonical, explicitly versioned schema per record kind; validation
  at ingestion and retrieval; incompatible versions fail closed.
- Observed facts stay separate from inferred causes and hypotheses.
- Original evidence is immutable; minimized reproductions are separate,
  linked artifacts with the transformation recorded.
- A failure may only be retired behind a real, executable regression test.
- Candidates validate inside an isolated harness and can never mark
  themselves validated: the harness owns the verdict.
- Promotion is explicit, auditable, and reversible; rejection keeps the
  candidate auditable.
- Records and stored payloads are untrusted data. Nothing in this module
  executes instructions taken from record contents.
"""
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

F2C_SCHEMA_VERSION = "1.0"
GENESIS = "0" * 64

# Backward-compatible vocabulary extension of schema 1.0 (2026-10-21):
# records with the new value validate; existing stores load unchanged.
FAILURE_CLASSES = ("answer_parsing", "authorization", "state_integrity",
                   "data_loss", "schema_drift", "regression",
                   "silent_failure", "unknown")
SEVERITY_SCALE = {
    "S1": "containment breach: engine failure can reach trusted authority",
    "S2": "wrong decision or certification from correct-looking evidence",
    "S3": "degraded output, recoverable without authority loss",
}
FAILURE_STATUSES = ("observed", "reproduced", "classified", "candidate_created",
                    "validated", "rejected", "retired")
FAILURE_TRANSITIONS = {
    "observed": ("reproduced", "rejected"),
    "reproduced": ("classified", "rejected"),
    "classified": ("candidate_created", "rejected"),
    "candidate_created": ("validated", "rejected"),
    "validated": ("retired", "rejected"),
    "rejected": (),
    "retired": (),
}
CAPABILITY_TYPES = ("validation_rule", "code_fix", "strategy",
                    "regression_test", "retrieval_entry", "training_example")
CAPABILITY_STATUSES = ("proposed", "under_test", "validated", "rejected",
                       "deprecated", "retired")
CAPABILITY_TRANSITIONS = {
    "proposed": ("under_test", "rejected"),
    "under_test": ("validated", "rejected"),
    "validated": ("deprecated", "retired"),
    "rejected": (),
    "deprecated": (),
    "retired": (),
}


class F2CError(ValueError):
    """Fail-closed: malformed, incompatible, or forbidden records."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc(timestamp: str) -> datetime:
    if timestamp.endswith("Z"):        # Python <3.11 rejects the Z
        timestamp = timestamp[:-1] + "+00:00"   # suffix; normalize it
    dt = datetime.fromisoformat(timestamp)
    if dt.tzinfo is None:
        raise F2CError("timestamps must carry an explicit timezone")
    return dt


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(record: Dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------- validation

FAILURE_REQUIRED = ("failure_id", "schema_version", "created_at", "failure_class",
                    "severity", "status", "expected_behavior", "observed_behavior",
                    "fixture_ref", "environment_fingerprint", "root_cause",
                    "provenance")
CAPABILITY_REQUIRED = ("capability_id", "schema_version", "created_at", "type",
                       "status", "description", "originating_failures",
                       "preconditions", "permissions", "limitations",
                       "provenance",
                       "failure_class_for_retrieval")


def _check(record: Dict[str, Any], required: tuple, allowed: tuple,
           kind: str) -> None:
    if not isinstance(record, dict):
        raise F2CError(f"{kind} must be a mapping")
    missing = [k for k in required if k not in record]
    if missing:
        raise F2CError(f"{kind} missing required fields: {missing}")
    extra = [k for k in record if k not in allowed]
    if extra:
        raise F2CError(f"{kind} has unknown fields (fail closed): {extra}")
    if record.get("schema_version") != F2C_SCHEMA_VERSION:
        raise F2CError(
            f"{kind} schema_version {record.get('schema_version')!r} is not "
            f"supported by this engine ({F2C_SCHEMA_VERSION}); refusing to "
            "coerce or migrate silently")
    _utc(record["created_at"])


def validate_failure(record: Dict[str, Any]) -> Dict[str, Any]:
    allowed = FAILURE_REQUIRED + ("candidate_refs", "regression_test_refs",
                                  "disposition")
    _check(record, FAILURE_REQUIRED, allowed, "failure record")
    if record["failure_class"] not in FAILURE_CLASSES:
        raise F2CError(f"failure_class {record['failure_class']!r} not in "
                       f"controlled vocabulary {FAILURE_CLASSES}")
    if record["severity"] not in SEVERITY_SCALE:
        raise F2CError(f"severity {record['severity']!r} not on the "
                       f"documented scale {sorted(SEVERITY_SCALE)}")
    if record["status"] not in FAILURE_STATUSES:
        raise F2CError(f"status {record['status']!r} not in {FAILURE_STATUSES}")
    rc = record["root_cause"]
    if not (isinstance(rc, dict) and rc.get("kind") in
            ("confirmed", "hypothesis", "unknown")):
        raise F2CError('root_cause must be {"kind": "confirmed" | "hypothesis"'
                       ' | "unknown", ...} — facts and hypotheses stay separate')
    prov = record["provenance"]
    if not (isinstance(prov, dict) and isinstance(prov.get("artifact_hashes"),
                                                  dict)):
        raise F2CError("provenance.artifact_hashes must be a mapping of "
                       "source artifact -> sha256")
    return record


def validate_capability(record: Dict[str, Any]) -> Dict[str, Any]:
    allowed = CAPABILITY_REQUIRED + ("validation_results", "regression_results",
                                     "promotion_history", "generator")
    _check(record, CAPABILITY_REQUIRED, allowed, "capability record")
    if record["type"] not in CAPABILITY_TYPES:
        raise F2CError(f"capability type {record['type']!r} not in "
                       f"{CAPABILITY_TYPES}")
    if record["status"] not in CAPABILITY_STATUSES:
        raise F2CError(f"status {record['status']!r} not in "
                       f"{CAPABILITY_STATUSES}")
    if record["failure_class_for_retrieval"] not in FAILURE_CLASSES:
        raise F2CError(f"failure_class_for_retrieval "
                       f"{record['failure_class_for_retrieval']!r} not in "
                       f"controlled vocabulary {FAILURE_CLASSES}")
    for field in ("preconditions", "limitations", "originating_failures"):
        if not isinstance(record[field], list):
            raise F2CError(f"{field} must be a list")
    if record["permissions"] not in ("none", "sandbox", "trusted"):
        raise F2CError('permissions must be "none", "sandbox", or "trusted" — '
                       "a candidate may not declare trusted authority")
    if record["permissions"] == "trusted":
        raise F2CError("candidates may never self-declare trusted permission")
    if not isinstance(record["capability_id"], str) or \
            not record["capability_id"]:
        raise F2CError("capability_id must be a non-empty string")
    return record


# ------------------------------------------------------------------- store

class Store:
    """Append-only, hash-chained JSONL store. Tampering with any line is
    detectable by verify(). Duplicate submissions are idempotent: the
    existing record is returned, no second row is written."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: List[Dict[str, Any]] = []
        self._hashes: List[str] = []
        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        problems = self.verify_file()
        if problems:
            raise F2CError(f"store fails integrity check: {problems[0]}")
        for line in self.path.read_text().splitlines():
            row = json.loads(line)
            self.records.append(row["record"])
            self._hashes.append(row["hash"])
        for row in self.records:            # retrieval-boundary validation
            version = row.get("schema_version")
            if version not in (F2C_SCHEMA_VERSION,):
                raise F2CError(
                    f"store contains schema_version {version!r}; this "
                    f"engine supports {F2C_SCHEMA_VERSION} — refusing to "
                    "silently coerce or migrate records")
            # schema-drift prevention for the human-guided layer: every
            # record kind is re-validated at load, fail-closed
            if (row.get("record_kind") is None
                    and "gap_id" in row):
                validate_gap(row)
            elif "fwbc_id" in row:
                validate_fwbc(row)
            elif row.get("record_kind") == "human_decision":
                if row.get("authority") != "human":
                    raise F2CError("human_decision row without "
                                   "authority='human' — engine-authored "
                                   "decisions are forbidden; the human "
                                   "channel cannot be forged from "
                                   "inside the store")

    def verify_file(self) -> List[str]:
        """Recompute the whole chain from disk. Returns [] when intact."""
        problems, prev = [], GENESIS
        if not self.path.is_file():
            return problems
        for i, line in enumerate(self.path.read_text().splitlines()):
            try:
                row = json.loads(line)
                rec_hash, record = row["hash"], row["record"]
            except (ValueError, KeyError):
                problems.append(f"line {i}: malformed")
                break
            expect = hashlib.sha256(
                (prev + canonical(record)).encode()).hexdigest()
            if expect != rec_hash:
                problems.append(f"line {i}: hash mismatch — record was "
                                "mutated after chaining")
                break
            prev = rec_hash
        return problems

    @property
    def head(self) -> str:
        return self._hashes[-1] if self._hashes else GENESIS

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        if "failure_id" in record or "capability_id" in record:
            key = "failure_id" if "failure_id" in record else "capability_id"
            for existing in self.records:
                if existing.get(key) == record[key]:
                    if existing == record:
                        return existing          # idempotent duplicate
                    raise F2CError(f"{key} {record[key]!r} already exists "
                                   "with different content — refusing to "
                                   "fork provenance")
        rec_hash = hashlib.sha256(
            (self.head + canonical(record)).encode()).hexdigest()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"hash": rec_hash, "record": record}) + "\n")
        self.records.append(record)
        self._hashes.append(rec_hash)
        return record

    def find(self, key: str, value: str) -> Optional[Dict[str, Any]]:
        for r in self.records:
            if r.get(key) == value:
                return r
        return None


# ---------------------------------------------------------------- transitions

def _transition(status: str, to: str, table: Dict[str, tuple], rid: str) -> None:
    if to not in table.get(status, ()):
        raise F2CError(f"{rid}: illegal transition {status!r} -> {to!r}; "
                       f"allowed: {table.get(status, ())}")


# ------------------------------------------------ FWBC: immune memory
# The Failure White Blood Cell layer: each digested failure becomes a
# condition model (conditions, assumptions, antibodies) stored as an
# additional hash-chained record in the SAME database. Only conditions,
# signatures, antibodies, mitigations, and repurposed uses are stored —
# never blame, never shame, never personal criticism: the goal is
# system improvement, not fault assignment.

FWBC_CLASSES = ("code_failure", "process_failure", "assumption_failure",
                "communication_failure", "monitoring_failure",
                "accidental_success")
PATTERN_THEMES = ("assumption_mismatch", "verification_failure",
                  "automation_trust", "naming_ambiguity", "indexing_error",
                  "communication_gap", "tool_misuse", "specification_drift",
                  "hidden_dependency")
FWBC_REQUIRED = ("fwbc_id", "archetype_scan", "schema_version", "created_at",
                 "incident_ref", "incident_class", "incident_summary",
                 "conditions", "root_causes", "assumptions",
                 "trigger_factors", "environmental_factors",
                 "behavioral_factors", "antibodies", "repurposed_uses",
                 "pattern_signatures", "confidence")


def validate_fwbc(record: Dict[str, Any]) -> Dict[str, Any]:
    _check(record, FWBC_REQUIRED, FWBC_REQUIRED + ("blame_free_note",),
           "fwbc condition model")
    # the five-layer scan is mandatory: adult (what happened), child
    # (simply), chicken (immediately before), egg (what condition made
    # it possible), farmer (what system should change)
    scan = record.get("archetype_scan")
    layers = ("adult", "child", "chicken", "egg", "farmer")
    if not isinstance(scan, dict) or set(scan) != set(layers) or \
            any(not str(scan[k]).strip() for k in layers):
        raise F2CError("every incident digest must answer all five "
                       "layers: adult, child, chicken, egg, farmer — "
                       "the Farmer layer (what system should change) "
                       "is where continuous improvement lives")
    if record["incident_class"] not in FWBC_CLASSES:
        raise F2CError(f"incident_class {record['incident_class']!r} not "
                       f"in {FWBC_CLASSES} — classify before digesting")
    for sig in record["pattern_signatures"]:
        if sig not in PATTERN_THEMES:
            raise F2CError(f"pattern signature {sig!r} not in the "
                           f"theme catalog {PATTERN_THEMES}")
    if not record["conditions"]:
        raise F2CError("a condition model without conditions is an "
                       "undiagnosed failure — refuse it")
    if not record["root_causes"]:
        raise F2CError("a condition model without root causes stopped "
                       "asking 'what condition made this possible?' "
                       "too early — refuse a shallow digest")
    for ab in record["antibodies"]:
        need = ("name", "description", "detection_rule", "mitigation",
                "status")
        if not (isinstance(ab, dict) and all(k in ab for k in need)):
            raise F2CError(f"antibody {ab!r} missing {need}")
        if ab["status"] not in ("implemented", "proposed"):
            raise F2CError(f"antibody status {ab['status']!r} must be "
                           "'implemented' or 'proposed' — honest status "
                           "only")
    conf = record["confidence"]
    if not (isinstance(conf, float) and 0.0 <= conf <= 1.0):
        raise F2CError("confidence must be a float in [0, 1]")
    return record


# ------------------------- human-guided uncertainty resolution
# Facts, AI hypotheses, human decisions, and verification results are
# stored as SEPARATE record kinds in the same hash-chained store.
# The gap record is an inspectable explanation: known facts (linked
# to evidence), unknowns, and AI assumptions labeled as hypotheses —
# never a claim that AI reasoning is proof. Silence is never approval;
# a human decision defines intent but proves nothing technically and
# authorizes nothing beyond existing permissions. A gap closes ONLY
# with a human decision AND a passing independent verification.

GAP_CLASSES = ("evidence_gap", "ambiguous_requirement",
               "competing_hypotheses", "missing_regression_coverage",
               "unvalidated_mitigation")
GAP_STATUSES = ("open", "answered", "verified", "closed")
SAFETY_RANK = {"S1": 0, "S2": 1, "S3": 2}


def validate_gap(record: Dict[str, Any]) -> Dict[str, Any]:
    need = ("gap_id", "schema_version", "created_at", "gap_class",
            "subject", "plain_language", "known_facts", "unknowns",
            "assumptions", "competing_hypotheses", "consequences",
            "uncertainty", "safety_impact", "recurrence",
            "human_resolvable", "recommended_options", "status")
    _check(record, need, need, "uncertainty gap")
    if not record["plain_language"].strip():
        raise F2CError("plain_language is required — a human must be "
                       "able to read what is missing and why it "
                       "matters without seeing the machinery")
    if not record["consequences"].strip():
        raise F2CError("consequences are required — what happens if "
                       "this gap stays open")
    if record["gap_class"] not in GAP_CLASSES:
        raise F2CError(f"gap_class {record['gap_class']!r} not in "
                       f"{GAP_CLASSES}")
    if record["safety_impact"] not in SAFETY_RANK:
        raise F2CError(f"safety_impact {record['safety_impact']!r} not "
                       f"in {sorted(SAFETY_RANK)}")
    if not (isinstance(record["uncertainty"], float)
            and 0.0 <= record["uncertainty"] <= 1.0):
        raise F2CError("uncertainty must be a float in [0, 1]")
    if not (isinstance(record["recurrence"], int)
            and record["recurrence"] >= 0):
        raise F2CError("recurrence must be a non-negative int")
    if record["human_resolvable"] and not record["recommended_options"]:
        raise F2CError("a human-resolvable gap must offer recommended "
                       "options — an empty question wastes the "
                       "human's attention")
    return record


# ------------------------------------------------------------- the engine

class FailureToCapabilityEngine:
    """Owns failure records, capabilities, and the validation harness.
    The trusted core's data is only ever read here; candidate execution
    happens in throwaway sandboxes."""

    def __init__(self, store_path: Path, repo_root: Path):
        self.store = Store(store_path)
        self.repo_root = Path(repo_root)

    # Stage 0 — ingest (validate + referential integrity + idempotency)
    def ingest_failure(self, record: Dict[str, Any]) -> Dict[str, Any]:
        validate_failure(record)
        fixture = self.repo_root / record["fixture_ref"]
        if not fixture.is_file():
            raise F2CError(f"fixture_ref {record['fixture_ref']!r} does not "
                           "exist — broken reference, failing closed")
        digest = sha256_file(fixture)
        recorded = record["provenance"]["artifact_hashes"].get(
            record["fixture_ref"])
        if recorded != digest:
            raise F2CError(f"fixture {record['fixture_ref']} hash mismatch: "
                          f"ledger says {recorded}, disk says {digest}")
        return self.store.append(record)

    def record_disposition(self, ref: str, status: str,
                            rationale: str,
                            **extra) -> Dict[str, Any]:
        """Append a durable disposition record for a failure or
        capability. The original record rows stay immutable; the
        lifecycle lives on as additional hash-chained rows in the SAME
        store. Idempotent on (ref, status, rationale): replays do not
        duplicate rows."""
        for r in self.store.records:
            if (r.get("record_kind") == "disposition"
                    and r.get("ref") == ref
                    and r.get("status") == status
                    and r.get("rationale") == rationale):
                return r
        count = sum(1 for r in self.store.records
                    if r.get("record_kind") == "disposition")
        row = {"record_kind": "disposition",
               "disposition_id": f"D-{count + 1}",
               "schema_version": F2C_SCHEMA_VERSION,
               "ref": ref, "status": status, "rationale": rationale,
               "at": _now()}
        row.update(extra)
        return self.store.append(row)

    def dispositions(self, ref: str) -> List[Dict[str, Any]]:
        return [r for r in self.store.records
                if r.get("record_kind") == "disposition"
                and r.get("ref") == ref]

    # -------------------------------------------- FWBC: immune memory
    def analyze_incident(self, model: Dict[str, Any]) -> Dict[str, Any]:
        """Digest a failure into a condition model and append it to the
        same hash-chained store. Idempotent on fwbc_id; antibody honesty
        is cross-checked: an antibody may claim 'implemented' only by
        citing a capability the store has actually validated."""
        validate_fwbc(model)
        for r in self.store.records:
            if r.get("fwbc_id") == model["fwbc_id"]:
                if r == model:
                    return r               # idempotent replay
                raise F2CError(f"fwbc_id {model['fwbc_id']!r} already "
                               "exists with different content — "
                               "refusing to fork the condition model")
        for ab in model["antibodies"]:
            cid = ab.get("capability_id")
            if cid is not None:
                cap = self.store.find("capability_id", cid)
                if cap is None:
                    raise F2CError(f"antibody {ab['name']!r} cites "
                                   f"capability {cid!r} which is not in "
                                   "the store — refuse unverified "
                                   "references")
                if ab["status"] == "implemented" and \
                        cap.get("status") != "validated":
                    raise F2CError(f"antibody {ab['name']!r} claims "
                                   "'implemented' but capability {cid!r} "
                                   f"is {cap.get('status')!r}, not "
                                   "validated — a label is not "
                                   "implementation evidence")
        return self.store.append(model)

    def immune_memory(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """Retrieve condition models and their antibodies by pattern
        signature and/or incident class. Recurring themes across
        unrelated failures are the point: this is how the system
        recognizes a pathogen it has seen before. Payloads are data,
        never instructions."""
        sigs = set(query.get("pattern_signatures", ()))
        cls = query.get("incident_class")
        models = [r for r in self.store.records
                  if "fwbc_id" in r
                  and (not sigs or sigs & set(r["pattern_signatures"]))
                  and (cls is None or r["incident_class"] == cls)]
        antibodies = sorted({ab["name"] for m in models
                             for ab in m["antibodies"]})
        return {"models": models, "antibodies": antibodies}

    # ------------------ human-guided uncertainty resolution
    def open_gap(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Register a detected gap: unresolved evidence gaps, ambiguous
        requirements, competing hypotheses, missing regression
        coverage, unvalidated mitigations. Idempotent on gap_id; opens
        as 'open' — silence is never approval, so nothing else moves
        the status."""
        validate_gap(record)
        if record["status"] != "open":
            raise F2CError("gaps open as 'open' — closure requires a "
                           "human decision AND passing verification")
        for r in self.store.records:
            if r.get("gap_id") == record["gap_id"] and \
                    "gap_id" in r:
                if r == record:
                    return r                # idempotent replay
                raise F2CError(f"gap_id {record['gap_id']!r} exists "
                               "with different content — refusing to "
                               "fork the gap")
        return self.store.append(record)

    def answer_gap(self, gap_id: str, decision: str,
                   selected_option: str) -> Dict[str, Any]:
        """Record a HUMAN decision as its own record kind. The engine
        never authors these rows; the decision defines intent only —
        it neither proves technical correctness nor authorizes
        anything beyond existing permissions. 'open' becomes
        'answered'; the gap is NOT closed."""
        gap = self.store.find("gap_id", gap_id)
        if gap is None or "gap_id" not in gap:
            raise F2CError(f"unknown gap {gap_id!r}")
        if gap["status"] not in ("open", "answered"):
            raise F2CError(f"gap {gap_id} is {gap['status']} — "
                           "verified or closed gaps accept no new "
                           "decisions")
        for r in self.store.records:
            if (r.get("record_kind") == "human_decision"
                    and r.get("gap_id") == gap_id):
                if (r["decision"] == decision
                        and r["selected_option"] == selected_option):
                    return r                # idempotent replay
                raise F2CError("a different human decision already "
                               "exists for this gap — refusing to "
                               "fork the decision")
        count = sum(1 for r in self.store.records
                    if r.get("record_kind") == "human_decision")
        row = {"record_kind": "human_decision",
               "decision_id": f"H-{count + 1}",
               "schema_version": F2C_SCHEMA_VERSION,
               "gap_id": gap_id, "decision": decision,
               "selected_option": selected_option,
               "authority": "human", "at": _now()}
        self.store.append(row)
        gap["status"] = "answered"
        self.record_disposition(gap_id, "answered", decision[:200],
                                selected_option=selected_option)
        return row

    def verify_gap_closure(self, gap_id: str, name: str, passed: bool,
                           evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Independent mechanical verification — its own record kind,
        separate from facts, AI hypotheses, and human decisions. A
        gap closes ONLY with a human decision AND a passing
        verification; a failed verification is recorded honestly and
        leaves the gap open for re-work."""
        gap = self.store.find("gap_id", gap_id)
        if gap is None or "gap_id" not in gap:
            raise F2CError(f"unknown gap {gap_id!r}")
        if gap["status"] != "answered":
            raise F2CError(f"gap {gap_id} is {gap['status']} — "
                           "verification cannot replace the human "
                           "decision, and silence is not approval")
        for r in self.store.records:
            if (r.get("record_kind") == "verification_result"
                    and r.get("gap_id") == gap_id
                    and r.get("name") == name
                    and r.get("passed") == bool(passed)
                    and r.get("evidence") == evidence):
                return r                # idempotent replay
        count = sum(1 for r in self.store.records
                    if r.get("record_kind") == "verification_result")
        row = {"record_kind": "verification_result",
               "verification_id": f"V-{count + 1}",
               "schema_version": F2C_SCHEMA_VERSION,
               "gap_id": gap_id, "name": name,
               "passed": bool(passed), "evidence": evidence,
               "at": _now()}
        self.store.append(row)
        if passed:
            gap["status"] = "closed"
            self.record_disposition(
                gap_id, "closed",
                "human decision plus passing independent verification")
        else:
            self.record_disposition(
                gap_id, "verification_failed",
                "recorded honestly; the gap stays open for re-work")
        return row

    def pending_questions(self,
                          limit: Optional[int] = None) -> List[Any]:
        """The human's ranked inbox: open, human-resolvable gaps sorted
        by safety impact, then uncertainty, then recurrence. Non-
        resolvable gaps are excluded — the human is only asked what
        human input can actually resolve."""
        gaps = [r for r in self.store.records
                if "gap_id" in r and r.get("record_kind") is None
                and r["status"] == "open" and r["human_resolvable"]]
        gaps.sort(key=lambda g: (SAFETY_RANK[g["safety_impact"]],
                                 -g["uncertainty"],
                                 -g["recurrence"]))
        return gaps[:limit] if limit else gaps

    def set_status(self, failure_id: str, to: str) -> Dict[str, Any]:
        rec = self.store.find("failure_id", failure_id)
        if rec is None:
            raise F2CError(f"unknown failure_id {failure_id!r}")
        _transition(rec["status"], to, FAILURE_TRANSITIONS, failure_id)
        rec["status"] = to
        return rec

    # Stage A — retrieval (documented similarity: class + severity overlap)
    def retrieve(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Returns validated capabilities relevant to a query, with their
        applicability conditions and limitations attached. Payloads are
        data, never instructions."""
        out = []
        for r in self.store.records:
            if "capability_id" not in r or r["status"] not in ("validated",
                                                                "deprecated"):
                continue
            if r["type"] in query.get("capability_types", CAPABILITY_TYPES):
                if r["failure_class_for_retrieval"] == query.get(
                        "failure_class", r["failure_class_for_retrieval"]):
                    out.append({"capability": r,
                                 "applicability": r["preconditions"],
                                 "limitations": r["limitations"],
                                 "status": r["status"],
                                 "source": f"store:{r['capability_id']}"})
        return out

    # Stage B — candidate generation (strict schema, untrusted output)
    def propose_candidate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """payload comes from any generator (LLM or deterministic). It is
        untrusted: it must validate or it is recorded as rejected, and the
        rejection is auditable."""
        try:
            if payload.get("status") != "proposed":
                raise F2CError(
                    "candidates enter the store as 'proposed' only; a "
                    f"self-declared status {payload.get('status')!r} is "
                    "refused — validation is the harness's verdict, not "
                    "the generator's")
            forged = [f for f in ("validation_results", "regression_results",
                                  "promotion_history") if f in payload]
            if forged:
                raise F2CError(
                    f"candidate carries harness-owned field(s) {forged}; "
                    "a candidate cannot arrive with its own test results "
                    "or promotion history")
            validate_capability(payload)
            rec = self.store.append(payload)
        except F2CError as exc:
            rejected = {"capability_id": payload.get("capability_id",
                                                     "unknown"),
                        "schema_version": F2C_SCHEMA_VERSION,
                        "created_at": _now(),
                        "type": payload.get("type", "unknown"),
                        "status": "rejected",
                        "description": f"schema validation failed: {exc}",
                        "originating_failures": payload.get(
                            "originating_failures", []),
                        "preconditions": [], "limitations": ["malformed"],
                        "permissions": "none",
                        "provenance": {"artifact_hashes": {},
                                       "rejection": str(exc)}}
            self.store.append(rejected)
            raise F2CError(f"candidate rejected at validation: {exc}")
        return rec

    # Stage C — isolated validation. The harness owns the verdict.
    def validate_candidate(self, capability_id: str,
                           arms: List[Callable[[], Dict[str, Any]]],
                           required: Optional[List[str]] = None) -> Dict[str, Any]:
        """Run each arm (a callable returning {'name', 'passed', ...}).
        A candidate passes only if every required arm passes. The candidate
        cannot choose its arms, skip one, or read this verdict."""
        rec = self.store.find("capability_id", capability_id)
        if rec is None:
            raise F2CError(f"unknown capability_id {capability_id!r}")
        _transition(rec["status"], "under_test", CAPABILITY_TRANSITIONS,
                    capability_id)
        rec["status"] = "under_test"
        results, started = [], time.monotonic()
        for arm in arms:
            r = arm()
            results.append(r)
        elapsed = round(time.monotonic() - started, 3)
        required = required or [r["name"] for r in results]
        by_name = {r["name"]: r for r in results}
        passed = all(by_name[n]["passed"] for n in required if n in by_name)
        missing = [n for n in required if n not in by_name]
        if missing:
            raise F2CError(f"validation arms missing: {missing}")
        rec["validation_results"] = {"arms": results, "passed": passed,
                                     "wall_seconds": elapsed,
                                     "evaluated_at": _now()}
        return rec

    # Stage D — promotion / rejection (explicit, auditable, reversible)
    def promote(self, capability_id: str, approver: str,
                rationale: str) -> Dict[str, Any]:
        rec = self.store.find("capability_id", capability_id)
        if rec is None:
            raise F2CError(f"unknown capability_id {capability_id!r}")
        if not rec.get("validation_results", {}).get("passed"):
            raise F2CError(f"{capability_id} was never validated — an LLM "
                           "label or a claim is not promotion evidence")
        _transition(rec["status"], "validated", CAPABILITY_TRANSITIONS,
                    capability_id)
        rec["status"] = "validated"
        rec.setdefault("promotion_history", []).append(
            {"action": "promoted", "by": approver, "rationale": rationale,
             "at": _now()})
        self.record_disposition(capability_id, "validated",
                                rationale, approver=approver)
        return rec

    def reject(self, capability_id: str, rationale: str) -> Dict[str, Any]:
        rec = self.store.find("capability_id", capability_id)
        if rec is None:
            raise F2CError(f"unknown capability_id {capability_id!r}")
        rec["status"] = "rejected"
        rec.setdefault("promotion_history", []).append(
            {"action": "rejected", "by": "harness", "rationale": rationale,
             "at": _now()})
        self.record_disposition(capability_id, "rejected", rationale)
        return rec

    def demote(self, capability_id: str, rationale: str) -> Dict[str, Any]:
        """Promotion is reversible."""
        rec = self.store.find("capability_id", capability_id)
        if rec is None:
            raise F2CError(f"unknown capability_id {capability_id!r}")
        _transition(rec["status"], "deprecated", CAPABILITY_TRANSITIONS,
                    capability_id)
        rec["status"] = "deprecated"
        rec["promotion_history"].append(
            {"action": "deprecated", "by": "harness", "rationale": rationale,
             "at": _now()})
        return rec

    # Retirement — a failure never retires on documentation alone.
    def retire_failure(self, failure_id: str,
                       regression_test: str) -> Dict[str, Any]:
        rec = self.store.find("failure_id", failure_id)
        if rec is None:
            raise F2CError(f"unknown failure_id {failure_id!r}")
        if rec["status"] != "validated":
            raise F2CError(f"{failure_id} is {rec['status']}, not validated — "
                           "cannot retire")
        module, _, test = regression_test.partition("::")
        source = self.repo_root / (module.replace(".", "/") + ".py")
        if not source.is_file() or test.split(".")[-1] not in source.read_text():
            raise F2CError(f"regression_test {regression_test!r} does not "
                           "exist — retirement refused")
        self.set_status(failure_id, "retired")
        rec["regression_test_refs"] = [regression_test]
        rec["disposition"] = {"decision": "retired",
                              "rationale": "executable regression coverage",
                              "regression_test": regression_test,
                              "authority": "harness", "at": _now()}
        self.record_disposition(failure_id, "retired",
                                "retired behind executable regression "
                                "coverage",
                                regression_test=regression_test)
        return rec


def run_isolated(command: List[str], repo_root: Path,
                 timeout: int = 120) -> Dict[str, Any]:
    """Run a validation command inside a throwaway copy of the repository.
    Default-deny egress is the host's job; here: scratch cwd, stripped
    environment, hard timeout, and the real repo tree is never the cwd."""
    import shutil, tempfile
    sandbox = Path(tempfile.mkdtemp(prefix="f2c-sandbox-"))
    try:
        snapshot = sandbox / "repo"
        shutil.copytree(repo_root, snapshot,
                        ignore=shutil.ignore_patterns(".git", "site"))
        proc = subprocess.run(
            command, cwd=snapshot, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"},
            capture_output=True, text=True)
        return {"command": " ".join(command), "cwd": "throwaway snapshot",
                "exit_code": proc.returncode, "stdout_tail":
                    proc.stdout[-400:], "sandbox": sandbox}
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
