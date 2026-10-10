#!/usr/bin/env python3
"""The four tests, in order, each in its own process. Run before every push
(never-back-to-zero.md §3): python3 claudelogin/tests/run_all.py"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["test1_mechanism.py", "test2_real.py", "test3_ugly.py", "test4_upgrade.py"]
red = []
for t in TESTS:
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, t)], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print("%-20s %-36s %5.1f s" % (t, last.split(": ", 1)[-1], time.time() - t0))
    for line in r.stdout.splitlines():
        if line.startswith("  FAIL"):
            print("    " + line.strip())
    if r.returncode:
        red.append(t)
        if r.stderr.strip():
            print("    " + r.stderr.strip().splitlines()[-1])
print("\nall four green" if not red else "\nRED: " + ", ".join(red))
sys.exit(1 if red else 0)
