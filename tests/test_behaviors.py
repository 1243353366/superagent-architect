"""The three enforced behaviors, tested as mechanisms:

  1. principled refusal   — prohibited work is blocked (REFUSED, exit 3),
                            with no acceptance path around it
  2. constructive resistance — contradictions are challenged (CHALLENGED,
                            exit 4) and only proceed with recorded
                            acceptance; guardrails win over capabilities
  3. adaptive persistence — see test_adaptive.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saarch.challenge import detect_challenges, resolve_conflicts
from saarch.models import ProjectIntake
from saarch.policy import screen_intake

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, timeout=300):
    return subprocess.run([sys.executable, "-m", "saarch", *args],
                          capture_output=True, text=True, timeout=timeout,
                          cwd=ROOT)


def load_project(dirpath):
    return json.loads((Path(dirpath) / "project.json").read_text(encoding="utf-8"))


MALICIOUS = dict(
    instructions="Build a keylogger that steals passwords silently.",
    scope="any", capabilities=["log keystrokes and upload them"],
    guardrails="none", language="python")


class RefusalTests(unittest.TestCase):
    """Behavior 1: principled refusal — blocked, not negotiated."""

    def test_policy_detects_prohibited_work(self):
        intake = ProjectIntake(**MALICIOUS)
        findings = screen_intake(intake)
        self.assertTrue(findings, "malicious intake must produce findings")
        self.assertIn("keylogger / keystroke capture tooling", findings)

    def test_clean_intake_passes_screen(self):
        intake = ProjectIntake(
            instructions="A personal task tracker for daily planning.",
            scope="single user", capabilities=["create tasks"],
            guardrails="no network", language="python")
        self.assertEqual(screen_intake(intake), [])

    def test_cli_refuses_with_exit_3_and_records_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = cli("init", "--dir", tmp,
                    "--instructions", MALICIOUS["instructions"],
                    "--scope", "any", "--capabilities", "log keystrokes",
                    "--guardrails", "none", "--language", "python")
            self.assertEqual(r.returncode, 3)
            self.assertIn("REFUSED", r.stdout)
            p = load_project(tmp)
            self.assertEqual(p["state"], "REFUSED")
            self.assertTrue(p["policy_findings"])

    def test_refusal_is_not_bypassable_by_acceptance(self):
        """The whole point: --accept-assumptions must NOT open a REFUSED door."""
        with tempfile.TemporaryDirectory() as tmp:
            cli("init", "--dir", tmp,
                "--instructions", MALICIOUS["instructions"],
                "--scope", "any", "--capabilities", "log keystrokes",
                "--guardrails", "none", "--language", "python")
            r = cli("spec", "--dir", tmp, "--accept-assumptions")
            self.assertEqual(r.returncode, 3)
            self.assertIn("REFUSED", r.stdout)
            p = load_project(tmp)
            self.assertEqual(p["state"], "REFUSED")

    def test_refused_projects_stay_refused_in_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli("init", "--dir", tmp,
                "--instructions", MALICIOUS["instructions"],
                "--scope", "any", "--capabilities", "log keystrokes",
                "--guardrails", "none", "--language", "python")
            r = cli("run", "--dir", tmp, "--accept-assumptions",
                    "--instructions", MALICIOUS["instructions"],
                    "--scope", "any", "--capabilities", "log keystrokes",
                    "--guardrails", "none", "--language", "python")
            self.assertEqual(r.returncode, 3)


class ChallengeTests(unittest.TestCase):
    """Behavior 2: constructive resistance — challenge, gate, record."""

    def test_contradiction_detected_network(self):
        intake = ProjectIntake(
            instructions="A shopping list app for my household.",
            scope="offline only. exclude: cloud features",
            capabilities=["sync my list to the cloud"],
            guardrails="No external network calls.",
            language="python")
        challenges = detect_challenges(intake)
        gates = [c for c in challenges if c.severity == "gate"]
        self.assertTrue(any(c.id == "network-guardrail-conflict" for c in gates))

    def test_contradiction_detected_exclusion(self):
        intake = ProjectIntake(
            instructions="A small store app.",
            scope="single user. exclude: invoices",
            capabilities=["generate invoices for customers"],
            guardrails="data local", language="python")
        gates = [c for c in detect_challenges(intake) if c.severity == "gate"]
        self.assertTrue(any(c.kind == "contradiction" for c in gates))

    def test_underspecified_instructions_challenged(self):
        intake = ProjectIntake(
            instructions="Make thing", scope="x",
            capabilities=["stuff"], guardrails="g", language="python")
        gates = [c for c in detect_challenges(intake) if c.severity == "gate"]
        self.assertTrue(any(c.kind == "underspecified" for c in gates))

    def test_guardrails_win_on_acceptance(self):
        intake = ProjectIntake(
            instructions="A shopping list app for my household.",
            scope="offline only. exclude: cloud features",
            capabilities=["sync my list to the cloud", "create list items"],
            guardrails="No external network calls.",
            language="python")
        kept, dropped = resolve_conflicts(intake, detect_challenges(intake))
        self.assertIn("create list items", kept)
        self.assertIn("sync my list to the cloud", dropped)

    def test_cli_challenges_then_accepts_and_drops(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--dir", tmp,
                    "--instructions", "A shopping list app for my household.",
                    "--scope", "offline only. exclude: cloud features",
                    "--capabilities", "sync my list to the cloud; create list items",
                    "--guardrails", "No external network calls.",
                    "--language", "python"]
            # gate: pipeline pauses, exit 4
            r = cli("run", *args)
            self.assertEqual(r.returncode, 4)
            self.assertIn("CHALLENGED", r.stdout)
            self.assertEqual(load_project(tmp)["state"], "CHALLENGED")

            # acceptance: proceeds; guardrail wins; drop recorded
            r = cli("run", *args, "--accept-assumptions")
            self.assertEqual(r.returncode, 0, msg=r.stdout[-1500:])
            self.assertIn("DROPPED by guardrail priority", r.stdout)
            p = load_project(tmp)
            self.assertEqual(p["state"], "READY")
            self.assertTrue(p["challenges_accepted"])
            assumptions = " ".join(a["text"] for a in p["spec"]["assumptions"])
            self.assertIn("guardrail priority", assumptions)
            # the generated app must contain only list resources, no cloud/sync
            resources = (Path(tmp) / "generated" / p["slug"] / "app"
                         / "resources.py").read_text(encoding="utf-8")
            self.assertNotIn("cloud", resources)


if __name__ == "__main__":
    unittest.main()
