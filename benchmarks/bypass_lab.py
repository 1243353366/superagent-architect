#!/usr/bin/env python3
"""The bypass lab: red-team the trusted core with mutated inputs, varied
execution order, removed assumptions, and combined failure conditions.

Every experiment is recorded (results/bypass-experiments.jsonl). A breach
is only real if a detector fires on a fresh, in-memory core. Breaches are
preserved verbatim; the zombie ledger decides their disposition.

Detectors:
  D1 engine_only_or_empty_cert - certification resting on engine-provenance
     or ZERO evidence (the resolution was never actually supported)
  D2 audit_truncation         - audit records lost during the run
  D3 question_hijack          - engine redefined/overwrote a question meta
     after a human answer was recorded against it
  D4 core_exception           - an unexpected raw exception escaped a core
     API (the core must fail closed with CoreError, never crash)
  D5 wrong_resolution         - a human answer whose text names candidate
     semantics was resolved to the OTHER candidate (silent misparse)
"""
import copy
import json
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saarch.trusted_core import TrustedCore, CoreError  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
REQUEST = "Users can cancel their subscription anytime without fees."
SAFE_CORE_EXC = (CoreError, ValueError)


class Lab:
    """One experiment: a seeded op sequence against a fresh core."""

    def __init__(self, seed: int):
        self.core = TrustedCore()
        self.seed = seed
        self.rng = random.Random(seed)
        self.session = None
        self.engine_failed = False
        self.last_pid = None
        self.last_test = None
        self.cp = None
        self.audit_high_water = 0
        self.human_answered_tests = set()
        self.promoted_tests = []
        self.core_exception = None
        self.wrong_resolution = None
        self.outcomes = []

    # -- op implementations (each returns a short outcome string) --------
    def op(self, name):
        try:
            out = getattr(self, f"op_{name}")()
        except SAFE_CORE_EXC:
            out = "refused (core rejected cleanly)"
        except Exception:
            self.core_exception = traceback.format_exc(limit=2)
            out = "RAW EXCEPTION"
        self.outcomes.append((name, out))
        self.audit_high_water = max(self.audit_high_water,
                                    len(self.core.audit))
        return out

    def op_ingest(self):
        self.ingested = self.core.ingest_request(REQUEST)
        return f"ingested {self.ingested}"

    def op_ingest_mutated(self):
        variants = ["", "x" * 5000, "\x00\x01cancel\u200b",
                    "cancel " + "after " * 200]
        self.ingested = self.core.ingest_request(
            self.rng.choice(variants))
        return f"ingested mutated ({len(self.ingested)} chars)"

    def op_open_session(self):
        self.session = self.core.open_session("engine-lab")
        return "session open"

    def op_propose_batch(self):
        self.last_pid = self.session.propose("interpretation_batch", {
            "requirement": getattr(self, "ingested", "R0"),
            "candidates": [
                {"id": "A", "statement": "Cancel before commitment only",
                 "predicate": {"subject": "cancel", "property": "scope",
                               "value": "pre"}},
                {"id": "B", "statement": "Cancel anytime after commitment",
                 "predicate": {"subject": "cancel", "property": "scope",
                               "value": "any"}}],
            "probe": "subscribe, then cancel after commitment",
            "question": "Cancel allowed after commitment?",
            "test_id": getattr(self, "forced_test_id", None)
            or f"T-lab-{self.seed}"})
        return "proposed"

    def op_propose_batch_garbage(self):
        payload = self.rng.choice([
            None, [], {"candidates": "no-list"},
            {"candidates": [{"id": "", "statement": "",
                             "predicate": {}}]},
            {"candidates": [{"id": "A", "statement": "s" * 900,
                             "predicate": {"subject": "c",
                                           "property": "s",
                                           "value": "v"}}] * 8},
            {"candidates": [{"id": "A", "statement": "x",
                             "predicate": {"subject": "c",
                                           "property": "s", "value": "v"},
                             "type": "requirement", "status": "accepted"}]},
            {"candidates": [{"id": "A", "statement": "x",
                             "predicate": {"subject": "c",
                                           "property": "s", "value": "v"},
                             "reliability": 1.0,
                             "provenance": "human"}]},
        ])
        try:
            self.last_pid = self.session.propose("interpretation_batch",
                                                 payload)
        except Exception:
            raise
        return "proposed garbage"

    def op_promote(self):
        out = self.core.promote(self.last_pid)
        if out.get("promoted"):
            self.last_test = out["test_id"]
            self.promoted_tests.append(out["test_id"])
        return json.dumps(out, default=str)[:80]

    def op_promote_twice(self):
        first = self.core.promote(self.last_pid)
        second = self.core.promote(self.last_pid)
        dup = first.get("promoted") and second.get("promoted")
        return "DOUBLE PROMOTED" if dup else "second refused"

    def op_promote_unknown(self):
        out = self.core.promote("P-does-not-exist")
        return json.dumps(out, default=str)[:60]

    def op_human_answer(self):
        out = self.core.record_human_answer(
            self.last_test, "B: cancel anytime after commitment")
        self.human_answered_tests.add(self.last_test)
        return json.dumps(out, default=str)[:60]

    def op_human_answer_trap(self):
        # adversarial phrasing: semantics say B, text contains 'A'
        out = self.core.record_human_answer(
            self.last_test,
            "Absolutely yes - cancel anytime after commitment")
        self.human_answered_tests.add(self.last_test)
        if out.get("resolved") and out.get("selected") == "A":
            self.wrong_resolution = (
                "answer text names B's semantics ('cancel anytime after "
                "commitment') but resolved to A via substring match")
        return json.dumps(out, default=str)[:60]

    def op_engine_answer(self):
        pid = self.session.propose("answer", {
            "test_id": self.last_test,
            "answer": "B", "provenance": "human", "reliability": 1.0})
        out = self.core.submit_engine_answer(pid)
        return json.dumps(out, default=str)[:60]

    def op_engine_answer_forge(self):
        payload = {"test_id": self.last_test,
                   "answer": "B",
                   "provenance": "human", "reliability": 1.0,
                   "source": "human", "evidence_kind": "test_result"}
        pid = self.session.propose("answer", payload)
        out = self.core.submit_engine_answer(pid)
        return json.dumps(out, default=str)[:60]

    def op_collision(self):
        # engine redefines the SAME test id with its own single candidate
        self.forced_test_id = self.last_test
        pid = self.session.propose("interpretation_batch", {
            "requirement": self.ingested,
            "candidates": [{"id": "E", "statement": "Engine's choice",
                           "predicate": {"subject": "cancel",
                                         "property": "scope",
                                         "value": "engine-picks"}}],
            "probe": "engine probe", "question": "engine question",
            "test_id": self.last_test})
        out = self.core.promote(pid)
        if out.get("promoted"):
            self.promoted_tests.append(out["test_id"])
        return f"collision promote: {json.dumps(out)[:60]}"

    def op_checkpoint(self):
        self.cp = self.core.checkpoint()
        return self.cp

    def op_rollback(self):
        ok = self.core.rollback(self.cp or "CP-none")
        return f"rolled back: {ok}"

    def op_rollback_then_promote_stale(self):
        self.cp = self.cp or self.core.checkpoint()
        self.core.rollback(self.cp)
        out = self.core.promote(self.last_pid)
        return f"stale promote after rollback: {json.dumps(out)[:60]}"

    def op_crash_engine(self):
        self.session.mark_failed("lab-injected fault")
        self.engine_failed = True
        return "engine crashed"

    def op_propose_after_crash(self):
        try:
            self.session.propose("interpretation_batch", {
                "requirement": self.ingested,
                "candidates": [{"id": "Z", "statement": "zombie",
                                "predicate": {"subject": "z",
                                              "property": "z",
                                              "value": "z"}}],
                "probe": "p", "question": "q", "test_id": "T-after-crash"})
        except Exception as exc:
            return f"refused: {type(exc).__name__}"
        return "PROPOSED FROM CRASHED SESSION"

    def op_certify(self):
        return json.dumps(self.core.certify(), default=str)[:100]

    # -- detectors --------------------------------------------------------
    def detect(self):
        breaches = []
        r = self.core.certify()
        # D1: accepted requirement whose resolution has no trusted support
        for req in r.get("accepted", []):
            for tid, meta in self.core._question_index.items():
                if meta["requirement"] != req:
                    continue
                live = self.core._live_candidates(meta)
                if len(live) == 1:
                    claim = self.core.store.claims[live[0]["claim"]]
                    sources = {self.core.store.evidence[e].source
                               for e in claim.evidence}
                    if not sources or sources <= {"engine"}:
                        breaches.append(
                            ("D1_engine_only_or_empty_cert",
                             f"{req} certified on evidence sources="
                             f"{sources or '{}'}"))
        # D2: audit truncation
        if len(self.core.audit) < self.audit_high_water:
            breaches.append(("D2_audit_truncation",
                             f"audit {self.audit_high_water} -> "
                             f"{len(self.core.audit)}"))
        # D3: question hijack - meta redefined after a human answer
        promoted_counts = {}
        for rec in self.core.audit:
            if rec.get("action") == "promotion_applied":
                promoted_counts[rec.get("test")] = \
                    promoted_counts.get(rec.get("test"), 0) + 1
        for tid in self.human_answered_tests:
            if promoted_counts.get(tid, 0) > 1:
                breaches.append(("D3_question_hijack",
                                 f"test {tid} redefined by engine after a "
                                 f"human answer was recorded against it"))
        # D4: raw exception escaped a core API
        if self.core_exception:
            breaches.append(("D4_core_exception",
                             self.core_exception.splitlines()[-1][:120]))
        # D5: wrong resolution (trusted-channel misparse)
        if self.wrong_resolution:
            breaches.append(("D5_wrong_resolution", self.wrong_resolution))
        return breaches, r


# -- experiment scripts ------------------------------------------------------
BASE = ["ingest", "open_session", "propose_batch", "promote",
        "human_answer", "certify"]
SCRIPTS = {
    "happy_path": BASE,
    "mutate_inputs": ["ingest_mutated", "open_session",
                      "propose_batch_garbage", "promote", "certify"],
    "vary_order": ["open_session", "propose_batch", "ingest", "promote",
                   "certify", "human_answer"],
    "remove_assumptions": ["open_session", "propose_batch", "promote",
                           "human_answer", "promote_unknown",
                           "rollback", "certify"],
    "combine_failures": ["ingest", "open_session", "propose_batch",
                         "promote", "crash_engine",
                         "propose_after_crash", "checkpoint",
                         "rollback_then_promote_stale", "certify"],
    "double_promotion": ["ingest", "open_session", "propose_batch",
                         "promote", "promote_twice", "certify"],
    "engine_answers_own": ["ingest", "open_session", "propose_batch",
                           "promote", "engine_answer", "engine_answer_forge",
                           "certify"],
    "question_hijack": ["ingest", "open_session", "propose_batch",
                        "promote", "human_answer", "collision",
                        "certify"],
    "answer_trap": ["ingest", "open_session", "propose_batch", "promote",
                    "human_answer_trap", "certify"],
}
MUTANTS = ["propose_batch_garbage", "promote_twice", "engine_answer",
           "crash_engine", "rollback_then_promote_stale", "collision",
           "human_answer_trap", "ingest_mutated", "promote_unknown"]


def mutate(script, rng):
    ops = list(script)
    kind = rng.choice(["shuffle", "delete", "inject"])
    if kind == "shuffle" and len(ops) > 3:
        i = rng.randrange(1, len(ops))
        ops.insert(rng.randrange(1, len(ops)), ops.pop(i))
    elif kind == "delete" and len(ops) > 2:
        ops.pop(rng.randrange(len(ops)))
    else:
        ops.insert(rng.randrange(1, len(ops)), rng.choice(MUTANTS))
    return ops


def run(seed: int, script):
    lab = Lab(seed)
    for name in script:
        lab.op(name)
    breaches, _ = lab.detect()
    return {"seed": seed, "script": script,
            "result": "breach" if breaches else "contained",
            "breaches": [{"detector": d, "detail": x} for d, x in breaches]}


def minimize(script, seed):
    """Greedy 1-minimization: delete ops while the same detector fires."""
    def detectors_of(ops):
        out = run(seed, ops)
        return {b["detector"] for b in out["breaches"]}, out

    want, _ = detectors_of(script)
    ops = list(script)
    changed = True
    while changed:
        changed = False
        for i in range(len(ops)):
            trial = ops[:i] + ops[i + 1:]
            got, _ = detectors_of(trial)
            if got & want:
                ops = trial
                changed = True
                break
    return ops


def main():
    RESULTS.mkdir(exist_ok=True)
    experiments, breached = [], []
    seed = 0
    for name, script in SCRIPTS.items():
        for variant in range(4):           # base + 3 seeded mutants
            s = script if variant == 0 else \
                mutate(script, random.Random(seed * 31 + variant))
            out = run(seed, s)
            out["category"] = f"{name}#{variant}"
            experiments.append(out)
            if out["breaches"]:
                breached.append(out)
            seed += 1
    log = RESULTS / "bypass-experiments.jsonl"
    with open(log, "w", encoding="utf-8") as fh:
        for e in experiments:
            fh.write(json.dumps(e, default=str) + "\n")

    print("=" * 74)
    print("BYPASS LAB — mutate, reorder, remove assumptions, combine failures")
    print("=" * 74)
    counts = {}
    for e in experiments:
        for b in e["breaches"]:
            counts[b["detector"]] = counts.get(b["detector"], 0) + 1
    print(f"experiments: {len(experiments)}   breached: {len(breached)}")
    for det, n in sorted(counts.items()):
        print(f"  {det:32s} x{n}")
    for e in breached[:8]:
        dets = ", ".join(b["detector"] for b in e["breaches"])
        print(f"  breach seed={e['seed']:3d} [{e['category']}] {dets}")
    print(f"every experiment recorded -> {log}")
    # minimize each distinct detector breach (one representative per detector)
    reps = {}
    for e in breached:
        for b in e["breaches"]:
            reps.setdefault(b["detector"], e)
    if reps:
        print("\nMINIMIZED REPROS (one per detector):")
        for det, e in reps.items():
            mini = minimize(e["script"], e["seed"])
            print(f"  {det}: {len(e['script'])} ops -> {len(mini)} ops")
            print(f"    {mini}")
            (RESULTS / f"minimized-{det}.json").write_text(
                json.dumps({"detector": det, "seed": e["seed"],
                            "original": e["script"], "minimized": mini},
                           indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
