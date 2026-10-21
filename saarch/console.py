"""Human Decision Console — the tool built from the FWBC findings.

Tells the human what happened (plain language first, evidence
behind it), lets them decide between valid alternatives, defer,
request more evidence, or reject — and the goal guard refuses
decisions that harm the registered goal directives, quoting the
directive and the amendment path.

Authentication: MFA is MANDATORY by default. The console refuses to
start without a configured owner credential, and login requires
password + TOTP — there is no skip path, verified by tests, not
merely configured. Passwords are stored only as PBKDF2-SHA256
hashes; the TOTP seed lives in a gitignored local file; neither ever
enters source code, logs, or the incident database.

Production identity (Google / company SSO via OAuth 2.0 / OIDC with
organization-enforced MFA) requires the owner's OAuth client
credentials and administrator approval — see README. This console
refuses to simulate that integration and does not claim it works
without evidence.
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH_PATH = ROOT / "benchmarks/results/console-auth.json"
STORE_PATH = ROOT / "benchmarks/results/failures.jsonl"
SESSION_TTL = 30 * 60

_sessions = {}          # token -> expiry (in-memory only)


# ---------------------------------------------------------------- TOTP
def totp_code(secret: str, t: float, step: int = 30,
              digits: int = 6) -> str:
    key = base64.b32decode(
        secret + "=" * ((8 - len(secret) % 8) % 8))
    msg = struct.pack(">Q", int(t // step))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    n = (struct.unpack(">I", digest[off:off + 4])[0]
         & 0x7FFFFFFF) % (10 ** digits)
    return f"{n:0{digits}d}"


def verify_totp(secret: str, code: str, at: float = None,
                drift: int = 1) -> bool:
    at = time.time() if at is None else at
    for d in range(-drift, drift + 1):
        if hmac.compare_digest(
                totp_code(secret, int(at) + d * 30), str(code)):
            return True
    return False


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(),
        200_000).hex()


def setup_auth(auth_path: Path = AUTH_PATH) -> None:
    """Generate the owner credential. Prints the password ONCE and
    the authenticator URI; only the salted hash and the TOTP seed
    are written to the gitignored local file."""
    password = secrets.token_urlsafe(12)
    totp = base64.b32encode(secrets.token_bytes(20)).decode() \
        .rstrip("=")
    salt = secrets.token_hex(16)
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps({
        "password_hash": hash_password(password, salt),
        "salt": salt, "totp_secret": totp}))
    print("owner password (shown ONCE, store it now):", password)
    print("authenticator setup URI:", "otpauth://totp/"
          f"SUPERAGENT:owner?secret={totp}&issuer=SUPERAGENT")


# ---------------------------------------------------------------- API
def state_json(store_path: Path = STORE_PATH, root: Path = ROOT):
    from saarch.f2c import FailureToCapabilityEngine
    engine = FailureToCapabilityEngine(store_path, root)
    inbox = [{
        "gap_id": g["gap_id"], "plain_language": g["plain_language"],
        "what_happened": g["known_facts"],
        "what_remains_uncertain": g["unknowns"] +
        g["competing_hypotheses"],
        "consequences_if_ignored": g["consequences"],
        "options": g["recommended_options"],
        "ai_recommendation": g["recommended_options"][0]
        if g["recommended_options"] else None}
        for g in engine.pending_questions()]
    decisions = [{
        "decision_id": r["decision_id"], "gap_id": r["gap_id"],
        "decision": r["decision"], "response_kind":
        r["response_kind"], "at": r["at"]}
        for r in engine.store.records
        if r.get("record_kind") == "human_decision"]
    return {"inbox": inbox, "decisions": decisions,
            "goal_directives": [
                {"directive_id": r["directive_id"],
                 "directive": r["directive"]}
                for r in engine.store.records
                if r.get("record_kind") == "goal_directive"]}


def decide(payload: dict, store_path: Path = STORE_PATH,
           root: Path = ROOT):
    from saarch.f2c import (F2CError,
                            FailureToCapabilityEngine)
    engine = FailureToCapabilityEngine(store_path, root)
    try:
        row = engine.answer_gap(
            payload["gap_id"], payload["decision"],
            payload.get("option", ""),
            payload.get("response_kind", "decide"))
    except F2CError as exc:
        return 409, {"refused": str(exc)}
    return 200, {"recorded": row["decision_id"],
                 "response_kind": row["response_kind"]}


# ------------------------------------------------------------ server
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>SUPERAGENT — Human Decisions</title>
<style>body{font-family:system-ui;max-width:760px;margin:2rem auto;
padding:0 1rem;background:#111;color:#eee}h1{font-size:1.2rem}
.gap{border:1px solid #444;border-radius:8px;padding:1rem;margin:1rem 0}
button{margin:0.3rem 0.2rem;padding:0.4rem 0.8rem;border-radius:6px;
border:1px solid #666;background:#222;color:#eee;cursor:pointer}
input{width:100%;padding:0.5rem;margin:0.3rem 0;border-radius:6px;
border:1px solid #666;background:#222;color:#eee}
.refused{color:#f88}.meta{color:#999;font-size:0.85rem}</style>
</head><body>
<h1>SUPERAGENT — Human Decisions</h1>
<div id="login"><p>Sign in (password + authenticator code, MFA
required):</p><input id="pw" type="password" placeholder="password">
<input id="totp" placeholder="6-digit code">
<button onclick="login()">Sign in</button><div id="lmsg"></div></div>
<div id="app" style="display:none"><div id="gaps"></div>
<h2>Decision history</h2><div id="hist" class="meta"></div></div>
<script>
async function login(){
 const r=await fetch('/api/login',{method:'POST',headers:
 {'Content-Type':'application/json'},body:JSON.stringify(
 {password:pw.value,totp:totp.value})});
 if(!r.ok){lmsg.textContent='refused: bad credentials';return}
 sessionStorage.token=(await r.json()).token;show();}
async function show(){
 document.getElementById('login').style.display='none';
 document.getElementById('app').style.display='block';
 const s=await (await fetch('/api/state',{headers:
 {'X-Session':sessionStorage.token}})).json();
 gaps.innerHTML=s.inbox.map(g=>
  '<div class=gap><div>'+g.plain_language+'</div>'+
  '<div class=meta>what happened: '+g.what_happened.join('; ')+
  '</div><div class=meta>uncertain: '+
  g.what_remains_uncertain.join('; ')+'</div><div class=meta>if ' +
  'ignored: '+g.consequences_if_ignored+'</div>'+
  '<b>options:</b> '+g.options.map((o,i)=>
  '<button onclick="decide('+(i+1)+',\\'decide\\',\\''+
  g.gap_id+'\\')">choose: '+o+'</button>').join('')+
  '<button onclick="decide(0,\\'defer\\',\\''+g.gap_id+
  '\\')">defer</button><button onclick="decide(0,' +
  '\\\'request_evidence\\\',\\''+g.gap_id+
  '\\')">request more evidence</button>'+
  '<div class=meta>AI recommends: '+g.ai_recommendation+
  '</div></div>').join('');
 hist.innerHTML=s.decisions.map(d=>d.decision_id+' ['+
 d.response_kind+'] '+d.decision).join('<br>');}
async function decide(i,kind,gid){
 const s=await (await fetch('/api/state',{headers:
 {'X-Session':sessionStorage.token}})).json();
 const g=s.inbox.find(x=>x.gap_id===gid);
 const opt=i>0?g.options[i-1]:'';
 const r=await fetch('/api/decide',{method:'POST',headers:
 {'Content-Type':'application/json','X-Session':sessionStorage.token},
 body:JSON.stringify({gap_id:gid,decision:
 kind==='decide'?('chosen: '+opt):kind,option:opt,
 response_kind:kind})});
 const j=await r.json();
 if(!r.ok){alert(j.refused)}else{show()}}
</script></body></html>"""


def make_server(store_path: Path = STORE_PATH,
                auth_path: Path = AUTH_PATH,
                root: Path = ROOT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):      # keep secrets out of logs
            pass

        def _json(self, code, body):
            blob = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type",
                             "application/json")
            self.send_header("Content-Length",
                             str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _authed(self):
            tok = self.headers.get("X-Session", "")
            exp = _sessions.get(tok, 0)
            return exp > time.time()

        def do_GET(self):
            if self.path == "/":
                blob = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "text/html; charset=utf-8")
                self.send_header("Content-Length",
                                 str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            elif self.path == "/api/state":
                if not self._authed():
                    return self._json(401, {"refused": "sign in"})
                return self._json(
                    200, state_json(store_path, root))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if self.path == "/api/login":
                cfg = json.loads(auth_path.read_text())
                ok_pw = hmac.compare_digest(
                    hash_password(body.get("password", ""),
                                   cfg["salt"]),
                    cfg["password_hash"])
                ok_totp = verify_totp(
                    cfg["totp_secret"], body.get("totp", ""))
                if not (ok_pw and ok_totp):
                    return self._json(401, {
                        "refused": "bad credentials (MFA is "
                        "mandatory)"})
                tok = secrets.token_urlsafe(32)
                _sessions[tok] = time.time() + SESSION_TTL
                return self._json(200, {"token": tok})
            if self.path == "/api/decide":
                if not self._authed():
                    return self._json(401, {
                        "refused": "sign in"})
                code, out = decide(body, store_path, root)
                return self._json(code, out)
            return self._json(404, {"error": "not found"})

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--setup" in argv:
        setup_auth()
        return 0
    if not AUTH_PATH.exists():
        print("MFA is mandatory: run  python -m saarch.console "
              "--setup  first (there is no skip path)")
        return 1
    httpd = make_server()
    print(f"Human Decision Console: "
          f"http://127.0.0.1:{httpd.server_address[1]}")
    print("Ctrl+C to shut down")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
