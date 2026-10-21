"""The four deterministic policies compared on the dial-in corpus.

first_guess          - 'just pick the common interpretation' (no questions,
                      no structure). The default agent behavior.
generic_clarify      - simple uncertainty tracking: a boolean 'ambiguous'
                      flag per requirement and a generic 'what did you
                      mean?' per flagged item. No candidate model, no
                      interaction structure.
confirmation_testing - conventional test generation: tests written from the
                      first guess; they confirm the chosen behavior and so
                      cannot distinguish candidates. They pass even when
                      the guess is wrong.
proposed             - the claims layer: candidate interpretations, a
                      distinguishing question per ambiguity, the assumption
                      graph, and max-split question ordering (assumption
                      questions first when they collapse dependents).

All four face the same truthful oracle. Cost = questions asked + answer
words the user must type. Correctness = resolutions matching gold, and
certification only when everything actually matches gold.
"""
from __future__ import annotations

from typing import Dict, List

import dial_in_corpus as C


def _cost(questions: List[Dict], answers: List[Dict]) -> Dict:
    return {
        "questions": len(questions),
        "answer_words": sum(len(a["answer"].split()) for a in answers),
    }


def first_guess(item: Dict) -> Dict:
    """Pick each ambiguity's first candidate. Never asks. Always certifies."""
    resolutions, false_resolutions = {}, []
    for aspect, spec in item["ambiguities"].items():
        choice = spec["candidates"][0]
        resolutions[aspect] = choice
        if choice != spec["gold"]:
            false_resolutions.append(aspect)
    return {"policy": "first_guess", **_cost([], []), "probes": 0,
            "resolutions": resolutions,
            "false_resolutions": false_resolutions,
            "unresolved": [], "certified": True,
            "false_certifications": len(false_resolutions)}


def generic_clarify(item: Dict) -> Dict:
    """One generic question per ambiguity, fixed order; wordy oracle answers."""
    questions, answers, resolutions = [], [], {}
    for aspect in item["ambiguities"]:
        questions.append({"kind": "generic", "aspect": aspect,
                          "text": f"Please clarify: what did you mean by "
                                  f"{aspect.replace('-', ' ')}?"})
        ans = C.generic_answer(item, aspect,
                                questions[-1]["text"])
        answers.append(ans)
        if ans["aspect"] in item["ambiguities"]:
            resolutions[ans["aspect"]] = item["ambiguities"][ans["aspect"]]["gold"]
    false = [a for a, spec in item["ambiguities"].items()
             if resolutions[a] != spec["gold"]]
    return {"policy": "generic_clarify", **_cost(questions, answers),
            "probes": 0, "resolutions": resolutions,
            "false_resolutions": false, "unresolved": [],
            "certified": True, "false_certifications": len(false)}


def confirmation_testing(item: Dict) -> Dict:
    """Tests written from the first guess: they assert the guess, so they
    pass regardless of gold. They distinguish nothing; the wrongness is
    discovered post hoc by a human, not by the tests."""
    run = first_guess(item)
    probes = len(item["ambiguities"])          # one test per ambiguity
    distinguishing = 0                        # tests that can eliminate
                                             # a live candidate: none
    run.update({
        "policy": "confirmation_testing", "probes": probes,
        "distinguishing_probes": distinguishing,
        "tests_all_passed": True,
        "note": ("every generated test passed although "
                 f"{len(run['false_resolutions'])} requirement(s) were "
                 "wrong; tests confirmed the guess instead of "
                 "distinguishing candidates"),
    })
    return run


def proposed(item: Dict) -> Dict:
    """Claims layer: ask the assumption question first when its resolution
    collapses dependents (max expected split), then distinguishing
    questions for whatever remains live. Certify only when fully resolved;
    answers are minimal because the questions are distinguishing."""
    questions, answers = [], []
    resolutions: Dict[str, str] = {}
    collapsed: Dict[str, str] = {}

    # 1. assumption questions whose collapse set is non-empty, biggest first
    assumptions = [(name, spec) for name, spec in
                   item.get("assumptions", {}).items()
                   if spec.get("collapses")]
    for name, spec in sorted(assumptions,
                            key=lambda kv: -len(kv[1]["collapses"])):
        questions.append({"kind": "assumption", "aspect": name,
                          "text": spec["question"]})
        ans = C.oracle_answer(item, name, spec["question"])
        answers.append(ans)
        for dep, val in ans["collapses"].items():
            collapsed[dep] = val

    # 2. distinguishing questions for ambiguities still live
    for aspect, spec in item["ambiguities"].items():
        if aspect in collapsed:
            resolutions[aspect] = collapsed[aspect]
            continue
        questions.append({"kind": "distinguishing", "aspect": aspect,
                          "text": spec["question"]})
        ans = C.oracle_answer(item, aspect, spec["question"])
        answers.append(ans)
        resolutions[aspect] = ans["answer"]

    false = [a for a, spec in item["ambiguities"].items()
             if resolutions.get(a) != spec["gold"]]
    unresolved = [a for a in item["ambiguities"]
                  if resolutions.get(a) is None]
    certified = (not unresolved) and (not false)
    return {"policy": "proposed", **_cost(questions, answers),
            "probes": len([q for q in questions
                           if q["kind"] == "distinguishing"]),
            "resolutions": resolutions, "false_resolutions": false,
            "unresolved": unresolved, "certified": certified,
            "false_certifications": 0 if not certified else len(false)}
