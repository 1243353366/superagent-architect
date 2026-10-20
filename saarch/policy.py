"""Principled refusal: prohibited work is blocked by code, not by manners.

This is a tool-level guardrail. `screen_intake` runs at intake and again
before specification; a refusal is a hard, non-overridable gate — there is no
flag that bypasses it, and the reason is recorded in the audit trail and the
project state (state = REFUSED, exit code 3).

Three behaviors are deliberately separate in this codebase:

  REFUSED        policy.py      prohibited work — never proceeds, never negotiable
  CHALLENGED     challenge.py  contradictory/underspecified input — proceeds only
                               with explicit recorded acceptance; guardrails win
  BLOCKED        verifier.py   honest failure — diagnosis + proposed better
                               specification instead of repeating the same attempt
"""
from __future__ import annotations

import re
from typing import List

from .models import ProjectIntake

# (pattern, reason) — screened against instructions and capabilities.
PROHIBITED_PATTERNS = [
    (r"\bkey\s?log(ger|ging)?\b|\bkeystroke\s+(capture|log)", "keylogger / keystroke capture tooling"),
    (r"\b(steal|exfiltrate|harvest|scrape)\b[^.]{0,60}\b(password|credential|token|cookie|session)", "credential theft or exfiltration"),
    (r"\b(backdoor|botnet|rootkit|trojan|ransomware|malware|spyware|stalkerware)\b", "malware-class software"),
    (r"\b(crack|bypass|defeat)\b[^.]{0,40}\b(licen[cs]e|activation|paywall|drm|copy protection)", "license/DRM circumvention"),
    (r"\b(bypass|defeat|evade)\b[^.]{0,40}\b(authentication|2fa|mfa|captcha|anti-?virus|firewall|intrusion)", "security-control evasion"),
    (r"\b(phishing|spoof(ed|ing)?)\b[^.]{0,40}\b(login|bank|email|identity|page|site)", "phishing infrastructure"),
    (r"\b(d)do(s|s attack|s tool)\b|\bdenial[- ]of[- ]service\b", "denial-of-service tooling"),
    (r"\b(brute[- ]?forc(e|ing))\b[^.]{0,40}\b(account|login|password|bank)", "brute-force attack tooling"),
    (r"\b(track|monitor|spy)\b[^.]{0,50}\bwithout (their|his|her|user|owner)['s]* (knowledge|consent|notice)", "covert surveillance of another person"),
    (r"\b(silently|secretly|covertly)\b[^.]{0,40}\b(install|deploy)\b[^.]{0,40}\b(someone|another|his|her|their)", "covert deployment against another person"),
    (r"\b(crypto(l|currency))\b[^.]{0,30}\b(miner|jack|mining)\b", "cryptomining/jacking tooling"),
]


def screen_text(text: str) -> List[str]:
    """Return human-readable policy violations found in `text`."""
    findings = []
    for pattern, reason in PROHIBITED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            findings.append(reason)
    return findings


def screen_intake(intake: ProjectIntake) -> List[str]:
    """Screen the user-supplied inputs. Any finding means: refuse, categorically.

    Note the asymmetry with challenge.py: contradiction and vagueness can be
    accepted and recorded; prohibited work cannot. There is no
    `--accept-assumptions` path past this function, by design.
    """
    combined = " ".join(
        [intake.instructions or ""]
        + list(intake.capabilities or []))
    return screen_text(combined)
