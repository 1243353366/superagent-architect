"""The trusted core: the only component with authority over accepted facts.

The system is split in two (see ARCHITECTURE.md):

  TRUSTED CORE (this file)   permissions, containment, audit records,
                             rollback and promotion rules, the gate
  EXPERIMENTAL ENGINE        interpretation generation, test generation,
                             hypothesis formation, candidate improvement
                             (saarch/experimental_engine.py)

The engine may fail, crash, hallucinate, or be compromised. Its failure
must NEVER grant it additional authority over this core, so:

  * the engine receives a contained session: read-only projections of the
    ledger plus a propose() channel. No store, gate, or audit mutators.
  * every proposal is validated, size-capped, provenance-stamped
    ("engine") and reliability-capped by the CORE, never self-declared.
  * promotion happens only through core.promote(), which re-validates
    against the ledger; there is no other write path.
  * the gate refuses to certify a requirement whose resolution rests
    solely on engine-provenance evidence — a hallucinated or malicious
    "answer" can refine hypotheses but can never, by itself, create an
    accepted fact.
  * audit is append-only. Even rollback appends; history is never edited.
  * checkpoints + rollback recover from any admitted-but-later-regretted
    promotion.
"""
from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .claims import (
    BAND_CERTAIN, BAND_CONTESTED, BAND_SUPPORTED, BAND_UNKNOWN_CONFLICTED,
    BAND_UNKNOWN_MISSING, BAND_REFUTED, Claim, ClaimStore, Evidence,
    AcceptanceGate,
)

ENGINE_RELIABILITY_CAP = 0.5     # engine-provenance evidence can never be as
                                 # strong as trusted-channel evidence
HUMAN_RELIABILITY = 0.95
MAX_STATEMENT_CHARS = 500
MAX_PROBE_CHARS = 500
MAX_QUESTION_CHARS = 400
MAX_CANDIDATES = 6
ALLOWED_PREDICATE_KEYS = {"subject", "property", "value", "polarity"}


class CoreError(Exception):
    """A rule of the trusted core was violated (recorded, never fatal)."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime())


def _clean_predicate(pred: Any) -> Optional[dict]:
    if pred is None:
        return None
    if not isinstance(pred, dict):
        return None
    cleaned = {k: pred[k] for k in ALLOWED_PREDICATE_KEYS if k in pred}
    if not {"subject", "property"} <= set(cleaned):
        return None
    cleaned["polarity"] = cleaned.get("polarity", "positive")
    return cleaned


class Proposal:
    """An engine idea waiting for core validation. Carries NO authority."""

    def __init__(self, proposal_id: str, session: "EngineSession",
                 kind: str, payload: dict):
        self.id = proposal_id
        self.engine_id = session.engine_id
        self.kind = kind                      # interpretation_batch | answer
        self.payload = payload                # core-sanitized at intake
        self.status = "pending"               # pending|admitted|rejected
        self.reason = ""

    def to_record(self) -> dict:
        return {"id": self.id, "engine": self.engine_id, "kind": self.kind,
                "status": self.status, "reason": self.reason}


class EngineSession:
    """The contained handle an experimental engine works through.

    Interface audit (test_enforced): only these public methods/attributes
    exist. There is intentionally no path to the store, the gate, the audit
    log, or any mutation of accepted facts.
    """

    def __init__(self, core: "TrustedCore", engine_id: str):
        self._core = core
        self.engine_id = engine_id
        self.failed = False

    # ---- read-only projections (deep copies; the engine cannot mutate) ----
    def read_requests(self) -> List[dict]:
        return copy.deepcopy(self._core._requests_view())

    def read_claims(self) -> List[dict]:
        return copy.deepcopy(self._core._claims_view())

    # ---- the only write channel: proposals, decided by the core ----
    def propose(self, kind: str, payload: dict) -> str:
        if self.failed:
            raise CoreError("session marked failed; open a new session")
        return self._core._submit(self, kind, payload)

    def mark_failed(self, why: str = "") -> None:
        self.failed = True
        self._core._engine_failed(self, why)


class TrustedCore:
    """Ledger + gate + audit + rollback/promotion rules.

    Two input channels exist and they are NOT equal:
      human channel    ingest_request(), record_human_answer()  (trusted)
      engine channel   open_session() -> propose() -> promote() (untrusted)
    """

    def __init__(self, store: Optional[ClaimStore] = None):
        self.store = store or ClaimStore()
        self.gate = AcceptanceGate(self.store)
        self.audit: List[dict] = []          # append-only, never truncated
        self.proposals: Dict[str, Proposal] = {}
        self._checkpoints: Dict[str, dict] = {}
        self._question_index: Dict[str, dict] = {}   # test id -> question meta

    # ------------------------------------------------------------------ audit
    def _record(self, action: str, **fields) -> None:
        entry = {"ts": _now(), "action": action}
        entry.update(fields)
        self.audit.append(entry)

    # ------------------------------------------------------ human channel (trusted)
    def ingest_request(self, text: str, source: str = "human") -> str:
        text = str(text)[:MAX_STATEMENT_CHARS]
        ev_id = self.store.add_evidence(Evidence(
            kind="prompt_excerpt", content=text,
            source=source, reliability=0.98))
        claim_id = self.store.add_claim(Claim(
            id=f"R{len(self.store.claims) + 1}",
            statement=text, type="requirement", evidence=[ev_id],
            predicate=None))
        claim_id = claim_id  # store assigns/validates the id
        self._record("ingest_request", claim=claim_id, chars=len(text))
        return claim_id

    def record_human_answer(self, test_id: str, answer_text: str) -> dict:
        """The trusted channel for human answers (provenance=human)."""
        meta = self._question_index.get(test_id)
        if not meta:
            raise CoreError(f"unknown test id: {test_id}")
        result = self._apply_answer(test_id, meta, str(answer_text),
                                    provenance="human",
                                    reliability=HUMAN_RELIABILITY)
        self._record("human_answer", test=test_id,
                     selected=result.get("selected"))
        return result

    # ------------------------------------------------------ engine channel (contained)
    def open_session(self, engine_id: str = "engine") -> EngineSession:
        session = EngineSession(self, engine_id)
        self._record("session_opened", engine=engine_id)
        return session

    def _submit(self, session: EngineSession, kind: str,
                payload: Any) -> str:
        """Validate + sanitize at intake. The core decides what a proposal
        is allowed to contain; self-declared authority fields are simply
        not read."""
        if not isinstance(payload, dict):
            self._record("proposal_rejected", engine=session.engine_id,
                         kind=str(kind)[:40], reason="payload not an object")
            pid = f"P-{uuid.uuid4().hex[:8]}"
            prop = Proposal(pid, session, str(kind)[:40], {})
            prop.status, prop.reason = "rejected", "payload not an object"
            self.proposals[pid] = prop
            return pid
        pid = f"P-{uuid.uuid4().hex[:8]}"
        prop = Proposal(pid, session, kind, payload)
        # authority fields are never read from the payload; strip them so a
        # compromised engine cannot smuggle provenance or reliability
        for forged in ("source", "provenance", "reliability",
                      "status", "accepted"):
            payload.pop(forged, None)
        reason = (self._validate_answer(payload)
                  if kind == "answer" else self._validate(payload))
        if reason:
            prop.status, prop.reason = "rejected", reason
            self._record("proposal_rejected", engine=session.engine_id,
                         proposal=pid, kind=kind, reason=reason)
        else:
            prop.status = "admitted"
            self._record("proposal_admitted", engine=session.engine_id,
                         proposal=pid, kind=kind)
        self.proposals[pid] = prop
        return pid

    def _validate_answer(self, payload: dict) -> str:
        if not str(payload.get("test_id", "")).strip():
            return "answer must reference a test/question id"
        if str(payload.get("test_id")) not in self._question_index:
            return "answer references an unknown test id"
        ans = payload.get("answer")
        if not ans or not str(ans).strip():
            return "answer text missing"
        if len(str(ans)) > MAX_STATEMENT_CHARS:
            return "answer too long"
        return ""

    @staticmethod
    def _validate(payload: dict) -> str:
        cands = payload.get("candidates")
        if not isinstance(cands, list) or not (1 <= len(cands) <= MAX_CANDIDATES):
            return f"candidates must be a list of 1..{MAX_CANDIDATES}"
        for c in cands:
            if not isinstance(c, dict):
                return "candidate not an object"
            if not str(c.get("id", "")).strip() or \
               not str(c.get("statement", "")).strip():
                return "candidate needs id and statement"
            if len(str(c.get("statement", ""))) > MAX_STATEMENT_CHARS:
                return "candidate statement too long"
            if c.get("type") == "requirement" or c.get("status"):
                return ("candidate attempts authority fields "
                        "(type/status are core-decided)")
            if not _clean_predicate(c.get("predicate")):
                return "candidate predicate must name subject and property"
        probe = payload.get("probe") or payload.get("test_id")
        if not probe or len(str(probe)) > MAX_PROBE_CHARS:
            return "distinguishing probe missing or too long"
        req = payload.get("requirement")
        if not req:
            return "requirement reference missing"
        return ""

    def promote(self, proposal_id: str) -> dict:
        """The ONLY path from engine proposal to ledger state."""
        prop = self.proposals.get(proposal_id)
        if not prop:
            raise CoreError(f"unknown proposal: {proposal_id}")
        if prop.status != "admitted":
            return {"promoted": False, "reason": f"proposal {prop.status}: {prop.reason}"}
        payload = prop.payload
        req_claim = payload.get("requirement")
        if req_claim not in self.store.claims:
            prop.status, prop.reason = "rejected", "requirement claim not in ledger"
            self._record("promotion_refused", proposal=proposal_id,
                         reason=prop.reason)
            return {"promoted": False, "reason": prop.reason}
        cands = []
        for c in payload["candidates"]:
            pred = _clean_predicate(c.get("predicate"))
            cid = self.store.add_claim(Claim(
                id="", statement=str(c["statement"])[:MAX_STATEMENT_CHARS],
                type="interpretation", evidence=[], predicate=pred,
                depends_on=[req_claim]))
            cands.append({"id": str(c["id"])[:32], "claim": cid})
        test_id = str(payload.get("test_id")
                      or f"T-{uuid.uuid4().hex[:8]}")
        self._question_index[test_id] = {
            "probe": str(payload.get("probe"))[:MAX_PROBE_CHARS],
            "question": str(payload.get("question", payload.get("probe", "")))[:MAX_QUESTION_CHARS],
            "requirement": req_claim,
            "candidates": cands,
            "engine": prop.engine_id,
        }
        prop.status = "promoted"
        self._record("promotion_applied", proposal=proposal_id,
                     engine=prop.engine_id, test=test_id,
                     candidates=[c["id"] for c in cands])
        return {"promoted": True, "test_id": test_id, "candidates": cands}

    def open_question(self, test_id: str) -> Optional[str]:
        meta = self._question_index.get(test_id)
        if not meta:
            return None
        live = self._live_candidates(meta)
        if len(live) <= 1:
            return None
        return meta["question"]

    # ------------------------------------------------------ answers & promotion rules
    def _live_candidates(self, meta: dict) -> List[dict]:
        live = []
        for cand in meta["candidates"]:
            if self.store.band(cand["claim"]) in (
                    BAND_UNKNOWN_CONFLICTED, BAND_CONTESTED, BAND_REFUTED):
                continue
            live.append(cand)
        return live

    def _apply_answer(self, test_id: str, meta: dict, answer_text: str,
                      provenance: str, reliability: float) -> dict:
        selected = None
        for cand in meta["candidates"]:
            if cand["id"] in answer_text:
                selected = cand
                break
        if selected is None:
            return {"resolved": False,
                    "reason": "answer names no candidate; question stays open"}
        ev = Evidence(
            kind="user_answer", content=answer_text[:MAX_STATEMENT_CHARS],
            source=provenance,
            reliability=(min(reliability, ENGINE_RELIABILITY_CAP)
                         if provenance == "engine" else reliability))
        ev_id = self.store.add_evidence(ev)
        self.store.claims[selected["claim"]].evidence.append(ev_id)
        for cand in meta["candidates"]:
            if cand is not selected:
                self.store.claims[cand["claim"]].refuted_by.append(ev_id)
        self._record("answer_applied", test=test_id,
                     provenance=provenance, selected=selected["id"])
        return {"resolved": True, "selected": selected["id"],
                "claim": selected["claim"]}

    def submit_engine_answer(self, proposal_id: str) -> dict:
        """Engine-relayed answers enter as ENGINE provenance, capped.
        The gate will not certify on engine-only support (see certify)."""
        prop = self.proposals.get(proposal_id)
        if not prop or prop.status != "admitted":
            return {"resolved": False,
                    "reason": f"proposal {prop.status if prop else 'unknown'}"}
        payload = prop.payload
        if prop.kind != "answer":
            return {"resolved": False, "reason": "proposal is not an answer"}
        meta = self._question_index.get(str(payload.get("test_id", "")))
        if not meta:
            return {"resolved": False, "reason": "unknown test id"}
        return self._apply_answer(str(payload.get("test_id", "")),
                                  meta, str(payload.get("answer", "")),
                                  provenance="engine",
                                  reliability=ENGINE_RELIABILITY_CAP)

    # ------------------------------------------------------ certification (the gate)
    def certify(self) -> dict:
        result = self.gate.certify()
        # promotion rule 1: a requirement whose interpretations are still
        # undistinguished cannot be certified, however 'supported' it looks
        ambiguous = []
        for test_id, meta in self._question_index.items():
            live = self._live_candidates(meta)
            if len(live) > 1 and meta["requirement"] in result.get("accepted", []):
                ambiguous.append({"requirement": meta["requirement"],
                                  "test": test_id,
                                  "live_candidates": [c["id"] for c in live],
                                  "reason": "evidence cannot distinguish the "
                                            "candidate interpretations; "
                                            "refusing to certify"})
        if ambiguous:
            result["certified"] = False
            result["accepted"] = [c for c in result.get("accepted", [])
                                  if c not in {b["requirement"] for b in ambiguous}]
            result["ambiguous_blockers"] = ambiguous
            self._record("certification_refused",
                         reasons=[b["reason"] for b in ambiguous])
        # promotion rule 2: engine-only support cannot certify a requirement.
        # The anchor is the RESOLUTION: if the surviving interpretation's
        # evidence comes solely from the engine channel (a hallucinated or
        # malicious self-answer), the requirement cannot become an accepted
        # fact — the engine's failure grants it no authority.
        engine_only = []
        for meta in self._question_index.values():
            live = self._live_candidates(meta)
            if len(live) != 1:
                continue
            sources = {self.store.evidence[e].source
                       for e in self.store.claims[live[0]["claim"]].evidence
                       if e in self.store.evidence}
            if sources and sources <= {"engine"}:
                engine_only.append(meta["requirement"])
        if engine_only:
            result["certified"] = False
            result["accepted"] = [c for c in result.get("accepted", [])
                                  if c not in engine_only]
            result["engine_only_blockers"] = [
                {"requirement": c,
                 "reason": "resolution rests solely on engine-provenance "
                           "evidence; a trusted human answer or test result "
                           "is required to certify"}
                for c in engine_only]
            self._record("certification_refused",
                         reasons=[b["reason"] for b in result["engine_only_blockers"]])
        return result

    # ------------------------------------------------------ rollback & recovery
    def checkpoint(self) -> str:
        cp_id = f"CP-{uuid.uuid4().hex[:8]}"
        self._checkpoints[cp_id] = {
            "claims": copy.deepcopy(self.store.claims),
            "evidence": copy.deepcopy(self.store.evidence),
            "questions": copy.deepcopy(self._question_index),
        }
        self._record("checkpoint", id=cp_id,
                     claims=len(self.store.claims))
        return cp_id

    def rollback(self, checkpoint_id: str) -> bool:
        cp = self._checkpoints.get(checkpoint_id)
        if not cp:
            return False
        self.store.claims = copy.deepcopy(cp["claims"])
        self.store.evidence = copy.deepcopy(cp["evidence"])
        self._question_index = copy.deepcopy(cp["questions"])
        self._record("rollback", checkpoint=checkpoint_id,
                     claims_restored=len(self.store.claims))
        return True   # audit is NEVER truncated — rollback appends

    # ------------------------------------------------------ engine failure handling
    def _engine_failed(self, session: EngineSession, why: str) -> None:
        self._record("engine_failed", engine=session.engine_id,
                     reason=str(why)[:200])

    # ------------------------------------------------------ read-only views
    def _requests_view(self) -> List[dict]:
        return [{"id": c.id, "statement": c.statement}
                for c in self.store.claims.values()
                if c.type == "requirement"]

    def _claims_view(self) -> List[dict]:
        return [{"id": c.id, "type": c.type,
                 "band": self.store.band(c.id),
                 "evidence_count": len(c.evidence)}
                for c in self.store.claims.values()]
