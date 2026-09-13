#!/usr/bin/env python3
"""G6 - stress: the monkey, and the soak.

The monkey presses keys nobody would press, in an order nobody would press
them, to find the state the author never imagined. The soak repeats the one
thing the program is for, many times, to find what leaks.
"""
import fcntl
import importlib.util
import os
import random
import re
import select
import struct
import subprocess
import sys
import termios
import time
import pty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("mamc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mamc", "mamc.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


# ---------------------------------------------------------------- the monkey
print("THE MONKEY - 400 random keypresses, including the dangerous ones")
random.seed(20260912)
KEYS = [b"j", b"k", b"\x1bOA", b"\x1bOB", b"i", b"l", b"t", b"r", b"/", b"\n",
        b"\x1b", b"\x7f", b"!", b"f", b"d", b"n", b"a", b"z", b"0",
        b"\x03", b" ", b"\t", b"\x1b[5~", b"\x1b[6~", b"g", b"G",
        b"\x1bOD", b"c", b"h", b"u", b"\x1b\x1b", b"1", b"9"]
# No q and no Q. Both quit, by design -- a monkey that presses quit and then
# reports "it exited" is reporting its own keystroke as a crash. And no y:
# d then y uninstalls an ordinary app for real, and over 400 keys that pair
# comes up about every other run. A monkey that can move your apps to the
# trash is testing your luck, not the program.
#
# And the trash is put out of reach first. t opens it and r restores the
# selected slot: the monkey restored two apps that had been uninstalled on
# purpose the day before, and they reappeared in the list looking like a
# discovery bug. Random keys against real state is sabotage of the person,
# not of the program.
TRASH = os.path.join(m.MAMC_DIR, "trash")
TRASH_ASIDE = TRASH + ".g6-aside"
if os.path.isdir(TRASH):
    os.rename(TRASH, TRASH_ASIDE)
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 90, 0, 0))
# --no-run: the run screen is the app's terminal now, so a monkey that
# pressed Enter on maha.commute and then 9 would have WIPED it. Enter still
# opens folders; on an app it refuses, and that refusal is what is tested.
# And u asks a GitHub on disk (tests/fixture_repo.py), never the network.
import fixture_repo
FIX_TMP, FIX_BARE, FIX_CO = fixture_repo.make(latest=1)
p = subprocess.Popen(["bash", "/data/data/com.termux/files/usr/bin/mamc", "--no-run"],
                     stdin=slave, stdout=slave, stderr=slave,
                     env=dict(fixture_repo.env_for(FIX_TMP, FIX_CO), TERM="xterm-256color"))
os.close(slave)
buf = b""


def pump(t):
    global buf
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.05)
        if r:
            try:
                buf += os.read(master, 200000)
            except OSError:
                break


end = time.time() + 25
while time.time() < end:
    pump(0.4)
    if "most-run first" in buf.decode("utf-8", "replace"):
        break
pump(1.0)

sent = 0
for i in range(400):
    if p.poll() is not None:
        break
    k = random.choice(KEYS)
    # never let the monkey confirm a removal: y at the first gate is a real
    # uninstall. It may press d, and it may press n, and that is the point.
    if k == b"y":
        k = b"n"
    try:
        os.write(master, k)
        sent += 1
    except OSError:
        break
    if i % 20 == 0:
        pump(0.15)
    else:
        time.sleep(0.02)
pump(2.0)

txt = re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"", buf).decode("utf-8", "replace")
alive = p.poll() is None
check(f"survived {sent} random keys", alive, "" if alive else f"rc={p.poll()}")
check("no traceback", "Traceback" not in txt,
      txt[txt.find("Traceback"):][:1500] if "Traceback" in txt else "")
check("no curses error escaped", "curses.error" not in txt)
check("nothing was actually uninstalled",
      os.path.isdir(os.path.expanduser("~/.tpedit")) and
      os.path.isdir(os.path.expanduser("~/.maread-web")))
if alive:
    os.write(master, b"q")
    time.sleep(1.5)
    if p.poll() is None:
        p.terminate()
        time.sleep(0.5)
check("it still quits after all that", p.poll() is not None)
check("--no-run never started anything: every app on the phone is still stoppable by nobody",
      "running:" not in txt and "Stop it and come back" not in txt)
import shutil as _sh; _sh.rmtree(FIX_TMP, ignore_errors=True)
try:
    os.close(master)
except OSError:
    pass

# ------------------------------------------------------------------ the soak
print("\nTHE SOAK - start and stop an app 12 times, looking for what leaks")
fixture = os.path.join(m.HOME, ".zz_soak")
os.makedirs(fixture, exist_ok=True)
open(os.path.join(fixture, "app.py"), "w").write(
    "from flask import Flask\nimport time\nPORT=9094\n"
    "while True: time.sleep(5)\n")
app = next((a for a in m.discover() if a.path == fixture), None)


def fd_count():
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except OSError:
        return -1


def stray():
    out = subprocess.run(["pgrep", "-f", "zz_soak"], capture_output=True,
                         text=True).stdout.strip()
    return len([x for x in out.split("\n") if x.strip()])


fds0 = fd_count()
for i in range(12):
    r = m.Runner()
    err = r.start(app)
    if err:
        check(f"cycle {i} started", False, err)
        break
    time.sleep(0.8)
    r.stop()
    time.sleep(0.2)
fds1 = fd_count()
time.sleep(1)

check("no file descriptors leaked", fds1 - fds0 <= 2, f"{fds0} -> {fds1}")
check("no stray processes left", stray() == 0, f"{stray()} left")
tmp = [f for f in os.listdir(m.MAMC_DIR) if f.endswith(".tmp")]
check("no half-written temp files", not tmp, str(tmp))
logs = os.path.join(m.MAMC_DIR, "logs")
nlogs = len(os.listdir(logs)) if os.path.isdir(logs) else 0
check("logs do not multiply per run", nlogs < 40, f"{nlogs} files")

import shutil
shutil.rmtree(fixture, ignore_errors=True)
runs = m.load_runs()
runs.pop(fixture, None)
m.save_runs(runs)

if os.path.isdir(TRASH_ASIDE):
    if os.path.isdir(TRASH):
        # the run made a trash of its own: keep both, the real one wins
        import shutil as _sh
        for name in os.listdir(TRASH):
            _sh.move(os.path.join(TRASH, name), TRASH_ASIDE)
        os.rmdir(TRASH)
    os.rename(TRASH_ASIDE, TRASH)

print(f"\n{len(fails)} failures")
sys.exit(1 if fails else 0)
