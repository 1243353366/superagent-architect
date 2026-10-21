#!/usr/bin/env python3
"""Live demo: a builder operation with a capability envelope, not a role.

Run from the repository root: python smoke/demo_capability.py
Shows: grant-free start, in-envelope work, a denied write, an expansion
REQUEST the operation cannot satisfy, human approval, and the audit."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.capability import (ApprovalAuthority, CapabilityEnvelope,  # noqa
                               EnvelopeEnforcer, EnvelopeViolation)

WORK = Path(tempfile.mkdtemp(prefix="envelope-demo-"))
(WORK / "src").mkdir(); (WORK / "out").mkdir()
(WORK / "src/spec.json").write_text('{"app": "task tracker"}')

envelope = CapabilityEnvelope("env-demo", "builder",
                              "turn the five-input spec into app files", {
    "read_paths": ["src/*.json", "src/notes.txt"],
    "write_paths": ["out/app.py", "out/main.py"],
    "commands": [["python3", "-c", "print('build ok')"]],
    "budget_seconds": 15, "network": False})

authority = ApprovalAuthority()
authority.register_human("human:aadi")

enforcer = EnvelopeEnforcer(envelope)
print("granted so far:", envelope.granted or "nothing — as designed")

# an ungranted operation can do nothing
try:
    enforcer.read("src/spec.json")
except EnvelopeViolation:
    print("read denied before any human grant (correct)")

authority.grant(envelope, ["read_paths", "commands"], "human:aadi")
import os
os.chdir(WORK)
spec = enforcer.read("src/spec.json")
enforcer.exec(["python3", "-c", "print('build ok')"])
print("builder reads and runs inside its granted groups")

# writing needs an expansion the operation cannot grant itself
try:
    enforcer.write("out/app.py", spec)
except EnvelopeViolation:
    print("write denied (not yet granted)")
rid = enforcer.request_expansion(
    ["write_paths"], "generated code needs its target files")
print(f"expansion requested ({rid}); the enforcer has no approve method")
authority.approve_expansion(envelope, rid, "human:aadi")
enforcer.write("out/app.py", spec)
enforcer.write("out/main.py", b"print('main')")
print("human approved the expansion; the writes succeed")

# a target beyond the ceiling stays denied even with write_paths granted
try:
    enforcer.write("out/release_notes.py", b"notes")
except EnvelopeViolation:
    print("target beyond the ceiling denied — a new envelope is required")
events = [e["event"] for e in envelope.audit]
print("audit trail:", events)
os.chdir(ROOT)
print("demo complete — an envelope is a grant, not a role label")
