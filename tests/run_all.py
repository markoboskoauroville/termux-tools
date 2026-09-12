#!/usr/bin/env python3
"""Start every app mamc knows about, then stop it, and report honestly.

Uses mamc's own Runner, so this exercises the same code path Enter and Q do in
the UI -- not a simulation of it.

For each app:
  START   did the process come up at all
  ALIVE   was it still alive after a settle period (not an instant crash)
  PORT    did something answer on its port (only meaningful if it has one)
  STOP    did Q's code path kill it
  CLEAN   was the whole process group gone afterwards
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("mamc", "/root/.mamc/mamc.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SETTLE = 6.0          # how long to let an app get on its feet
RESULTS = []


def port_open(port):
    if not port:
        return None
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        s.close()


def group_alive(pgid):
    try:
        out = subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)],
                             capture_output=True, text=True, timeout=10)
        return len([l for l in out.stdout.split("\n") if l.strip()])
    except Exception:
        return -1


apps = [a for a in m.discover() if not a.orphan]
print(f"testing {len(apps)} apps\n", flush=True)

for a in apps:
    r = m.Runner()
    row = {"name": a.name, "cmd": a.cmd, "port": a.port,
           "pre_bound": port_open(a.port)}

    err = r.start(a)
    if err:
        row.update(start=False, note=err)
        RESULTS.append(row)
        print(f"{a.name:34} START FAILED  {err}", flush=True)
        continue
    row["start"] = True
    pgid = None
    try:
        pgid = os.getpgid(r.proc.pid)
    except Exception:
        pass

    time.sleep(SETTLE)
    row["alive"] = r.alive
    row["port_after"] = port_open(a.port)
    row["output_lines"] = len(r.lines)
    row["last_line"] = (list(r.lines)[-1][:90] if r.lines else "")
    row["rc_if_dead"] = None if r.alive else r.proc.returncode

    t0 = time.time()
    r.stop()
    row["stop_secs"] = round(time.time() - t0, 2)
    time.sleep(0.6)
    row["still_alive"] = r.alive
    row["group_left"] = group_alive(pgid) if pgid else -1

    ok = row["alive"] and not row["still_alive"] and row["group_left"] == 0
    row["pass"] = bool(ok)
    flag = "ok " if ok else "FAIL"
    print(f"{a.name:34} {flag}  alive={row['alive']} "
          f"port={row['port_after']} stop={row['stop_secs']}s "
          f"left={row['group_left']} rc={row['rc_if_dead']} "
          f"| {row['last_line'][:50]}", flush=True)
    RESULTS.append(row)

out = "/tmp/claude-0/-storage-emulated-0-Download-claudecode/85d0f06a-f318-45f8-997b-07e022982d04/scratchpad/run_all.json"
json.dump(RESULTS, open(out, "w"), indent=2)
passed = sum(1 for r in RESULTS if r.get("pass"))
print(f"\n{passed}/{len(RESULTS)} ran and quit cleanly")
