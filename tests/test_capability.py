"""Capability envelopes: responsibility, capability, and authorization
are three different things, and only the third is enforceable. The model
can request an expansion; it can never grant one."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.capability import (ApprovalAuthority, CapabilityEnvelope,  # noqa
                               EnvelopeError, EnvelopeViolation,
                               EnvelopeEnforcer, CAPABILITY_SCHEMA_VERSION)


def make_envelope(budget=10.0):
    return CapabilityEnvelope("env-1", "builder",
                              "generate app files from the spec", {
        "read_paths": ["src/spec.json", "src/template.py", "src/notes.txt"],
        "write_paths": ["out/app.py", "out/main.py"],
        "commands": [["python3", "-c", "print('ok')"]],
        "budget_seconds": budget, "network": False})


class AuthorityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="caps-"))
        self.authority = ApprovalAuthority()
        self.authority.register_human("human:aadi")
        self.orig = Path.cwd()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class Schema(AuthorityCase):

    def test_round_trip_and_separation(self):
        env = make_envelope()
        rec = env.to_record()
        self.assertEqual(rec["schema_version"], CAPABILITY_SCHEMA_VERSION)
        self.assertEqual(rec["responsible_for"],
                         "generate app files from the spec")
        self.assertEqual(rec["granted"], [])      # authorized: nothing
        self.assertIn("budget_seconds", rec["capabilities"])
        self.assertTrue(all(e["event"] for e in rec["audit"]))

    def test_network_is_never_grantable(self):
        with self.assertRaises(EnvelopeError):
            CapabilityEnvelope("e", "op", "duty", {"network": True,
                                                  "budget_seconds": 5})
        with self.assertRaises(EnvelopeError):
            CapabilityEnvelope("e", "op", "duty",
                              {"budget_seconds": 5, "surprise": []})

    def test_budget_must_be_positive(self):
        with self.assertRaises(EnvelopeError):
            make_envelope(budget=0)


class Enforcement(AuthorityCase):

    def test_no_grant_means_nothing_runs(self):
        env = make_envelope()
        enforcer = EnvelopeEnforcer(env)
        (self.tmp / "src").mkdir()
        (self.tmp / "src/spec.json").write_text("{}")
        import os
        os.chdir(self.tmp)
        try:
            with self.assertRaises(EnvelopeViolation):
                enforcer.read("src/spec.json")
        finally:
            os.chdir(self.orig)
        self.assertTrue(any(e["event"] == "deny" and e["kind"] == "read"
                            for e in env.audit))

    def test_granted_operation_completes_inside_the_envelope(self):
        env = make_envelope()
        self.authority.grant(env, ["read_paths", "write_paths",
                                    "commands"], "human:aadi")
        (self.tmp / "src").mkdir(); (self.tmp / "out").mkdir()
        (self.tmp / "src/spec.json").write_text('{"app": "tasks"}')
        import os
        os.chdir(self.tmp)
        try:
            enforcer = EnvelopeEnforcer(env)
            spec = enforcer.read("src/spec.json")
            enforcer.write("out/app.py", spec)
            enforcer.write("out/main.py", b"print('main')")
            proc = enforcer.exec(["python3", "-c", "print('ok')"])
            self.assertEqual(proc.returncode, 0)
        finally:
            os.chdir(self.orig)
        self.assertTrue((self.tmp / "out/app.py").exists())

    def test_out_of_envelope_is_denied_and_logged(self):
        env = make_envelope()
        self.authority.grant(env, ["write_paths"], "human:aadi")
        import os
        os.chdir(self.tmp)
        try:
            enforcer = EnvelopeEnforcer(env)
            for attempt in ["out/other.py", "../outside.py",
                            "/etc/passwd"]:
                with self.assertRaises(EnvelopeViolation):
                    enforcer.write(attempt, b"x")
            with self.assertRaises(EnvelopeViolation):
                enforcer.exec(["python3", "-c", "import os"])
            self.assertEqual(len([e for e in env.audit
                                  if e["event"] == "deny"]), 4)
        finally:
            os.chdir(self.orig)
        self.assertFalse((self.tmp / "out/other.py").exists())

    def test_budget_exhaustion_denies(self):
        env = make_envelope(budget=0.01)
        self.authority.grant(env, ["read_paths"], "human:aadi")
        (self.tmp / "src").mkdir()
        (self.tmp / "src/spec.json").write_text("{}")
        import os
        os.chdir(self.tmp)
        try:
            enforcer = EnvelopeEnforcer(env)
            for _ in range(200):
                try:
                    enforcer.read("src/spec.json")
                except EnvelopeViolation:
                    break
            else:
                self.fail("budget never exhausted")
            self.assertTrue(any(e.get("kind") == "budget"
                                for e in env.audit))
        finally:
            os.chdir(self.orig)


class Expansion(AuthorityCase):

    def test_model_can_request_but_never_grant(self):
        env = make_envelope()
        self.authority.grant(env, ["read_paths"], "human:aadi")
        enforcer = EnvelopeEnforcer(env)
        rid = enforcer.request_expansion(["write_paths"],
                                         "need to write generated files")
        self.assertEqual(env._requests[rid]["status"], "pending")
        self.assertEqual(env._requests[rid]["requested_by"], "builder")
        # the enforcer has NO approve method — structural separation
        self.assertFalse(hasattr(enforcer, "approve_expansion"))
        self.assertFalse(hasattr(enforcer, "grant"))
        # and nothing model-shaped can pass the authority check
        for fake in ["builder", "engine", "model", "human:fake"]:
            with self.assertRaises(EnvelopeError):
                self.authority.approve_expansion(env, rid, fake)
        self.assertEqual(env._requests[rid]["status"], "pending")

    def test_human_approval_activates_the_expansion(self):
        env = make_envelope()
        self.authority.grant(env, ["read_paths"], "human:aadi")
        (self.tmp / "src").mkdir(); (self.tmp / "out").mkdir()
        (self.tmp / "src/spec.json").write_text("{}")
        import os
        os.chdir(self.tmp)
        try:
            enforcer = EnvelopeEnforcer(env)
            with self.assertRaises(EnvelopeViolation):
                enforcer.write("out/app.py", b"x")       # not granted yet
            rid = enforcer.request_expansion(
                ["write_paths"], "generated code needs a target")
            self.authority.approve_expansion(env, rid, "human:aadi")
            enforcer.write("out/app.py", b"x")           # now allowed
        finally:
            os.chdir(self.orig)
        self.assertTrue((self.tmp / "out/app.py").exists())
        self.assertEqual(env._requests[rid]["status"], "approved")

    def test_grant_cannot_exceed_the_ceiling(self):
        env = make_envelope()
        with self.assertRaises(EnvelopeError):
            self.authority.grant(env, ["write_paths", "credentials"],
                                 "human:aadi")
        self.assertEqual(env.granted, [])      # nothing was granted

    def test_revocation_takes_effect_immediately(self):
        env = make_envelope()
        self.authority.grant(env, ["write_paths"], "human:aadi")
        (self.tmp / "out").mkdir()
        import os
        os.chdir(self.tmp)
        try:
            enforcer = EnvelopeEnforcer(env)
            enforcer.write("out/app.py", b"x")
            self.authority.revoke(env, "human:aadi")
            with self.assertRaises(EnvelopeViolation):
                enforcer.write("out/main.py", b"x")
        finally:
            os.chdir(self.orig)
        self.assertTrue(any(e["event"] == "revoke" for e in env.audit))


if __name__ == "__main__":
    unittest.main()
