"""Stage B and C: derive a structured ProjectSpec from the five inputs.

Rule 1: users never supply the data model, API shape, or folder structure —
the spec builder derives them and records every assumption it makes.
Rule 2: mandatory guardrails are always included and never weakened.
Rule 3: an LLM provider (when configured) only *enriches* the derived spec;
the deterministic derivation always runs first, so the pipeline works without
any model access.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .audit import AuditLog
from .models import (
    Assumption, ClarifyingQuestion, EndpointSpec, EntitySpec, FieldSpec,
    MANDATORY_SECURITY_REQUIREMENTS, MANDATORY_TESTING_CRITERIA, Project,
    ProjectSpec, Risk, SPEC_VERSION,
)
from .provider import ModelProvider, PROVIDER_UNAVAILABLE

DEFAULT_FIELDS = [
    FieldSpec(name="title", type="str", required=True),
    FieldSpec(name="notes", type="str", required=False),
    FieldSpec(name="done", type="bool", required=False),
    FieldSpec(name="created_at", type="datetime", required=True),
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", str(text).strip().lower()).strip("-")
    return slug or "item"


def singularize(word: str) -> str:
    w = word.strip().lower()
    if w.endswith("ies"):          # policies -> policy
        return w[:-3] + "y"
    if w.endswith("sses"):         # classes, addresses -> class, address
        return w[:-2]
    if w.endswith(("ss", "us")):   # status, virus, bonus stay singular
        return w
    if w.endswith("s"):            # expenses -> expense, houses -> house
        return w[:-1]
    return w


def parse_exclusions(scope_text: str) -> List[str]:
    """Lines like 'exclude: X' or 'not included: X' become explicit exclusions."""
    exclusions = []
    for line in str(scope_text or "").splitlines():
        m = re.search(r"(?:^|[.;]\s*)(?:exclude[sd]?|not included|exclusions?)"
                      r"\s*[:\-]\s*(.+)$", line, re.IGNORECASE)
        if m:
            exclusions.append(m.group(1).strip())
    return exclusions


def derive_entities(capabilities: List[str]) -> List[EntitySpec]:
    entities: List[EntitySpec] = []
    seen = set()
    for cap in capabilities:
        words = re.findall(r"[a-z][a-z0-9]*", cap.lower())
        stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for",
                "with", "can", "be", "is", "are", "user", "users", "system",
                "app", "application", "create", "manage", "track", "list",
                "view", "add", "edit", "delete", "update", "mark"}
        noun = None
        for w in words:
            if w not in stop and len(w) > 2:
                noun = w
                break
        name = singularize(slugify(noun or cap).replace("-", "_")) if noun else slugify(cap).replace("-", "_")
        name = singularize(name)
        if name in seen:
            continue
        seen.add(name)
        entities.append(EntitySpec(
            name=name, table=name,
            fields=[FieldSpec(**{**f.__dict__}) for f in DEFAULT_FIELDS]))
    if not entities:
        entities.append(EntitySpec(
            name="item", table="item",
            fields=[FieldSpec(**{**f.__dict__}) for f in DEFAULT_FIELDS]))
    return entities


def derive_endpoints(entities: List[EntitySpec]) -> List[EndpointSpec]:
    eps = []
    for e in entities:
        plural = e.table + ("s" if not e.table.endswith("s") else "es")
        base = f"/api/{plural}"
        eps.append(EndpointSpec("GET", base, f"List {plural}"))
        eps.append(EndpointSpec("POST", base, f"Create a {e.table}"))
        eps.append(EndpointSpec("GET", base + "/{id}", f"Get one {e.table} by id"))
        eps.append(EndpointSpec("PUT", base + "/{id}", f"Update a {e.table}"))
        eps.append(EndpointSpec("DELETE", base + "/{id}", f"Delete a {e.table}"))
    eps.append(EndpointSpec("GET", "/health", "Health check"))
    return eps


def build_spec(project: Project, provider: Optional[ModelProvider] = None,
               audit: Optional[AuditLog] = None,
               capabilities_override: Optional[List[str]] = None) -> ProjectSpec:
    intake = project.intake
    # capabilities_override is set when the user accepted a challenge and
    # guardrail priority dropped conflicting capabilities (see challenge.py)
    caps = (capabilities_override if capabilities_override is not None
            else intake.normalized_capabilities())

    entities = derive_entities(caps)
    # Optional enrichment: an LLM provider may refine entity field types.
    provider_note = "deterministic derivation (no model provider configured)"
    if provider is not None:
        try:
            entities = provider.enrich_entities(entities, context=intake.instructions)
            provider_note = f"entities enriched by provider {provider.name()}"
        except PROVIDER_UNAVAILABLE:
            provider_note = "provider unavailable; deterministic derivation used"

    endpoints = derive_endpoints(entities)
    exclusions = parse_exclusions(intake.scope)

    functional = [f"System shall support: {cap}" for cap in caps]

    assumptions = [
        Assumption(f"Entity model derived deterministically from capabilities ({provider_note}); "
                    "each capability maps to one resource with fields: "
                    + ", ".join(f"{f.name} ({f.type}{', required' if f.required else ''})"
                                for f in DEFAULT_FIELDS) + "."),
        Assumption("SQLite chosen as the data store: single-file, zero-config, portable; "
                   "appropriate for the MVP scope and swappable via migrations."),
        Assumption("English pluralization applied naively (append s/es) for API routes."),
        Assumption("Authentication not generated because no capability or guardrail "
                   "explicitly required authenticated roles; recorded as a risk."),
    ]
    risks = [
        Risk("No authentication/authorization layer was requested or generated; "
             "generated API is single-tenant and must sit behind a trusted "
             "boundary before exposure.", severity="medium"),
        Risk("SQLite suits development and small deployments; high-concurrency "
             "production use needs a client-server database.", severity="low"),
        Risk("Entity field shapes are template-derived; unusual domain fields "
             "will need storage.py and validation.py edits.", severity="low"),
    ]
    questions = []
    if re.search(r"\b(deploy|production|host)\b", intake.instructions, re.IGNORECASE):
        questions.append(ClarifyingQuestion(
            "The instructions mention deployment. Which target environment "
            "(none/docker/cloud) should the generated deployment setup assume? "
            "Proceeding without an answer: local-run instructions only."))

    spec = ProjectSpec(
        version=SPEC_VERSION,
        instructions=intake.instructions,
        boundaries=intake.scope,
        exclusions=exclusions,
        functional_requirements=functional,
        entities=entities,
        endpoints=endpoints,
        ui_routes=["GET / — single-page UI listing every resource"],
        security_requirements=list(MANDATORY_SECURITY_REQUIREMENTS) + [intake.guardrails],
        testing_criteria=list(MANDATORY_TESTING_CRITERIA),
        dependencies=["python >= 3.9 (standard library only)"],
        assumptions=assumptions,
        risks=risks,
        questions=questions,
        profile="python-stdlib-sqlite",
    )
    if audit:
        audit.append("spec_built",
                     entities=[e.name for e in entities],
                     endpoints=len(endpoints),
                     exclusions=exclusions,
                     provider=provider_note,
                     questions=[q.text for q in questions])
    return spec
