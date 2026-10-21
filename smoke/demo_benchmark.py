#!/usr/bin/env python3
"""Smoke demo 3 — the uncertainty-loss benchmark, live numbers."""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bench = ROOT / "benchmarks" / "uncertainty_loss.py"
runpy.run_path(str(bench), run_name="__main__")
