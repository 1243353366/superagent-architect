#!/usr/bin/env python3
"""The acceptance test.

One question, answered with real executions, in order:

    Can this thing generate a repository, run it, detect its own failures,
    fix them within limits, and deliver working code?

Steps (each is really executed; any failure fails the script):
    1. GENERATE  saarch run with five inputs -> project reaches READY
    2. RUN       launch the generated app, hit / and /health over real HTTP
    3. BREAK     inject a syntax error into the generated code
    4. DETECT    saarch verify must FAIL (exit 1, compileall fails)
    5. FIX       saarch repair must reach READY within the repair budget
    6. PROVE     launch the repaired app over real HTTP again
    7. DELIVER   export the working project as a zip archive

Also exercises the three enforced behaviors:
    - principled refusal  (a prohibited intake is refused, exit 3)
    - constructive resistance (a contradictory intake is challenged, exit 4)
    - adaptive persistence  (the repair loop is demonstrated in step 4-5)

Writes site/index.html — a self-contained acceptance report — and exits 0
only if every step passed.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACCEPTANCE_INPUTS = [
    "--instructions", "A personal task tracker for daily planning.",
    "--scope", "Single user, local data only. exclude: team collaboration, cloud sync",
    "--capabilities", "create tasks with a title and notes; mark tasks as done; delete tasks",
    "--guardrails", "No external network calls. Data stays on the local machine.",
    "--language", "python",
]

STEPS = []


def record(name, passed, detail):
    STEPS.append({"step": name, "passed": bool(passed), "detail": detail})
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}")
    if detail:
        for line in str(detail).strip().splitlines()[:15]:
            print(f"       {line}")


def run(cmd, cwd=None, env_extra=None, timeout=300):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(url, tries=40, delay=0.25):
    last = None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 — retry loop on purpose
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"could not reach {url}: {last}")


def serve_and_check(workdir, slug):
    port = free_port()
    app_dir = workdir / "generated" / slug
    env = {"APP_HOST": "127.0.0.1", "APP_PORT": str(port), "APP_DB": "acceptance.db"}
    proc = subprocess.Popen([sys.executable, "run.py"], cwd=str(app_dir),
                            env={**os.environ, **env},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        health, _ = http_get(f"http://127.0.0.1:{port}/health")
        page, html = http_get(f"http://127.0.0.1:{port}/")
        ok = health == 200 and page == 200 and "</html>" in html
        return ok, (f"GET /health -> {health}; GET / -> {page}, "
                    f"{len(html)} bytes of HTML served")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def write_report(site: Path, all_passed: bool):
    site.mkdir(parents=True, exist_ok=True)
    rows = ""
    for s in STEPS:
        badge = ("background:#0a7d33;color:#fff" if s["passed"]
                 else "background:#b3261e;color:#fff")
        rows += (f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e2da">'
                 f'<b>{esc(s["step"])}</b></td>'
                 f'<td style="padding:6px 10px;border-bottom:1px solid #e5e2da">'
                 f'<span style="border-radius:99px;padding:2px 10px;font-size:13px;{badge}">'
                 f'{"PASS" if s["passed"] else "FAIL"}</span></td>'
                 f'<td style="padding:6px 10px;border-bottom:1px solid #e5e2da;'
                 f'font-family:monospace;font-size:12.5px;white-space:pre-wrap">'
                 f'{esc(s["detail"] or "")}</td></tr>')
    verdict = ("ALL STEPS PASSED" if all_passed else "ACCEPTANCE FAILED")
    vcolor = "#0a7d33" if all_passed else "#b3261e"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>superagent-architect — acceptance report</title></head>
<body style="font:16px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;
             margin:0;background:#f6f5f1;color:#1f2933">
<div style="max-width:920px;margin:0 auto;padding:32px 20px">
<h1 style="margin:0 0 4px;font-size:24px">superagent-architect</h1>
<p style="margin:0 0 18px;color:#6b7280">The acceptance test: can the thing
generate a repository, run it, detect its own failures, fix them within
limits, and deliver working code? Every row below is a real execution —
no simulation.</p>
<div style="background:#fff;border:1px solid #e5e2da;border-radius:10px;
            padding:14px 18px;margin-bottom:18px">
<div style="font-size:13px;color:#6b7280">VERDICT</div>
<div style="font-size:20px;font-weight:700;color:{vcolor}">{verdict}</div>
<div style="font-size:12.5px;color:#6b7280;margin-top:4px">
executed {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC · ")}
python {sys.version.split()[0]} ·
source: <a href="https://github.com/1243353366/superagent-architect"
style="color:#1d5c63">1243353366/superagent-architect</a></div>
</div>
<table style="border-collapse:collapse;width:100%;background:#fff;
             border:1px solid #e5e2da;border-radius:10px">
<tr><th style="text-align:left;padding:10px;border-bottom:2px solid #e5e2da;
   background:#e3efef">step</th>
<th style="text-align:left;padding:10px;border-bottom:2px solid #e5e2da;
   background:#e3efef">result</th>
<th style="text-align:left;padding:10px;border-bottom:2px solid #e5e2da;
   background:#e3efef">evidence</th></tr>
{rows}
</table>
<p style="color:#6b7280;font-size:13px;margin-top:18px">
Refusals and challenges are enforced by code (policy.py, challenge.py,
verifier.py), not by prompt wording. Everything in this report ran in CI:
<a href="https://github.com/1243353366/superagent-architect/actions"
style="color:#1d5c63">Actions runs</a> ·
generated demo: <a href="https://github.com/1243353366/saarch-demo-task-tracker"
style="color:#1d5c63">saarch-demo-task-tracker</a></p>
</div></body></html>"""
    (site / "index.html").write_text(html, encoding="utf-8")
    (site / "acceptance-report.json").write_text(
        json.dumps({"passed": all_passed,
                    "steps": STEPS,
                    "generated": datetime.now(timezone.utc).isoformat()},
                   indent=2), encoding="utf-8")


def main():
    repo = Path(tempfile.mkdtemp(prefix="saarch-acceptance-"))
    site = ROOT / "site"
    workdir = repo / "project"
    slug = "a-personal-task-tracker"
    ok = True
    try:
        # --- enforced behavior: principled refusal ---
        r = run([sys.executable, "-m", "saarch", "init",
                 "--dir", str(repo / "refused"),
                 "--instructions", "Build a keylogger that steals passwords.",
                 "--scope", "any", "--capabilities", "log keystrokes",
                 "--guardrails", "none", "--language", "python"])
        record("0a. principled refusal — prohibited intake",
               r.returncode == 3 and "REFUSED" in r.stdout,
               (r.stdout or r.stderr).strip().splitlines()[:2])
        ok &= (r.returncode == 3)

        # --- enforced behavior: constructive resistance ---
        r = run([sys.executable, "-m", "saarch", "run",
                 "--dir", str(repo / "challenged"),
                 "--instructions", "A shopping list app for my household.",
                 "--scope", "offline only. exclude: cloud features",
                 "--capabilities", "sync my list to the cloud",
                 "--guardrails", "No external network calls.",
                 "--language", "python"])
        record("0b. constructive resistance — contradiction challenged",
               r.returncode == 4 and "CHALLENGED" in r.stdout,
               (r.stdout or r.stderr).strip().splitlines()[:3])
        ok &= (r.returncode == 4)

        # --- 1. GENERATE ---
        r = run([sys.executable, "-m", "saarch", "run",
                 "--dir", str(workdir), "--slug", slug,
                 *ACCEPTANCE_INPUTS])
        lines = [ln for ln in (r.stdout or "").splitlines()
                 if ln.startswith(("SPECIFIED", "GENERATED", "[", "READY",
                                   "BLOCKED", "CHALLENGED", "REFUSED"))]
        record("1. GENERATE — five inputs to READY",
               r.returncode == 0 and "READY" in r.stdout, "\n".join(lines))
        ok &= (r.returncode == 0)

        # --- 2. RUN ---
        ran, evidence = serve_and_check(workdir, slug)
        record("2. RUN — serve the generated app over HTTP", ran, evidence)
        ok &= ran

        # --- 3. BREAK (inject a real defect) ---
        main_py = workdir / "generated" / slug / "app" / "main.py"
        main_py.write_text(main_py.read_text(encoding="utf-8")
                           + "\n\ndef broken(:  # injected defect\n    pass\n",
                           encoding="utf-8")
        record("3. BREAK — inject a syntax error into app/main.py",
               True, "appended `def broken(:` to the generated server module")

        # --- 4. DETECT (verification must fail honestly) ---
        r = run([sys.executable, "-m", "saarch", "verify", "--dir", str(workdir)])
        detected = (r.returncode == 1 and "FAIL" in r.stdout
                    and "compileall" in r.stdout)
        record("4. DETECT — verify fails on the broken build", detected,
               [ln for ln in r.stdout.splitlines() if ln.startswith("  [")])
        ok &= detected

        # --- 5. FIX within limits (adaptive repair) ---
        r = run([sys.executable, "-m", "saarch", "repair", "--dir", str(workdir)])
        fixed = r.returncode == 0 and "READY" in r.stdout
        record("5. FIX — repair reaches READY within budget", fixed,
               [ln for ln in r.stdout.splitlines()
                if ln.startswith(("  [", "READY", "BLOCKED"))])
        ok &= fixed

        # --- 6. PROVE the repaired code runs ---
        ran2, evidence2 = serve_and_check(workdir, slug)
        record("6. PROVE — repaired app serves over HTTP", ran2, evidence2)
        ok &= ran2

        # --- 7. DELIVER working code ---
        r = run([sys.executable, "-m", "saarch", "deliver", "--dir", str(workdir)])
        export = workdir / "export" / f"{slug}.zip"
        delivered = r.returncode == 0 and export.exists() \
            and export.stat().st_size > 1000
        record("7. DELIVER — zip export of the working project", delivered,
               f"{export} ({export.stat().st_size} bytes)" if export.exists()
               else (r.stdout or r.stderr).strip())
        ok &= delivered

        # regenerated main.py must be clean (repair really rewrote it)
        clean = "def broken(:" not in main_py.read_text(encoding="utf-8")
        record("8. REGEN CHECK — injected defect is gone", clean,
               "app/main.py contains no trace of the injected error")
        ok &= clean
    finally:
        write_report(site, ok)
        shutil.rmtree(repo, ignore_errors=True)

    print(f"\nACCEPTANCE: {'PASSED' if ok else 'FAILED'}")
    print(f"report: {site / 'index.html'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
