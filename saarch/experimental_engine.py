"""The experimental engine: interpretation generation and test generation.

This component is UNTRUSTED by design. It may fail, crash, hallucinate, or
be compromised — and none of those outcomes may grant it authority over the
trusted core (saarch/trusted_core.py). It holds no store, no gate, and no
audit: it sees read-only projections and speaks through a contained
session's propose() channel. Every idea it produces is a Proposal that the
core validates, sanitizes, provenance-stamps, and may promote or reject.

What lives here (the experimental side):
  * interpretation generation   candidates for an ambiguous request
  * test generation              the distinguishing probe per ambiguity
  * hypothesis formation         (future) LLM-generated candidates
  * candidate improvement        (future) re-ranking from failed answers

What deliberately does NOT live here:
  * permissions, containment, audit, rollback, promotion, the gate.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .interpret import active_patterns


class EngineCrash(Exception):
    """Raised by this engine whenever it 'fails' — the core survives."""


class ExperimentalEngine:
    """Pattern-driven interpretation generator behind a contained session."""

    def __init__(self, session):
        self.session = session
        self.generation_count = 0

    def generate_interpretations(self, requirement: dict) -> Optional[str]:
        """Match the request text against known ambiguity patterns and
        propose a candidate batch + distinguishing test through the
        session. Returns a proposal id, or None when nothing matched.

        This method is allowed to be wrong; the core decides what enters
        the ledger and nothing here can promote itself.
        """
        text = str(requirement.get("statement", ""))
        for pattern in active_patterns():
            if pattern.cue.search(text):
                payload = {
                    "requirement": requirement.get("id"),
                    "candidates": [
                        {"id": c.id, "statement": c.statement,
                         "predicate": c.predicate}
                        for c in pattern.candidates],
                    "probe": pattern.test.probe,
                    "question": pattern.test.question,
                    "test_id": pattern.test.id,
                }
                self.generation_count += 1
                return self.session.propose("interpretation_batch", payload)
        return None

    def relay_answer(self, test_id: str, answer_text: str) -> str:
        """Relay a (possibly human) answer. The CORE stamps provenance:
        everything arriving through this channel is engine-provenance,
        capped, and cannot certify alone."""
        return self.session.propose(
            "answer", {"test_id": str(test_id), "answer": str(answer_text)})

    # ----- deliberately crashy paths, used by containment tests -----
    def hallucinate(self, requirement_id: str) -> str:
        """Simulated hallucination: fabricated references, oversized
        payloads, forged authority fields. The core must reject all of it."""
        return self.session.propose("interpretation_batch", {
            "requirement": requirement_id,
            "candidates": [{"id": "H", "statement": "x" * 10_000,
                            "predicate": {"subject": "s", "property": "p",
                                          "value": "v"},
                            "type": "requirement", "status": "accepted"}],
            "probe": "fabricated probe",
            "evidence": ["E999"],          # fabricated evidence reference
            "source": "human", "reliability": 1.0,   # forged provenance
        })

    def crash(self) -> None:
        """Simulated engine crash."""
        raise EngineCrash("experimental engine fault (simulated)")
