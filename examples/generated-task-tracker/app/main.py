"""HTTP server and routing. Standard library only."""
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .api import Repo
from .resources import RESOURCES
from .storage import Database
from .ui import PAGE
from .validation import ValidationError

_API_RE = re.compile(r"^/api/([a-z0-9_-]+)(?:/(\d+))?$")


def make_server(host, port, db_path):
    """Create a migrated database and a server bound to (host, port)."""
    db = Database(db_path)
    db.migrate()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep test output clean; override to enable logging

        def _json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, body, status=200):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _dispatch(self, method):
            path = self.path.split("?", 1)[0]
            if path == "/":
                if method == "GET":
                    return self._html(PAGE)
                return self._json(405, {"error": "method not allowed"})
            if path == "/health":
                if method == "GET":
                    return self._json(200, {"status": "ok"})
                return self._json(405, {"error": "method not allowed"})

            m = _API_RE.match(path)
            if not m:
                return self._json(404, {"error": "not found"})
            plural, item_id = m.group(1), m.group(2)
            if plural not in RESOURCES:
                return self._json(404, {"error": f"unknown resource '{plural}'"})
            repo = Repo(self.db, plural)
            item_id = int(item_id) if item_id else None
            try:
                if method == "GET":
                    if item_id is None:
                        return self._json(200, repo.list())
                    item = repo.get(item_id)
                    if item is None:
                        return self._json(404, {"error": "not found"})
                    return self._json(200, item)
                if method == "POST":
                    if item_id is not None:
                        return self._json(405, {"error": "method not allowed"})
                    return self._json(201, repo.create(self._body()))
                if method == "PUT":
                    if item_id is None:
                        return self._json(405, {"error": "method not allowed"})
                    item = repo.update(item_id, self._body())
                    if item is None:
                        return self._json(404, {"error": "not found"})
                    return self._json(200, item)
                if method == "DELETE":
                    if item_id is None:
                        return self._json(405, {"error": "method not allowed"})
                    if not repo.delete(item_id):
                        return self._json(404, {"error": "not found"})
                    return self._json(200, {"deleted": True})
            except ValidationError as exc:
                return self._json(400, {"error": {"field": exc.field,
                                                   "message": exc.message}})
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._json(400, {"error": "invalid JSON body"})
            except sqlite3.Error:
                return self._json(500, {"error": "storage failure"})
            return self._json(405, {"error": "method not allowed"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

    Handler.db = db
    return ThreadingHTTPServer((host, port), Handler)
