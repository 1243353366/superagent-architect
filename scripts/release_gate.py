"""Release gate — the F-GATE-CHAIN lesson made structural.

During the 2026-10-21 delivery the claim check ran, failed, and the
commit shipped anyway: the operator placed git add outside the
check's && chain. The gate existed but was not a precondition. This
script is the Farmer answer, and it fails closed on every known
bypass class:

- alternate entry points / missing attestation: the commit message
  must contain the marker 'claim_matches_state' as a whole word;
- skipped steps: the CI workflow must still run the gate — removing
  the step from ci.yml is a bypass, not a fix, and the gate detects
  it;
- misleading success signals: the marker alone is an ATTESTATION.
  The committed release-claims file is the PROOF: the gate re-runs
  claim_matches_state against the repository, so a pasted marker
  with stale or false claims fails.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
CLAIMS = ROOT / "benchmarks/results/release-claims.json"
MARKER = "claim_matches_state"


def gate_ok(commit_message, workflow_text=None, claims_path=CLAIMS):
    """Returns (allowed, reason). Fail closed on every known bypass."""
    if not re.search(r"\b" + MARKER + r"\b",
                     commit_message or ""):
        return False, ("commit message lacks the release-gate marker "
                       f"'{MARKER}' — the claim check was not run as "
                       "a precondition (F-GATE-CHAIN, FWBC-007); "
                       "rerun the claim check and recommit through "
                       "the gate")
    if workflow_text is None:
        workflow_text = (WORKFLOW.read_text()
                         if WORKFLOW.exists() else None)
    if workflow_text is None or "release_gate.py" not in workflow_text:
        return False, ("ci.yml no longer runs the release gate — "
                       "removing the check from CI is a bypass, not "
                       "a fix (F-GATE-CHAIN)")
    if claims_path is None or not Path(claims_path).exists():
        return False, ("release-claims file missing — the gate fails "
                       "closed rather than run on trust (F-GATE-CHAIN)"
                       )
    try:
        from saarch.patching import claim_matches_state
        claim_matches_state(json.loads(
            Path(claims_path).read_text()))
    except Exception as exc:
        return False, ("claims re-verification failed — the marker "
                       f"is attestation, the claims are proof: {exc}")
    return True, ("release gate: marker present as a whole word, "
                  "workflow integrity intact, claims re-verified "
                  "against the repository (F-GATE-CHAIN capability "
                  "C-gate-as-precondition)")


def main() -> int:
    msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%B"],
        capture_output=True, text=True).stdout
    ok, why = gate_ok(msg)
    print(why)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
