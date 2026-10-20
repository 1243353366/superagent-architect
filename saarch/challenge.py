"""Constructive resistance: challenge contradictory, underspecified, or
unsound input — and make acceptance an explicit, recorded decision.

The mechanism (not the personality) is:
  1. `detect_challenges` finds contradictions between the five inputs
     (capabilities vs exclusions, capabilities vs guardrails) and flags
     input that is too underspecified to derive a sound spec from.
  2. Gate-level challenges put the project into the CHALLENGED state. It
     will not proceed until the user either edits the inputs or runs
     `saarch spec --accept-assumptions`.
  3. On acceptance, MANDATORY GUARDRAILS WIN: capabilities that directly
     contradict a guardrail or an explicit exclusion are dropped from the
     spec, and the drop is recorded as an assumption. The system never
     silently weakens a guardrail to make a capability easier.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .models import Challenge, ProjectIntake
from .spec_builder import parse_exclusions, slugify

_NETWORK_GUARDRAIL = re.compile(
    r"\b(no|without|never|offline|air[- ]gap(ped)?)\b[^.]{0,40}"
    r"\b(network|internet|external|online|api|cloud|call|connection)", re.IGNORECASE)
_NETWORK_CAPABILITY = re.compile(
    r"\b(sync|cloud|api\b|fetch|pull from|upload|webhook|email|notif|integrat|scrape)", re.IGNORECASE)
_SHARED_CAPABILITY = re.compile(
    r"\b(shar(e|ing)|multi[- ]?tenant|collaborat|team\b|public)", re.IGNORECASE)
_LOCAL_DATA_GUARDRAIL = re.compile(
    r"\b(data stays local|local data|no database|no cloud|no sharing)", re.IGNORECASE)


def _words(text: str) -> set:
    return set(re.findall(r"[a-z][a-z0-9]+", (text or "").lower()))


def detect_challenges(intake: ProjectIntake) -> List[Challenge]:
    challenges: List[Challenge] = []
    caps = intake.normalized_capabilities()
    caps_text = " ".join(caps)
    guard = intake.guardrails or ""
    scope = intake.scope or ""
    exclusions = parse_exclusions(scope)

    # 1. capability vs guardrail: network
    if _NETWORK_GUARDRAIL.search(guard) and _NETWORK_CAPABILITY.search(caps_text):
        offenders = [c for c in caps if _NETWORK_CAPABILITY.search(c)]
        challenges.append(Challenge(
            id="network-guardrail-conflict",
            kind="contradiction",
            severity="gate",
            message="Capabilities require network access, but the mandatory "
                    "guardrails forbid it.",
            evidence="offending capabilities: " + "; ".join(offenders),
            resolution="Drop the network-dependent capabilities (guardrails "
                      "win on acceptance), or relax the guardrail — your "
                      "decision, recorded either way."))

    # 2. capability vs guardrail: data locality / sharing
    if _LOCAL_DATA_GUARDRAIL.search(guard) and _SHARED_CAPABILITY.search(caps_text):
        offenders = [c for c in caps if _SHARED_CAPABILITY.search(c)]
        challenges.append(Challenge(
            id="locality-guardrail-conflict",
            kind="contradiction",
            severity="gate",
            message="Capabilities imply sharing or multi-tenancy, but the "
                    "guardrails require data to stay local.",
            evidence="offending capabilities: " + "; ".join(offenders),
            resolution="Drop the sharing capabilities on acceptance, or relax "
                      "the guardrail."))

    # 3. capability vs explicit exclusion
    for exc in exclusions:
        exc_words = _words(exc)
        for cap in caps:
            overlap = _words(cap) & exc_words - {"and", "the", "with", "for"}
            if overlap:
                challenges.append(Challenge(
                    id=f"exclusion-conflict-{slugify(exc)[:24]}",
                    kind="contradiction",
                    severity="gate",
                    message="A capability directly conflicts with an explicit "
                            "exclusion in the scope.",
                    evidence=f"capability: “{cap}” vs exclusion: “{exc}” "
                             f"(shared terms: {', '.join(sorted(overlap))})",
                    resolution="Accepting drops the excluded capability; or "
                              "remove the exclusion."))

    # 4. underspecified instructions
    instruction_words = re.findall(r"[A-Za-z0-9]+", intake.instructions or "")
    if len(instruction_words) < 4:
        challenges.append(Challenge(
            id="underspecified-instructions",
            kind="underspecified",
            severity="gate",
            message="The instructions are too short to derive a meaningful "
                    "architecture from.",
            evidence=f"instructions contain {len(instruction_words)} word(s)",
            resolution="Rewrite the instructions with at least one concrete "
                      "purpose, or accept and let conservative template "
                      "assumptions stand (recorded)."))

    # 5. vague capabilities (note-level: recorded, never gating)
    vague = [c for c in caps if len(_words(c)) < 2]
    if vague:
        challenges.append(Challenge(
            id="vague-capabilities",
            kind="underspecified",
            severity="note",
            message="Some capabilities are single-word and will map to "
                    "generic resources.",
            evidence="; ".join(vague),
            resolution="Recorded as an assumption; optional to improve."))

    return challenges


def resolve_conflicts(intake: ProjectIntake,
                      challenges: List[Challenge]) -> Tuple[List[str], List[str]]:
    """Apply guardrail priority to accepted challenges.

    Returns (kept_capabilities, dropped_capabilities). Guardrails and
    exclusions always win over capabilities; the caller records the drops.
    """
    caps = intake.normalized_capabilities()
    dropped: List[str] = []
    gate = {c.id for c in challenges if c.severity == "gate"}

    if "network-guardrail-conflict" in gate:
        for cap in list(caps):
            if _NETWORK_CAPABILITY.search(cap):
                caps.remove(cap)
                dropped.append(cap)
    if "locality-guardrail-conflict" in gate:
        for cap in list(caps):
            if _SHARED_CAPABILITY.search(cap):
                caps.remove(cap)
                dropped.append(cap)
    exclusions = parse_exclusions(intake.scope or "")
    for exc in exclusions:
        exc_words = _words(exc)
        for cap in list(caps):
            if _words(cap) & exc_words:
                caps.remove(cap)
                dropped.append(cap)
    return caps, dropped
