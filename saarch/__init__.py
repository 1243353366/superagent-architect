"""saarch — SUPERAGENT: Autonomous Application Architect & Repository Generator.

Turns a five-input specification (instructions, scope, capabilities, guardrails,
language) into a complete, verified, portable application skeleton, and delivers
it as a GitHub-ready repository.

Zero external dependencies in the core; optional integrations (LLM providers,
E2B sandboxes, PyGithub) are loaded lazily and reported honestly when absent.
"""
__version__ = "0.1.0"
