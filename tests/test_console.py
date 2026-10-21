"""Human Decision Console: MFA enforcement is VERIFIED, not merely
configured. Password + TOTP are both mandatory; wrong or missing
credentials are refused; unauthenticated API calls are refused;
decisions route through the goal guard; defer does not block a
later final decision. F-GATE-CHAIN's cousin lesson: never claim an
auth policy works without testing it."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saarch.console import (hash_password, make_server,    # noqa: E402
                            totp_code, verify_totp)
from saarch.f2c import FailureToCapabilityEngine            # noqa: E402


class Totp(unittest.TestCase):

    def test_correct_code_verifies_and_wrong_refused(self):
        secret = "JBSWY3DPEHPK3PXP"
        now = 2000000000
        self.assertTrue(verify_totp(secret,
                                    totp_code(secret, now), at=now))
        self.assertFalse(verify_totp(secret, "123456", at=now))
        self.assertFalse(verify_totp(secret, "", at=now))

    def test_drift_window(self):
        secret = "JBSWY3DPEHPK3PXP"
        now = 2000000000
        self.assertTrue(verify_totp(secret,
                                    totp_code(secret, now - 30),
                                    at=now))


class Console(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.store = self.dir / "s.jsonl"
        self.auth = self.dir / "auth.json"
        self.password = "correct-horse-battery"
        self.secret = "JBSWY3DPEHPK3PXP"
        self.auth.write_text(json.dumps({
            "password_hash": hash_password(self.password, "salt16"),
            "salt": "salt16", "totp_secret": self.secret}))
        engine = FailureToCapabilityEngine(self.store, ROOT)
        engine.open_gap({
            "gap_id": "G-C1", "schema_version":
            engine.ingest_failure.__globals__["F2C_SCHEMA_VERSION"],
            "created_at": "2026-10-21T00:00:00Z",
            "gap_class": "ambiguous_requirement",
            "subject": "F-TEST",
            "plain_language": "One option is risky, one is safe. "
                              "You choose.",
            "known_facts": ["fact"],
            "unknowns": ["unknown"],
            "assumptions": [],
            "competing_hypotheses": [],
            "consequences": "nothing changes",
            "uncertainty": 0.5, "safety_impact": "S2",
            "recurrence": 0, "human_resolvable": True,
            "recommended_options": ["safe option", "risky option"],
            "status": "open"})
        engine.register_goal_directive(
            "never accept shared coverage", "owner directive",
            ["accept shared coverage"])
        self.httpd = make_server(self.store, self.auth, ROOT)
        port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{port}"
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def _post(self, path, body, token=None):
        req = urllib.request.Request(
            self.base + path, method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        if token:
            req.add_header("X-Session", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _login(self, password=None, totp=None):
        code = totp if totp is not None else \
            totp_code(self.secret, time.time())
        return self._post("/api/login", {
            "password": password or self.password, "totp": code})

    def test_mfa_is_mandatory_not_just_configured(self):
        self.assertEqual(self._login(totp="")[0], 401,
                         "login without a TOTP code must be refused")
        self.assertEqual(self._login(password="wrong")[0], 401)
        self.assertEqual(self._login(totp="000000")[0], 401)
        self.assertEqual(self._login()[0], 200,
                         "password + valid TOTP must succeed")

    def test_unauthenticated_api_refused(self):
        self.assertEqual(
            self._post("/api/decide", {"gap_id": "G-C1",
                                       "decision": "x"})[0], 401)

    def test_decision_records_and_closes_nothing(self):
        _, out = self._login()
        tok = out["token"]
        code, res = self._post("/api/decide", {
            "gap_id": "G-C1", "decision": "chosen: safe option",
            "option": "safe option", "response_kind": "decide"},
            token=tok)
        self.assertEqual(code, 200)
        engine = FailureToCapabilityEngine(self.store, ROOT)
        self.assertEqual(engine.store.find("gap_id", "G-C1")
                         ["status"], "answered")

    def test_goal_harm_refused_with_directive_quoted(self):
        _, out = self._login()
        code, res = self._post("/api/decide", {
            "gap_id": "G-C1",
            "decision": "accept shared coverage",
            "option": "accept shared coverage"}, token=out["token"])
        self.assertEqual(code, 409)
        self.assertIn("never accept shared coverage",
                      res["refused"])
        engine = FailureToCapabilityEngine(self.store, ROOT)
        self.assertEqual(engine.store.find("gap_id", "G-C1")
                         ["status"], "open",
                         "a refused decision must not move the gap")

    def test_defer_then_decide(self):
        _, out = self._login()
        tok = out["token"]
        code, _ = self._post("/api/decide", {
            "gap_id": "G-C1", "decision": "need more time",
            "response_kind": "defer"}, token=tok)
        self.assertEqual(code, 200)
        engine = FailureToCapabilityEngine(self.store, ROOT)
        self.assertEqual(engine.store.find("gap_id", "G-C1")
                         ["status"], "open",
                         "defer must not move the gap")
        code, _ = self._post("/api/decide", {
            "gap_id": "G-C1", "decision": "chosen: safe option",
            "option": "safe option", "response_kind": "decide"},
            token=tok)
        self.assertEqual(code, 200,
                         "a later final decision must not be blocked "
                         "by a defer")
