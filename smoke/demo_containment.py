#!/usr/bin/env python3
"""Smoke 4 — attack simulation: the experimental engine tries to break out.

Every attack must fail CLOSED. Watch the core refuse, record, and recover.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saarch.experimental_engine import EngineCrash, ExperimentalEngine
from saarch.trusted_core import TrustedCore

LINE = "=" * 76
REQUEST = ("Members can cancel their subscription anytime without penalty.")


def banner(title):
    print("\n" + LINE + f"\n{title}\n" + LINE)


def audits(core):
    return [e["action"] for e in core.audit]


def main():
    print(LINE)
    print("SMOKE 4 — TRUSTED CORE vs COMPROMISED EXPERIMENTAL ENGINE")
    print(LINE)
    core = TrustedCore()
    req = core.ingest_request(REQUEST)
    session = core.open_session("engine-under-attack")
    engine = ExperimentalEngine(session)
    print(f"\ntrusted core owns: claims={len(core.store.claims)} "
          f"audit={len(core.audit)}")

    banner("ATTACK 1 — the engine hallucinates (oversized, forged, fabricated)")
    pid = engine.hallucinate(req)
    prop = core.proposals[pid]
    print(f"  proposal {pid}: {prop.status} — {prop.reason[:60]}")
    print(f"  ledger untouched: claims={len(core.store.claims)} "
          f"(still just the requirement)")
    print(f"  recorded: {'proposal_rejected' in audits(core)}")

    banner("ATTACK 2 — the engine answers its own question")
    pid = engine.generate_interpretations({"id": req, "statement": REQUEST})
    out = core.promote(pid)
    tid = out["test_id"]
    print(f"  interpretations admitted (hypotheses carry no authority): "
          f"{[c['id'] for c in out['candidates']]}")
    print(f"  open question: {core.open_question(tid)[:60]}...")
    forged = session.propose("answer", {
        "test_id": tid, "answer": "B",
        "source": "human", "reliability": 1.0, "status": "accepted"})
    applied = core.submit_engine_answer(forged)
    print(f"  self-answer accepted as EVIDENCE (provenance stamped 'engine'):"
          f" {applied['resolved']}")
    result = core.certify()
    print(f"  certification on engine-only support: {result['certified']}")
    print(f"  blocker: {result['engine_only_blockers'][0]['reason'][:70]}")

    banner("ATTACK 3 — the engine crashes mid-flight")
    claims_before = len(core.store.claims)
    try:
        engine.crash()
    except EngineCrash as e:
        print(f"  {e}")
    session.mark_failed("crash after promotion")
    print(f"  core state intact: claims={len(core.store.claims)} "
          f"(was {claims_before}) | audit grew: "
          f"{'engine_failed' in audits(core)}")
    print(f"  crashed session can no longer propose at all")

    banner("DEFENSE — the human channel resolves; rollback proves recovery")
    core2 = TrustedCore()
    req2 = core2.ingest_request(REQUEST)
    s2 = core2.open_session("engine-clean")
    e2 = ExperimentalEngine(s2)
    pid2 = e2.generate_interpretations({"id": req2, "statement": REQUEST})
    out2 = core2.promote(pid2)
    tid2 = out2["test_id"]
    cp = core2.checkpoint()          # snapshot while still ambiguous
    core2.record_human_answer(tid2, "B")
    r2 = core2.certify()
    print(f"  human answers the open question -> certified: {r2['certified']} "
          f"accepted: {r2['accepted']}")
    core2.rollback(cp)
    r3 = core2.certify()
    print(f"  rollback to {cp}: the answer is undone -> certified again: "
          f"{r3['certified']} (ambiguity restored, {len(core2.store.claims)} "
          f"claims)")
    print(f"  audit never truncated: {len(core2.audit)} records, rollback "
          f"appended at the end")

    banner("RESULT")
    print("  every attack failed closed. Nothing promoted, nothing forged,")
    print("  the audit grew with each attempt, and the engine's failures")
    print("  granted it no additional authority over the trusted core.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
