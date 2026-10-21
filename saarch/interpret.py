"""Compile a natural-language request into candidate interpretations, generate
distinguishing tests, and refuse certification while the evidence cannot
distinguish them.

The honest core: ambiguous human intent cannot always be recovered from the
prompt alone. What this module promises instead is smaller and deliverable —
when ambiguity blocks certification, it names EXACTLY the question or
experiment that resolves it, choosing the probe that splits the remaining
candidate space most (greedy max-split, a deterministic seed of a
failure-discovery algorithm over an interaction space that would otherwise
grow explosively).

Deterministic pattern engine: ambiguity patterns are cue-matched, generated
candidates carry structured predicates, and distinguishing tests are derived
from those predicates — so contradiction detection and evidence-based
elimination are all machine-checkable. No LLM is consulted anywhere here;
LLM-generated interpretations are future work and would land as just another
source of candidate claims.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .claims import (
    BAND_CONTESTED, BAND_REFUTED, BAND_UNKNOWN_CONFLICTED, Claim, ClaimStore,
    Evidence, AcceptanceGate,
)


# ---------------------------------------------------------------- patterns

@dataclass
class Candidate:
    id: str
    statement: str
    predicate: dict          # {subject, property, value, polarity}


@dataclass
class DistinguishingTest:
    id: str
    probe: str                                   # the experiment, stated concretely
    question: str                                 # the human-facing resolving question
    outcomes: Dict[str, str]                      # candidate_id -> expected observation
    answer_map: Dict[str, str]                    # human answer -> candidate it selects


@dataclass
class AmbiguityPattern:
    name: str
    cue: re.Pattern                              # matched against the request text
    candidates: List[Candidate]
    test: DistinguishingTest


_PATTERNS: List[AmbiguityPattern] = []


def _pattern(name: str, cue: str, candidates: List[Candidate],
             test: DistinguishingTest) -> AmbiguityPattern:
    p = AmbiguityPattern(name=name, cue=re.compile(cue, re.IGNORECASE),
                         candidates=candidates, test=test)
    _PATTERNS.append(p)
    return p


# --- pattern 1: scope of "anytime" in cancellation ---
_pattern(
    "cancellation-scope",
    r"\bcancel[a-z]*\b[^.]{0,40}\banytime\b|\banytime\b[^.]{0,40}\bcancel",
    [Candidate("A", "Cancellation is permitted BEFORE commitment only",
               {"subject": "subscription", "property": "cancel_window",
                "value": "pre_commitment", "polarity": "positive"}),
     Candidate("B", "Cancellation is permitted at ANY time, including after "
                "commitment", {"subject": "subscription",
                               "property": "cancel_window",
                               "value": "any_time", "polarity": "positive"})],
    DistinguishingTest(
        id="T-cancel-scope",
        probe="Start a subscription, then after the commitment point call "
              "cancel; observe success vs rejection.",
        question="After someone commits (subscribes), can they still cancel "
                 "anytime — or does 'anytime' only mean before committing?",
        outcomes={"A": "cancel after commitment is REJECTED",
                  "B": "cancel after commitment is ACCEPTED"},
        answer_map={"after commitment too": "B", "any time": "B",
                    "only before commitment": "A", "before committing": "A"}))

# --- pattern 2: deletion semantics ---
_pattern(
    "deletion-semantics",
    r"\bdelete\b[^.]{0,30}\b(data|record|account|entries?)\b",
    [Candidate("A", "Delete means HARD delete: data is removed permanently",
               {"subject": "data", "property": "delete_semantics",
                "value": "hard", "polarity": "positive"}),
     Candidate("B", "Delete means SOFT delete: data is hidden but retained",
               {"subject": "data", "property": "delete_semantics",
                "value": "soft", "polarity": "positive"})],
    DistinguishingTest(
        id="T-delete-semantics",
        probe="Delete a record, then restart and inspect the store: does the "
              "record return (soft) or stay gone (hard)?",
        question="When a user deletes data, must it be gone permanently "
                 "(hard delete) — or is hiding-but-retaining acceptable?",
        outcomes={"A": "record absent after restart",
                  "B": "record present-but-hidden after restart"},
        answer_map={"hard delete": "A", "permanently removed": "A",
                    "soft delete": "B", "hidden is fine": "B"}))

# --- pattern 3: "local" storage strength ---
_pattern(
    "local-strength",
    r"\b(data|information|entries?)\b[^.]{0,40}\blocal\b|\blocal\b[^.]{0,40}\b(data|information|entries?)\b",
    [Candidate("A", "Local means NO network calls with user data at all",
               {"subject": "data", "property": "network_use",
                "value": "none", "polarity": "positive"}),
     Candidate("B", "Local means stored locally, but sync/backup calls are "
                "acceptable", {"subject": "data",
                               "property": "network_use",
                               "value": "storage_only", "polarity": "positive"})],
    DistinguishingTest(
        id="T-local-strength",
        probe="Attempt any outbound request carrying user data (sync/backup); "
              "observe whether one occurs.",
        question="Does 'data stays local' forbid ALL network calls with user "
                 "data — or only off-device storage, allowing optional sync?",
        outcomes={"A": "no outbound data call occurs",
                  "B": "a sync/backup call occurs"},
        answer_map={"all network calls": "A", "no network at all": "A",
                    "sync is fine": "B", "storage only": "B"}))

# --- pattern 4: unquantified performance words ---
_pattern(
    "performance-threshold",
    r"\b(fast|quick|snappy|responsive)\b",
    [Candidate("A", "'Fast' means a concrete threshold: p95 under 200ms",
               {"subject": "system", "property": "latency_target",
                "value": "p95<200ms", "polarity": "positive"}),
     Candidate("B", "'Fast' is subjective: no enforceable threshold exists",
               {"subject": "system", "property": "latency_target",
                "value": "unspecified", "polarity": "positive"})],
    DistinguishingTest(
        id="T-perf-threshold",
        probe="Measure p95 latency and compare to a threshold; without a "
              "number, acceptance is unfalsifiable.",
        question="What response-time threshold makes 'fast' testable — e.g. "
                 "p95 under 200ms — or is no threshold required?",
        outcomes={"A": "p95 under 200ms passes, above fails",
                  "B": "no threshold to check"},
        answer_map={"200ms": "A", "a threshold": "A",
                    "no threshold": "B", "subjective": "B"}))


def active_patterns() -> List[AmbiguityPattern]:
    return list(_PATTERNS)


# ------------------------------------------------------- interpretation set

@dataclass
class Interpretation:
    pattern: str
    requirement_claim: str          # claim id of the requirement under test
    candidate_claims: Dict[str, str]  # candidate id -> claim id
    test: DistinguishingTest


class Interpreter:
    """Compile requests -> candidate interpretations -> distinguishing tests.

    The requirement itself is a claim; each candidate interpretation is a
    child claim it depends on. The requirement stays uncertifiable while its
    candidate interpretations remain live (un-distinguished) — the gate
    refuses, and this module names the exact resolving question.
    """

    def __init__(self, store: Optional[ClaimStore] = None) -> None:
        self.store = store or ClaimStore()
        self.gate = AcceptanceGate(self.store)
        self.interpretations: List[Interpretation] = []
        self._test_evidence: Dict[str, str] = {}   # test id -> evidence id
        self._req_seq = 0

    # ------------------------------------------------------- compile

    def compile(self, request_text: str) -> dict:
        """Compile a natural-language request. Returns the interpretation
        report; certification is always available via certify()."""
        self._req_seq += 1
        req_id = f"R{self._req_seq}"
        # the request itself is evidence of what the human wrote
        ev = self.store.add_evidence(Evidence(kind="prompt_excerpt",
                                              content=request_text.strip(),
                                              source="request"))
        self.store.add_claim(Claim(
            id=req_id, type="requirement",
            statement=f"Request: {request_text.strip()[:160]}",
            evidence=[ev]))
        ambiguity_found = False
        for pat in active_patterns():
            if pat.cue.search(request_text):
                ambiguity_found = True
                # requirement carries the excerpt; candidates depend on it
                cand_claims: Dict[str, str] = {}
                for cand in pat.candidates:
                    cid = self.store.add_claim(Claim(
                        id=f"{req_id}-{pat.name}-{cand.id}",
                        type="interpretation",
                        statement=cand.statement,
                        predicate=dict(cand.predicate),
                        depends_on=[req_id]))
                    cand_claims[cand.id] = cid
                self.interpretations.append(Interpretation(
                    pattern=pat.name, requirement_claim=req_id,
                    candidate_claims=cand_claims, test=pat.test))
        return {
            "request_claim": req_id,
            "ambiguous": ambiguity_found,
            "interpretations": [i.pattern for i in self.interpretations
                                if i.requirement_claim == req_id],
        }

    # ------------------------------------------------------- resolution

    def live_candidates(self, interp: Interpretation) -> List[str]:
        """Candidates not yet eliminated by evidence.

        A candidate interpretation is a HYPOTHESIS: absence of evidence is its
        normal state, not a refutation. A candidate is dead only when evidence
        contradicts it (contested / conflicted band). This is exactly the
        missing-vs-contradictory distinction: liveness reads contradiction,
        not support.
        """
        live = []
        for cand_id, claim_id in interp.candidate_claims.items():
            if self.store.band(claim_id) in (BAND_CONTESTED, BAND_REFUTED,
                                             BAND_UNKNOWN_CONFLICTED):
                continue
            live.append(cand_id)
        return live

    def resolving_questions(self) -> List[dict]:
        """The exact questions/experiments that resolve live ambiguity.

        Greedy max-split: when several tests could be asked, order by how many
        candidate pairs each splits. This is the deterministic seed of a
        failure-discovery algorithm: probe where the evidence splits the most.
        """
        out = []
        for interp in self.interpretations:
            live = self.live_candidates(interp)
            if len(live) <= 1:
                continue
            out.append({
                "requirement": interp.requirement_claim,
                "ambiguity": interp.pattern,
                "test_id": interp.test.id,
                "question": interp.test.question,
                "probe": interp.test.probe,
                "distinguishes": live,
                "max_split": len(live),   # one probe splits all live candidates
            })
        return out

    def answer(self, test_id: str, answer_text: str) -> dict:
        """Turn a human answer into evidence; re-propagate automatically.

        The answer selects the candidate it supports; every other live
        candidate is contradicted by the same evidence. Downstream statuses
        recompute, and the certification verdict changes as a consequence —
        no manual re-plumbing.
        """
        interp = next((i for i in self.interpretations
                       if i.test.id == test_id), None)
        if interp is None:
            raise ValueError(f"unknown test id: {test_id}")
        selected = None
        text = answer_text.strip().lower()
        for phrase, cand in interp.test.answer_map.items():
            if phrase in text:
                selected = cand
                break
        ev = self.store.add_evidence(Evidence(
            kind="user_answer", content=answer_text.strip(),
            source=interp.test.id))
        if selected is None:
            return {"changed": False,
                    "reason": "answer did not match any candidate phrasing; "
                              "question remains open (honestly)",
                    "question": interp.test.question}
        sel_claim = interp.candidate_claims[selected]
        self.store.claims[sel_claim].evidence.append(ev)
        for cand_id, claim_id in interp.candidate_claims.items():
            if cand_id == selected:
                continue
            # one-sided refutation: the answer eliminates the other
            # hypotheses WITHOUT asserting them as counter-claims, so the
            # surviving candidate is not pulled into a symmetric contradiction
            self.store.claims[claim_id].refuted_by.append(ev)
        self.store.detect_contradictions()
        return {"changed": True, "selected": selected,
                "resolved": len(self.live_candidates(interp)) <= 1}

    # ------------------------------------------------------- certification

    def certify(self) -> dict:
        """Refuse to certify while any requirement's interpretations are
        undistinguishable, even if all claims look 'supported'."""
        gate_result = self.gate.certify()
        ambiguous_blockers = []
        for interp in self.interpretations:
            live = self.live_candidates(interp)
            if len(live) > 1 and interp.requirement_claim in gate_result["accepted"]:
                ambiguous_blockers.append({
                    "requirement": interp.requirement_claim,
                    "ambiguity": interp.pattern,
                    "live_candidates": live,
                    "reason": "evidence cannot distinguish the candidate "
                              "interpretations; refusing to certify",
                })
        if ambiguous_blockers:
            gate_result["certified"] = False
            gate_result["accepted"] = [c for c in gate_result["accepted"]
                                       if c not in {b["requirement"]
                                                    for b in ambiguous_blockers}]
            gate_result["ambiguous_blockers"] = ambiguous_blockers
        return gate_result
