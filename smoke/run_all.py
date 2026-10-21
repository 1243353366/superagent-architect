#!/usr/bin/env python3
"""Run every smoke demo in order — the physical tour of the prototype."""
import runpy
import sys

DEMOS = ["demo_ambiguity.py", "demo_calibration.py", "demo_benchmark.py"]

for demo in DEMOS:
    print(f"\n\n########## {demo} ##########")
    runpy.run_path(str(__file__).replace("run_all.py", demo),
                   run_name="__main__")
print("\nAll smoke demos completed.")
