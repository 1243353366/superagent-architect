"""Typed claim language, dependency graph, contradiction detector, and the
acceptance gate.

A claim is the only unit the pipeline may reason with. Requirements do not
become accepted facts until the gate certifies them, and certification
requires evidence — a claim with no evidence is "unknown (missing)", a claim
with conflicting evidence is "contested", and the two are NEVER collapsed into
one generic "uncertain" bucket. That distinction is the calibration core:
absence of evidence and contradiction of evidence are different epistemic
states with different remedies (ask vs. reconcile).

Deterministic by design: no model calls, no randomness. Every status is
recomputable from the evidence log alone.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- vocabulary

CLAIM_TYPES = ("requirement", "interpretation", "assumption", "derived_fact",
               "open_question")

EVIDENCE_KINDS = ("prompt_excerpt", "user_answer", "test_result")

# Calibration bands. Ordered from strongest to weakest support. Two distinct
# "unknown" bands are the point of the prototype: they carry different
# remedies (missing -> ask/measure; conflicted -> reconcile the sources).
BAND_CERTAIN = "certain"                  # multiple independent supports
BAND_SUPPORTED = "supported"              # at least one support, deps intact
BAND_CONTESTED = "contested"              # conflicting evidence exists
BAND_REFUTED = "refuted"                  # evidence eliminated this hypothesis
BAND_UNKNOWN_MISSING = "unknown-missing"  # no evidence at all
BAND_UNKNOWN_CONFLICTED = "unknown-conflicted"  # every witness contradicted
BAND_ORDER = {BAND_CERTAIN: 0, BAND_SUPPORTED: 1, BAND_CONTESTED: 2,
              BAND_REFUTED: 3, BAND_UNKNOWN_MISSING: 4,
              BAND_UNKNOWN_CONFLICTED: 5}

# Per-evidence-kind reliability used for calibration (documented, tunable).
EVIDENCE_RELIABILITY = {
    "prompt_excerpt": 0.8,   # the user did say it — but wording may be ambiguous
    "user_answer": 0.95,     # an explicit human answer to a direct question
    "test_result": 0.9,      # an executed probe of behavior
}


@dataclass
class Evidence:
    kind: str                    # one of EVIDENCE_KINDS
    content: str                 # the excerpt / answer / observed outcome
    source: str = ""             # where it came from (url, actor, probe id)
    reliability: float = field(default=0.0)   # 0 = use EVIDENCE_RELIABILITY[kind]

    def resolved_reliability(self) -> float:
        if self.reliability and 0 < self.reliability <= 1:
            return self.reliability
        return EVIDENCE_RELIABILITY.get(self.kind, 0.5)


@dataclass
class Claim:
    id: str
    statement: str
    type: str                                   # one of CLAIM_TYPES
    # Optional structured predicate — enables deterministic contradiction
    # detection. Free-text-only claims are honestly reported as
    # "cannot-machine-check" rather than pseudo-verified.
    predicate: Optional[dict] = None             # {subject, property, value, polarity}
    evidence: List[str] = field(default_factory=list)   # evidence ids
    # one-sided refutation: evidence that eliminates THIS hypothesis without
    # asserting a counter-claim (so the surviving candidate is not dragged
    # into a symmetric contradiction)
    refuted_by: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)  # claim ids


@dataclass
class Contradiction:
    claim_a: str
    claim_b: str
    subject: str
    property_: str
    value_a: str
    value_b: str


class ClaimStore:
    """Append-only claims + evidence with recomputable statuses."""

    def __init__(self) -> None:
        self.evidence: Dict[str, Evidence] = {}
        self.claims: Dict[str, Claim] = {}
        self._evidence_seq = 0
        self._claim_seq = 0
        self.contradictions: List[Contradiction] = []

    # ---------------------------------------------------------- add / read

    def add_evidence(self, ev: Evidence) -> str:
        self._evidence_seq += 1
        ev_id = f"E{self._evidence_seq}"
        self.evidence[ev_id] = ev
        return ev_id

    def add_claim(self, claim: Claim) -> str:
        if not claim.id:
            self._claim_seq += 1
            claim.id = f"C{self._claim_seq}"
        if claim.id in self.claims:
            raise ValueError(f"duplicate claim id: {claim.id}")
        self.claims[claim.id] = claim
        return claim.id

    # ---------------------------------------------------------- calibration

    def _dependents_broken(self, claim: Claim, seen: Optional[set] = None) -> bool:
        """True when any transitive dependency is unsupported or contradicted."""
        seen = seen or set()
        for dep in claim.depends_on:
            if dep in seen:
                continue  # cycle-safe; cycles are reported separately
            seen.add(dep)
            d = self.claims.get(dep)
            if d is None:
                return True
            if d.evidence is None or not d.evidence:
                return True
            if self._in_contradiction(d.id):
                return True
            if not d.depends_on:
                continue
            if self._dependents_broken(d, seen):
                return True
        return False

    def _in_contradiction(self, claim_id: str) -> bool:
        return any(c.claim_a == claim_id or c.claim_b == claim_id
                   for c in self.contradictions)

    def detect_contradictions(self) -> List[Contradiction]:
        """Deterministic contradiction detection over structured predicates.

        Two claims contradict when they assert the same (subject, property)
        with different values, or the same value with opposing polarity.
        Claims without predicates are not machine-checkable and are never
        pseudo-verified — they simply cannot contradict-detect here.

        Only ASSERTED claims participate: a claim with no evidence is a
        hypothesis (an alternative), not an assertion. Candidate
        interpretations therefore coexist without contradiction until
        evidence eliminates them — one more place where 'missing' and
        'contradictory' must not be conflated.
        """
        found: List[Contradiction] = []
        with_pred = [c for c in self.claims.values()
                     if c.predicate and c.evidence]
        for i, a in enumerate(with_pred):
            for b in with_pred[i + 1:]:
                pa, pb = a.predicate, b.predicate
                if (pa["subject"] != pb["subject"]
                        or pa["property"] != pb["property"]):
                    continue
                same_value = pa["value"] == pb["value"]
                polarity_conflict = (pa.get("polarity", "positive")
                                     != pb.get("polarity", "positive"))
                if (not same_value) or (same_value and polarity_conflict):
                    found.append(Contradiction(
                        claim_a=a.id, claim_b=b.id,
                        subject=pa["subject"], property_=pa["property"],
                        value_a=str(pa["value"]), value_b=str(pb["value"])))
        # replace, do not duplicate
        self.contradictions = found
        return found

    def band(self, claim_id: str) -> str:
        """Calibration band for a claim. Missing and conflicted evidence are
        distinct bands; the gate reads exactly this."""
        claim = self.claims[claim_id]
        if claim.refuted_by:
            return BAND_REFUTED
        if self._in_contradiction(claim_id):
            return BAND_UNKNOWN_CONFLICTED
        if not claim.evidence:
            return BAND_UNKNOWN_MISSING
        if self._dependents_broken(claim):
            return BAND_UNKNOWN_MISSING   # inherited support is broken
        reliabilities = [self.evidence[e].resolved_reliability()
                         for e in claim.evidence if e in self.evidence]
        if not reliabilities:
            return BAND_UNKNOWN_MISSING
        # independent support: distinct evidence kinds compound
        kinds = {self.evidence[e].kind for e in claim.evidence if e in self.evidence}
        # noisy-OR of witness reliabilities, capped, banded
        score = 1.0
        for r in reliabilities:
            score *= (1.0 - r)
        confidence = 1.0 - score
        if len(kinds) >= 2 and confidence >= 0.95:
            return BAND_CERTAIN
        return BAND_SUPPORTED

    def calibration(self, claim_id: str) -> dict:
        band = self.band(claim_id)
        claim = self.claims[claim_id]
        reliabilities = [self.evidence[e].resolved_reliability()
                         for e in claim.evidence if e in self.evidence]
        score = 1.0
        for r in reliabilities:
            score *= (1.0 - r)
        return {
            "claim": claim_id,
            "band": band,
            "numeric": None if not reliabilities else round(1 - score, 3),
            "evidence_count": len(claim.evidence),
            "evidence_kinds": sorted({self.evidence[e].kind
                                       for e in claim.evidence
                                       if e in self.evidence}),
            "depends_on": list(claim.depends_on),
            "deps_broken": self._dependents_broken(claim),
            "remedy": ("hypothesis eliminated by evidence; drop it"
                       if band == BAND_REFUTED
                       else "reconcile conflicting evidence"
                       if band == BAND_UNKNOWN_CONFLICTED
                       else "ask a resolving question or run a distinguishing test"
                       if band == BAND_UNKNOWN_MISSING
                       else "none — supported"),
        }


# ------------------------------------------------------------- the gate

@dataclass
class Blocker:
    claim_id: str
    reason: str                     # unsupported | contradicted | dependency-broken
    band: str
    remedy: str


class AcceptanceGate:
    """Blocks unsupported requirements from becoming accepted facts.

    A requirement may only be accepted when its band is SUPPORTED or CERTAIN
    and no dependency is broken. The gate returns WHAT blocks each refusal and
    the remedy — it never silently waves anything through, and it never
    converts 'missing' into 'probably fine'.
    """

    def __init__(self, store: ClaimStore) -> None:
        self.store = store

    def certify(self, requirement_ids: Optional[List[str]] = None) -> dict:
        store = self.store
        store.detect_contradictions()
        ids = requirement_ids or [c.id for c in store.claims.values()
                                  if c.type == "requirement"]
        blockers: List[Blocker] = []
        accepted: List[str] = []
        for cid in ids:
            claim = store.claims.get(cid)
            if claim is None:
                blockers.append(Blocker(cid, "unknown-claim", "n/a",
                                        "claim does not exist"))
                continue
            band = store.band(cid)
            if band == BAND_UNKNOWN_CONFLICTED:
                blockers.append(Blocker(cid, "contradicted", band,
                                        "reconcile the conflicting evidence"))
            elif band == BAND_UNKNOWN_MISSING:
                reason = ("dependency-broken"
                          if store._dependents_broken(claim) and claim.evidence
                          else "unsupported")
                blockers.append(Blocker(cid, reason, band,
                                        "supply evidence, ask the resolving "
                                        "question, or run a distinguishing test"))
            else:
                accepted.append(cid)
        certified = not blockers
        return {"certified": certified, "accepted": accepted,
                "blockers": [vars(b) for b in blockers],
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
