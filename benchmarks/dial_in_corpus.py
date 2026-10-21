"""Dial-in benchmark: gold requirements, ambiguous requests, interacting assumptions.

Each corpus item pairs a deliberately ambiguous request with the KNOWN
intended interpretation (gold) and a truthful oracle that answers questions.
Policies are compared on how much user input they need to reach the gold
interpretation, whether they falsely certify, and what their tests actually
distinguish. Everything here is deterministic: same corpus, same policies,
same numbers, every run.
"""
from __future__ import annotations

from typing import Dict, List

# Each item:
#   request         - the deliberately ambiguous natural-language request
#   ambiguities     - known ambiguous aspects; candidates + gold answer
#   assumptions     - interacting assumptions: resolving one collapses
#                     dependent ambiguities (the claim graph knows this;
#                     an unstructured tracker does not)
#   paraphrases     - same meaning, different words (pattern-recall set)
ITEMS: List[Dict] = [
    {
        "id": "cancel-refund",
        "request": ("Members can cancel their subscription anytime without "
                    "penalty. Members who cancel receive a refund for the "
                    "current period."),
        "ambiguities": {
            "cancellation-scope": {
                "question": "After someone commits (subscribes), can they "
                            "still cancel anytime?",
                "candidates": ["before-commitment-only", "anytime-including-after"],
                "gold": "anytime-including-after",
            },
            "refund-scope": {
                "question": "How much of the current period is refunded?",
                "candidates": ["full", "prorated", "none"],
                "gold": "prorated",
            },
        },
        "assumptions": {
            "auto-renews": {
                "question": "Does the subscription auto-renew each period?",
                "candidates": ["yes", "no"],
                "gold": "yes",
                "collapses": {"refund-scope": "prorated"},
            },
        },
        "paraphrases": [
            "Users may terminate the service at any moment, free of charge. "
            "Anyone who quits gets their money back for the ongoing month.",
        ],
    },
    {
        "id": "delete-account",
        "request": "Users can delete their account and their data at any time.",
        "ambiguities": {
            "delete-semantics": {
                "question": "Is deletion immediate and permanent, or soft "
                            "with a recovery window?",
                "candidates": ["hard-immediate", "soft-recoverable"],
                "gold": "soft-recoverable",
            },
        },
        "assumptions": {
            "deletion-is-irreversible": {
                "question": "Is deletion treated as irreversible once "
                            "confirmed?",
                "candidates": ["yes", "no"],
                "gold": "no",
                "collapses": {"delete-semantics": "soft-recoverable"},
            },
        },
        "paraphrases": [
            "People can wipe their profile whenever they want.",
        ],
    },
    {
        "id": "local-only",
        "request": ("Keep the data local. The app should be fast for "
                    "everyday use."),
        "ambiguities": {
            "storage-scope": {
                "question": "Does 'local' mean no network calls at all, or "
                            "just local storage?",
                "candidates": ["local-storage-only", "no-network-calls-at-all"],
                "gold": "no-network-calls-at-all",
            },
            "performance-target": {
                "question": "What response time counts as 'fast'?",
                "candidates": ["no-explicit-threshold", "under-500ms"],
                "gold": "no-explicit-threshold",
            },
        },
        "assumptions": {},
        "paraphrases": [
            "Everything stays on this machine, and it should feel snappy.",
        ],
    },
    {
        "id": "single-clear",
        "request": "A todo list app with checkboxes and a delete button.",
        "ambiguities": {},
        "assumptions": {},
        "paraphrases": [
            "A checklist application where entries can be removed.",
        ],
    },
]

# Aspect aliases for scoring LLM output against gold aspects
ASPECT_ALIASES = {
    "cancellation-scope": ["cancel", "terminat", "quit", "refund"],
    "refund-scope": ["refund", "money back", "prorat"],
    "delete-semantics": ["delete", "remov", "wipe", "account"],
    "storage-scope": ["local", "network", "data", "machine"],
    "performance-target": ["fast", "performance", "snappy", "response"],
}


def all_aspects(item: Dict) -> List[str]:
    return list(item["ambiguities"]) + list(item.get("assumptions", {}))


def oracle_answer(item: Dict, aspect: str, question: str) -> Dict:
    """Truthful oracle. Assumption answers are short; generic restate more.

    A policy that asks the RIGHT (distinguishing) question gets a minimal
    answer; a policy that asks a GENERIC question still gets the truth but
    pays in answer length and in extra questions later (it learns nothing
    about the interaction structure).
    """
    if aspect in item["ambiguities"]:
        gold = item["ambiguities"][aspect]["gold"]
        return {"aspect": aspect, "answer": f"{gold}", "kind": "ambiguity",
                "collapses": {}}
    if aspect in item.get("assumptions", {}):
        a = item["assumptions"][aspect]
        return {"aspect": aspect, "answer": a["gold"], "kind": "assumption",
                "collapses": dict(a.get("collapses", {}))}
    raise KeyError(f"unknown aspect {aspect!r} for {item['id']}")


def generic_answer(item: Dict, aspect: str, question: str) -> Dict:
    """Answer to an unstructured 'what did you mean by X?' question.

    The oracle answers only the aspect asked about — truthfully, but in
    prose, because the question did not offer the candidate choices. The
    user pays in typing; the answer carries no collapse information because
    the policy has no interaction structure to apply it to.
    """
    if aspect in item["ambiguities"]:
        spec = item["ambiguities"][aspect]
        text = (f"For {aspect.replace('-', ' ')}: what we intend is that the "
                f"behavior is '{spec['gold']}' — that is the meaning the "
                f"request was written with.")
    elif aspect in item.get("assumptions", {}):
        spec = item["assumptions"][aspect]
        text = (f"Regarding {aspect.replace('-', ' ')}: we assume "
                f"'{spec['gold']}'.")
    else:
        text = "That part is not ambiguous."
    return {"aspect": aspect, "answer": text, "kind": "generic",
            "collapses": {}}


