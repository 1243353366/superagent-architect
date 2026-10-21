"""Release gate — the F-GATE-CHAIN lesson made structural.

During the 2026-10-21 delivery the claim check ran, failed, and the
commit shipped anyway: the operator placed git add outside the
check's && chain. The gate existed but was not a precondition. This
script is the Farmer answer: the gate marker must be present in the
commit message, enforced by CI — the one place the operator cannot
skip. Commits that lack the marker fail CI with the incident named.
"""
import subprocess
import sys

MARKER = "claim_matches_state"


def gate_ok(commit_message: str):
    """Returns (allowed, reason). A commit whose message was not
    produced through the claim check is refused — fail closed."""
    if MARKER not in commit_message:
        return False, ("commit message lacks the release-gate marker "
                       f"'{MARKER}' — the claim check was not run as "
                       "a precondition (F-GATE-CHAIN, FWBC-007); "
                       "rerun the claim check and recommit through "
                       "the gate")
    return True, ("release gate: claim-check marker present "
                  "(F-GATE-CHAIN capability C-gate-as-precondition)")


def main() -> int:
    msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%B"],
        capture_output=True, text=True).stdout
    ok, why = gate_ok(msg)
    print(why)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
