#!/usr/bin/env python3
"""Zombie lifecycle runner: raise from lab evidence, minimize, retire.

Dispositions are written here, in code review, on purpose — retiring a
zombie without a documented regression test raises.

    raise   — load minimized repros from results/, record zombies (walking)
    retire  — apply the documented dispositions (fix + regression test)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saarch.zombies import ZombieLedger  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
LEDGER = RESULTS / "zombies.json"

TITLES = {
    "D1_engine_only_or_empty_cert":
        "Engine hijacks a live question via test-id collision and the "
        "requirement certifies on zero evidence",
    "D3_question_hijack":
        "Engine redefines a question after a human answer was recorded "
        "against it (question immutability violated)",
    "D5_wrong_resolution":
        "Trusted-channel answer misparse: 'Absolutely ... cancel anytime "
        "after commitment' resolved to candidate A by substring match",
}
# documented dispositions — retire is refused without these
DISPOSITIONS = {
    "D1_engine_only_or_empty_cert": {
        "regression_test":
            "tests.test_zombie_regressions.ZombieRegressions.test_z1",
        "fix_note": "promote() refuses test-id collisions (questions are "
                    "immutable once created); certify() blocks resolutions "
                    "with empty evidence, not just engine-only evidence",
    },
    "D3_question_hijack": {
        "regression_test":
            "tests.test_zombie_regressions.ZombieRegressions.test_z1",
        "fix_note": "same fix as D1 — the core refuses to redefine an "
                    "existing question id",
    },
    "D5_wrong_resolution": {
        "regression_test":
            "tests.test_zombie_regressions.ZombieRegressions.test_z3",
        "fix_note": "answer matching uses whole-token candidate ids "
                    "(word boundaries), not substring containment; "
                    "ambiguous answers leave the question open instead of "
                    "silently misparsing",
    },
}


def load_ledger() -> ZombieLedger:
    L = ZombieLedger()
    if LEDGER.is_file():
        recs = json.loads(LEDGER.read_text())
        for r in recs:
            z = L.raise_zombie(r["record"]["title"],
                               r["record"]["discovered"],
                               r["record"]["repro"],
                               r["record"]["original"])
            L.minimize(z.id, z.repro,
                       z.discovered.get("retained_breach", ""))
            if r["record"]["status"] == "fixed":
                L.mark_fixed(z.id, r["record"]["disposition"]["fix_summary"])
            if r["record"]["status"] == "retired":
                L.mark_fixed(z.id, r["record"]["disposition"]["fix_note"])
                L.retire(z.id, r["record"]["disposition"]["regression_test"],
                         r["record"]["disposition"]["fix_note"],
                         r["record"]["disposition"]["retired_at"])
    return L


def main(action: str) -> int:
    L = load_ledger()
    if action == "raise":
        for det, title in TITLES.items():
            f = RESULTS / f"minimized-{det}.json"
            if not f.is_file():
                continue
            d = json.loads(f.read_text())
            if any(z.title == title for z in L.zombies):
                continue
            z = L.raise_zombie(title,
                               {"detector": det, "seed": d["seed"]},
                               {"ops": d["minimized"]},
                               {"ops": d["original"],
                                "log": "results/bypass-experiments.jsonl"})
            print(f"raised {z.id}: {title[:60]}...")
    elif action == "retire":
        today = "2026-10-21"
        for z in L.zombies:
            if z.status != "minimized":
                continue
            det = z.discovered["detector"]
            disp = DISPOSITIONS[det]
            L.mark_fixed(z.id, disp["fix_note"])
            L.retire(z.id, disp["regression_test"], disp["fix_note"], today)
            print(f"retired {z.id} ({det})")
    else:
        print("usage: zombie_ledger.py [raise|retire]")
        return 2
    LEDGER.write_text(L.to_json(), encoding="utf-8")
    problems = L.verify()
    print("ledger integrity:", "OK" if not problems else problems)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
