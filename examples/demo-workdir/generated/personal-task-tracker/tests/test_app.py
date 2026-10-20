"""End-to-end tests for the generated application. Standard library only.

These tests start a real HTTP server on an ephemeral port and exercise the
API over HTTP, including validation, error, and persistence behavior.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import make_server  # noqa: E402
from app.resources import RESOURCES  # noqa: E402


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "test.db")
        cls.server = make_server("127.0.0.1", 0, cls.db_path)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        payload = json.dumps(body) if body is not None else None
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        parsed = json.loads(data) if data else {}
        return resp.status, parsed

    def test_health(self):
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_ui_page_served(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/")
        resp = conn.getresponse()
        content = resp.read().decode("utf-8")
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertIn("</html>", content)

    def test_crud_roundtrip(self):
        for key in RESOURCES:
            status, created = self.request("POST", f"/api/{key}",
                                          {"title": f"first {key}", "notes": "n1"})
            self.assertEqual(status, 201, f"create failed for {key}: {created}")
            item_id = created["id"]
            self.assertTrue(created["title"])
            self.assertFalse(created["done"])

            status, listed = self.request("GET", f"/api/{key}")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(len(listed), 1)

            status, updated = self.request("PUT", f"/api/{key}/{item_id}",
                                           {"done": True})
            self.assertEqual(status, 200)
            self.assertTrue(updated["done"])

            status, _ = self.request("DELETE", f"/api/{key}/{item_id}")
            self.assertEqual(status, 200)

            status, _ = self.request("GET", f"/api/{key}/{item_id}")
            self.assertEqual(status, 404)

    def test_validation_rejects_bad_payloads(self):
        key = next(iter(RESOURCES))
        status, body = self.request("POST", f"/api/{key}", {"nope": "x"})
        self.assertEqual(status, 400)
        self.assertIn("unknown field", json.dumps(body))

        status, body = self.request("POST", f"/api/{key}", {"title": ""})
        self.assertEqual(status, 400)

        status, body = self.request("POST", f"/api/{key}", {"title": "t", "done": "yes"})
        self.assertEqual(status, 400)

    def test_unknown_resource_and_route(self):
        status, _ = self.request("GET", "/api/doesnotexist")
        self.assertEqual(status, 404)

        status, _ = self.request("GET", "/no/such/route")
        self.assertEqual(status, 404)

    def test_method_not_allowed(self):
        key = next(iter(RESOURCES))
        status, _ = self.request("PUT", f"/api/{key}", {"title": "x"})
        self.assertEqual(status, 405)

    def test_persistence_across_restart(self):
        key = next(iter(RESOURCES))
        status, created = self.request("POST", f"/api/{key}",
                                       {"title": "survives restart"})
        self.assertEqual(status, 201)
        server2 = make_server("127.0.0.1", 0, self.db_path)
        port2 = server2.server_address[1]
        thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
        thread2.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port2, timeout=10)
            conn.request("GET", f"/api/{key}/{created['id']}")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertEqual(body["title"], "survives restart")
        finally:
            server2.shutdown()
            server2.server_close()


if __name__ == "__main__":
    unittest.main()
