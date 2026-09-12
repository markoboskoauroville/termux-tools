#!/usr/bin/env python3
"""Re-run the apps that exited instantly, this time WITH a terminal.

The first sweep gave every app stdin=DEVNULL and no tty. Six of them quit at
once, and a curses app raised nocbreak() returned ERR -- which is not the app
failing, it is the test failing to reproduce the conditions the app runs in.
The manifest is explicit about this: any detail the system keys on has to
match reality or the test proves nothing about reality.

So: a real pty, a real terminal size, and then the app's own quit key.
"""
import fcntl
import importlib.util
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

spec = importlib.util.spec_from_file_location("mamc", "/root/.mamc/mamc.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SUSPECTS = {
    "ahkedit":                         b"q",
    "mawhisper":                       b"q",
    "tts_reader":                      b"q",
    "MAHA_TRANSCRIBE_TERMUX_TERMINAL": b"q",
    "maha.commute":                    b"q",
    "talker":                          b"q",
}

apps = {a.name: a for a in m.discover()}
print(f"{'app':34} {'started':>8} {'alive@6s':>9} {'quit key':>9} {'left':>5}")
print("-" * 72)

rows = []
for name, quitkey in SUSPECTS.items():
    a = apps.get(name)
    if not a:
        print(f"{name:34} not found")
        continue
    argv, cwd = m.Runner().command_for(a)
    if not argv:
        print(f"{name:34} nothing to run")
        continue

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", MA_NESTED="1",
               PYTHONUNBUFFERED="1")
    try:
        p = subprocess.Popen(argv, cwd=cwd if cwd and os.path.isdir(cwd) else m.HOME,
                             stdin=slave, stdout=slave, stderr=slave,
                             env=env, start_new_session=True, close_fds=True)
    except Exception as e:
        print(f"{name:34} could not start: {e}")
        os.close(master); os.close(slave)
        continue
    os.close(slave)
    pgid = os.getpgid(p.pid)

    buf = b""
    end = time.time() + 6
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                buf += os.read(master, 65536)
            except OSError:
                break
    alive = p.poll() is None

    # the app's own quit key, the way a person would press it
    quit_worked = None
    if alive:
        try:
            os.write(master, quitkey)
        except OSError:
            pass
        t0 = time.time()
        while time.time() - t0 < 5 and p.poll() is None:
            try:
                r, _, _ = select.select([master], [], [], 0.2)
                if r:
                    buf += os.read(master, 65536)
            except OSError:
                break
        quit_worked = p.poll() is not None

    if p.poll() is None:                      # it would not go: use Q's path
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(1.5)
        if p.poll() is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            time.sleep(0.5)
    p.poll()
    left = subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)],
                          capture_output=True, text=True).stdout.strip()
    nleft = len([x for x in left.split("\n") if x.strip()])
    os.close(master)

    txt = re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"", buf).decode("utf-8", "replace")
    print(f"{name:34} {'yes':>8} {str(alive):>9} "
          f"{str(quit_worked):>9} {nleft:>5}   "
          f"{txt.strip().splitlines()[-1][:34] if txt.strip() else ''}")
    rows.append((name, alive, quit_worked, nleft))

print()
good = [r for r in rows if r[1] and r[2] and r[3] == 0]
print(f"{len(good)}/{len(rows)} ran with a terminal and quit on their own key")
for name, alive, quit_worked, nleft in rows:
    if not (alive and quit_worked and nleft == 0):
        why = ("exits immediately even with a tty" if not alive else
               "did not quit on its key" if not quit_worked else
               f"{nleft} processes left behind")
        print(f"   {name}: {why}")
