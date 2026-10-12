#!/usr/bin/env python3
"""Test 8: claudelogin, driven on a pty like a person: o opens the page, v pastes the code.

A fake claude prints a login link and waits for the code; a fake opener records the url it was
given; a fake clipboard holds the code. The screen is read through tests/vt.py, not the byte stream.
"""
import os
import pty
import select
import stat
import struct
import fcntl
import subprocess
import sys
import tempfile
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vt  # noqa: E402

ROOT = os.path.dirname(HERE)
tmp = tempfile.mkdtemp()
SHEB = "#!" + sys.executable + "\n"


def script(name, body):
    p = os.path.join(tmp, name)
    open(p, "w").write(SHEB + body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


state = os.path.join(tmp, "state")
fake_claude = script("claude", f'''
import sys, os
a = sys.argv[1:]
st = {state!r}
if a[:2] == ["auth", "logout"]:
    open(st + ".out", "w").write("1"); sys.exit(0)
if a[:2] == ["auth", "status"]:
    ok = os.path.exists(st + ".in")
    print('{{"loggedIn": %s, "email": "new@example.com"}}' % ("true" if ok else "false")); sys.exit(0)
if a[:2] == ["auth", "login"]:
    print("Opening browser to sign in...")
    print("If it does not open, visit: https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    open(st + ".code", "w").write(code)
    if code == "GOODCODE12345":
        open(st + ".in", "w").write("1"); print("Login successful"); sys.exit(0)
    print("bad code"); sys.exit(1)
''')
opened = os.path.join(tmp, "opened")
fake_open = script("opener", f'''
import sys
open({opened!r}, "w").write(sys.argv[1])
''')
fake_clip = script("clip", 'print("GOODCODE12345")')

env = dict(os.environ, CLAUDELOGIN_CLAUDE=fake_claude, CLAUDELOGIN_OPEN=fake_open,
           CLAUDELOGIN_CLIP=fake_clip, TERM="xterm", HOME=tmp)
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "claudelogin", "claudelogin.py")],
                        stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
os.close(slave)
scr = vt.Screen(30, 100)
fails = []


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                d = os.read(master, 65536)
            except OSError:
                return
            scr.feed(d)


def waitfor(text, sec=8):
    end = time.time() + sec
    while time.time() < end:
        pump(0.2)
        if text in scr.text():
            return True
    return False


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


check("logged out first", waitfor("1  logged out") and os.path.exists(state + ".out"))
check("login link appears on screen", waitfor("claude.ai/oauth/authorize"))
check("key bar shows open and paste code", "open" in scr.line(29) and "paste code" in scr.line(29))
os.write(master, b"o")                     # one key, no Enter
check("o opens the page with the link", waitfor("opened with") and open(opened).read().startswith("https://claude.ai/oauth/authorize"))
os.write(master, b"v")                     # one key, no Enter
check("v sends the clipboard code to the login", waitfor("signed in: new@example.com", 15) and open(state + ".code").read() == "GOODCODE12345")
check("the code itself is never drawn", "GOODCODE12345" not in scr.text())
os.write(master, b"q")
try:
    proc.wait(timeout=8)
    check("q quits", True)
except subprocess.TimeoutExpired:
    proc.kill()
    check("q quits", False)
print("FAILED: %s" % fails if fails else "all passed")
sys.exit(1 if fails else 0)
