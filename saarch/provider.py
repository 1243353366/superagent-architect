"""Model-provider abstraction.

The core pipeline is deterministic and needs no model access. When an LLM
provider is configured (LiteLLM, or any object implementing ModelProvider),
it is used only to *enrich* derived artifacts — never as a source of truth
that bypasses verification.
"""
from __future__ import annotations

from typing import List, Optional

from .models import EntitySpec


class ProviderUnavailable(Exception):
    """The requested provider is not installed or not configured."""


PROVIDER_UNAVAILABLE = ProviderUnavailable


class ModelProvider:
    """Interface for optional model enrichment."""

    def name(self) -> str:
        raise NotImplementedError

    def enrich_entities(self, entities: List[EntitySpec],
                        context: str) -> List[EntitySpec]:
        """Optionally refine entity fields from the instruction context.

        Implementations MUST return EntitySpec objects and MUST raise
        ProviderUnavailable when they cannot run. Failures fall back to the
        deterministic derivation; they are never silently ignored.
        """
        raise ProviderUnavailable(f"{self.name()} does not implement entity enrichment")


class LiteLLMProvider(ModelProvider):
    """Routes model requests through LiteLLM when it is installed and a key is present.

    Kept behind a lazy import so the core stays zero-dependency. Set
    SAARCH_MODEL and the provider's key environment variable to use it.
    """

    def __init__(self, model: Optional[str] = None):
        self._model = model

    def name(self) -> str:
        return f"litellm:{self._model or 'default'}"

    def enrich_entities(self, entities: List[EntitySpec],
                        context: str) -> List[EntitySpec]:
        import litellm  # noqa: import checked lazily on purpose
        model = self._model or __import__("os").environ.get("SAARCH_MODEL", "gpt-4o-mini")
        prompt = (
            "Given this project context:\n" + context + "\n\n"
            "Refine the field list for these resources. Reply with JSON only, "
            "a list of objects {\"name\": str, \"fields\": [{\"name\", \"type\", \"required\"}]}."
        )
        try:
            resp = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}])
            text = resp["choices"][0]["message"]["content"]
            import json
            data = json.loads(text)
        except Exception as exc:  # malformed or failed completion -> honest fallback
            raise ProviderUnavailable(f"litellm enrichment failed: {exc}") from exc
        enriched = []
        for e in entities:
            match = next((d for d in data if str(d.get("name", "")).lower() == e.name), None)
            if match and isinstance(match.get("fields"), list):
                from .models import FieldSpec
                fields = [FieldSpec(name=str(f.get("name")), type=str(f.get("type", "str")),
                                    required=bool(f.get("required", False)))
                          for f in match["fields"] if f.get("name")]
                if fields:
                    enriched.append(EntitySpec(name=e.name, table=e.table, fields=fields))
                    continue
            enriched.append(e)
        return enriched


def get_provider() -> Optional[ModelProvider]:
    """Return a configured provider, or None when none is available.

    Honesty contract: this function returns None rather than pretending a
    provider exists. The pipeline reports which path was used.
    """
    import os
    if os.environ.get("SAARCH_PROVIDER", "").lower() == "litellm":
        try:
            return LiteLLMProvider()
        except Exception:
            return None
    return None
