#!/usr/bin/env python3
"""Live 'existing LLM prompting' baseline (zero-shot ambiguity analysis).

The standard practice this models: give a model the ambiguous request and
ask it to find ambiguities, state its best interpretation, and list the
questions it would ask. No candidate model, no gate, no distinguishing
tests — the model's output is taken as-is. Each request is scored against
the gold interpretations, and every failure (parse error, refusal,
timeout, wrong call) is recorded, not discarded.

Run with an OpenAI-compatible endpoint:
    SAARCH_LLM_URL=... SAARCH_MODEL=... python3 benchmarks/run_llm_baseline.py

Not run in CI (no LLM there); the committed results + transcripts are the
record of the run, with model and date stamped.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dial_in_corpus as C

URL = os.environ.get("SAARCH_LLM_URL", "")
MODEL = os.environ.get("SAARCH_MODEL", "")
TIMEOUT = int(os.environ.get("SAARCH_LLM_TIMEOUT", "120"))

PROMPT = """Analyze this product request. Reply with compact JSON only, no prose, no code fences:
{"ambiguities":[{"aspect":str,"question":str}],"interpretations":[{"aspect":str,"choice":str}],"questions_you_would_ask":[str]}
Detect genuinely ambiguous aspects; if the request is clear, use empty arrays.

REQUEST: {request}"""


def call_llm(request_text: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user",
                      # plain concatenation: PROMPT contains literal JSON
                      # braces that .format() would treat as placeholders
                      # (recorded failure: KeyError '"ambiguities"')
                      "content": PROMPT.replace("{request}", request_text)}],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    attempts = 0
    # free tiers rate-limit; honest backoff, attempts recorded
    for delay in (0, 30, 60, 120):
        if delay:
            time.sleep(delay)
        attempts += 1
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            return {"content": content, "seconds": round(time.time() - t0, 1),
                    "model": data.get("model", MODEL), "attempts": attempts}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and delay < 120:
                continue
            raise
    raise RuntimeError("unreachable")


def parse_json(content: str) -> dict:
    text = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object found in reply")
    return json.loads(m.group(0))


def aspect_matches(aspect: str, gold_aspect: str) -> bool:
    for alias in C.ASPECT_ALIASES.get(gold_aspect, [gold_aspect]):
        if alias in aspect.lower():
            return True
    return False


def normalize(parsed: dict) -> dict:
    """Strict view + lenient view. Zero-shot prompting drifts schemas: the
    model often returns 'ambiguous_aspects'/'clarifications_needed' instead
    of the requested keys. Strict = the integration contract; lenient =
    what a human reader would credit. Both are recorded."""
    strict_amb = parsed.get("ambiguities", [])
    strict_qs = parsed.get("questions_you_would_ask", [])
    len_amb = [{"aspect": a} for a in parsed.get("ambiguous_aspects", [])]
    len_qs = parsed.get("clarifications_needed", [])
    return {"ambiguities": strict_amb or len_amb,
            "questions_you_would_ask": strict_qs or len_qs,
            "schema_compliant": bool(strict_amb or strict_qs)
                                or "ambiguities" in parsed}


def score(item: dict, parsed: dict) -> dict:
    parsed = normalize(parsed)
    detected, matches = [], []
    for gold_aspect, spec in item["ambiguities"].items():
        hit = next((a for a in parsed.get("ambiguities", [])
                    if aspect_matches(str(a.get("aspect", "")), gold_aspect)),
                   None)
        if hit is not None:
            detected.append(gold_aspect)
        interp = next((i for i in parsed.get("interpretations", [])
                       if aspect_matches(str(i.get("aspect", "")), gold_aspect)),
                      None)
        if interp:
            choice = str(interp.get("choice", "")).lower()
            gold_words = {w for w in re.findall(r"[a-z]+", spec["gold"])}
            if any(w in choice for w in gold_words):
                matches.append(gold_aspect)
    return {"detected": detected, "gold_matches": matches}


def main() -> None:
    assert URL and MODEL, "set SAARCH_LLM_URL and SAARCH_MODEL"
    transcripts, summary = [], []
    for item in C.ITEMS:
        rec = {"item": item["id"], "request": item["request"]}
        try:
            raw = call_llm(item["request"])
            rec.update(raw)          # transcript recorded even on later failure
            parsed = parse_json(raw["content"])
            rec["parsed"] = parsed
            s = score(item, parsed)
            rec.update(s)
            rec["status"] = "ok"
            rec["false_positive_ambiguities"] = [
                a.get("aspect") for a in parsed.get("ambiguities", [])
                if not any(aspect_matches(str(a.get("aspect", "")), g)
                           for g in item["ambiguities"])]
        except Exception as exc:
            import traceback
            rec["status"] = f"failed: {type(exc).__name__}: {exc}"
            rec["traceback_tail"] = traceback.format_exc()[-400:]
        transcripts.append(rec)
        print(f"  {item['id']:16s} -> {rec['status'][:60]}")
        if rec.get("status") == "ok":
            print(f"      detected={rec['detected']} gold_matches="
                  f"{rec['gold_matches']} "
                  f"false_pos={rec.get('false_positive_ambiguities')}")

    ok = [r for r in transcripts if r.get("status") == "ok"]
    gold_total = sum(len(i["ambiguities"]) for i in C.ITEMS)
    out = {
        "baseline": "zero-shot LLM prompting (no candidates, no gate)",
        "model": MODEL, "date": datetime.now(timezone.utc).isoformat(),
        "parse_success": f"{len(ok)}/{len(C.ITEMS)}",
        "schema_compliance": (sum(1 for r in ok if r.get("schema_compliant"))
                              / len(ok) if ok else None),
        "ambiguity_recall_lenient": (sum(len(r["detected"]) for r in ok)
                                     / gold_total if gold_total else None),
        "gold_match_rate": (sum(len(r["gold_matches"]) for r in ok)
                            / gold_total if gold_total else None),
        "questions_per_item_avg": (
            sum(len(r.get("parsed", {}).get("questions_you_would_ask", []))
                for r in ok) / len(ok) if ok else None),
        "per_item": [{k: r.get(k) for k in
                      ("item", "status", "detected", "gold_matches",
                       "false_positive_ambiguities", "seconds", "model")}
                     for r in transcripts],
    }
    dest = Path(__file__).parent / "results"
    dest.mkdir(exist_ok=True)
    (dest / "llm-baseline-summary.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    (dest / "llm-baseline-transcripts.json").write_text(
        json.dumps(transcripts, indent=1), encoding="utf-8")
    print("\nSUMMARY:", json.dumps({k: out[k] for k in
                                    ("model", "parse_success",
                                     "schema_compliance",
                                     "ambiguity_recall_lenient",
                                     "gold_match_rate",
                                     "questions_per_item_avg")}, indent=1))


if __name__ == "__main__":
    main()
