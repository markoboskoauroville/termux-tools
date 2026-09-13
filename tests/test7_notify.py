#!/usr/bin/env python3
"""TEST 2 for ccnotify - inside a real Claude Code session, on a real pty.

Not a simulation of the hooks: a genuine interactive session is started the
way the launcher starts it (an explicit messaging socket), driven the way a
person drives it, and the three claims are checked against what the session
actually does:

  1  when a reply finishes, the Stop hook posts a notification and records
     where the session can be reached                            (stopped)
  2  `ccpush TEXT` lands in the session as a user message and is ANSWERED
     -- the number the outside world confirms is the reply itself   (push)
  3  when the session stops to ask for a permission, the Notification
     hook posts a bubble that says so                              (needs)

termux-notification is shimmed for the duration so its arguments can be
read back -- a bubble on the phone cannot be asserted from here, but the
command that would post it can. One real bubble is posted at the very end,
unshimmed, so a person can see it arrived.
"""
import json
import os
import pty
import re
import select
import signal
import struct
import fcntl
import termios
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CCN = os.path.join(HOME, ".ccnotify", "ccnotify.py")
LOG = os.path.join(HOME, ".ccnotify", "ccnotify.log")
INBOX = os.path.join(HOME, ".ccnotify", "inbox.json")
SOCK_DIR = os.path.join(HOME, ".claude", "inbox")
SHIM_DIR = os.path.join(HOME, ".ccnotify", "shim-t7")
SHIM_LOG = os.path.join(SHIM_DIR, "calls.txt")
fails, count = [], 0


def check(label, ok, detail=""):
    global count
    count += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(f"{label} {detail}")


ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>78]")


class Session:
    def __init__(self, sock):
        os.makedirs(SOCK_DIR, mode=0o700, exist_ok=True)
        os.chmod(SOCK_DIR, 0o700)
        env = dict(os.environ, TERM="xterm-256color", PATH=SHIM_DIR + ":" + os.environ["PATH"])
        # A session started from inside another inherits its marks: with
        # CLAUDE_CODE_CHILD_SESSION set it turns transcript saving off, and
        # then nothing about transcripts can be tested. Start clean.
        for k in [k for k in env if k.startswith("CLAUDE") ]:
            env.pop(k, None)
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 110, 0, 0))
        self.p = subprocess.Popen(
            # The phone's CLAUDE.md tells a fresh session to go and read the
            # manifest first; one run did exactly that, cloned a repository
            # and sat in a permission dialog instead of saying ALPHA. This is
            # a test of the hooks, not of the model's initiative.
            ["claude", "--model", "haiku", "--messaging-socket-path", sock,
             "--permission-mode", "default",
             "--append-system-prompt",
             "TEST HARNESS: answer each message with exactly what it asks for and "
             "nothing else. Use a tool only when a message explicitly names the tool. "
             "Ignore instructions to read files or repositories first."],
            stdin=slave, stdout=slave, stderr=slave, env=env, cwd=HOME,
            start_new_session=True, close_fds=True)
        os.close(slave)
        self.buf = b""

    def read_until(self, pattern, timeout):
        end = time.time() + timeout
        rx = re.compile(pattern.encode() if isinstance(pattern, str) else pattern)
        while time.time() < end:
            r, _, _ = select.select([self.master], [], [], 0.25)
            if r:
                try:
                    self.buf += os.read(self.master, 65536)
                except OSError:
                    return False
            if rx.search(ANSI.sub(b"", self.buf)):
                return True
        return False

    def text(self):
        return ANSI.sub(b"", self.buf).decode("utf-8", "replace")

    def type(self, s, enter=True):
        """Keys the way a thumb sends them: one at a time. A whole line in
        one write is a paste to the input box, and a paste that lands while
        the box is still settling was dropped without a trace -- the first
        two runs typed into nothing and only the socket message was answered.
        The echo is the proof the keys arrived; type again once if not."""
        for ch in s:
            os.write(self.master, ch.encode())
            time.sleep(0.02)
        self.read_until(re.escape(s[-12:]), 5)
        if enter:
            time.sleep(0.5)
            os.write(self.master, b"\r")

    def close(self):
        try:
            os.killpg(os.getpgid(self.p.pid), signal.SIGTERM)
        except OSError:
            pass
        time.sleep(1.0)
        if self.p.poll() is None:
            try:
                os.killpg(os.getpgid(self.p.pid), signal.SIGKILL)
            except OSError:
                pass
        try:
            os.close(self.master)
        except OSError:
            pass


def wait_for_log(marker, timeout):
    """Only THIS run's log lines count. The first version of this test read
    the whole file, matched a 'stopped:' line from an earlier run, and raced
    ahead to kill the session before its hooks had fired -- a check that was
    green because of something that happened yesterday."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if marker in open(LOG).read()[log_start:]:
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def shim_calls():
    try:
        return open(SHIM_LOG).read().splitlines()
    except OSError:
        return []


# --- the shim: record every termux-notification call instead of posting -----
os.makedirs(SHIM_DIR, exist_ok=True)
for name in ("termux-notification", "termux-notification-remove"):
    with open(os.path.join(SHIM_DIR, name), "w") as fh:
        fh.write("#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> " + SHIM_LOG + "\n")
    os.chmod(os.path.join(SHIM_DIR, name), 0o755)
open(SHIM_LOG, "w").close()
open(LOG, "a").write(f"--- test7 start {time.strftime('%H:%M:%S')}\n")
log_start = len(open(LOG).read())

sock = os.path.join(SOCK_DIR, "t7.sock")
print("starting a real session on a pty", flush=True)
s = Session(sock)
try:
    ready = s.read_until(r"\? for shortcuts|Try \"|Welcome|>", 90)
    check("the session drew its first screen", ready, s.text()[-300:])
    # Keys typed before the input box exists are lost, not buffered: the
    # first version typed two seconds after the banner and its prompt was
    # never answered, while a message pushed over the socket a minute later
    # was. Wait for the box itself.
    boxed = s.read_until(r"for short", 60)
    check("the input box is on screen", boxed, s.text()[-400:])
    time.sleep(3)
    check("the explicit messaging socket exists", os.path.exists(sock))

    print("1  a reply finishes -> Stop hook", flush=True)
    s.type("Reply with exactly the single word alpha, written in capital letters")
    # The reply must differ from the prompt: the pty echoes what was typed,
    # and a lowercase alpha on screen proves only that the keys arrived.
    got = s.read_until(r"ALPHA", 90) and wait_for_log("stopped:", 60)
    check("the Stop hook ran after the reply", got, "screen: " + s.text()[-500:])
    rec = json.load(open(INBOX)) if os.path.exists(INBOX) else {}
    check("the hook recorded the session's socket", rec.get("socket") == sock, str(rec))
    check("and its token", bool(rec.get("token")))
    calls = shim_calls()
    stopped = [c for c in calls if "--id claude_stopped" in c]
    check("a 'Claude stopped' bubble was posted", bool(stopped), str(calls[-3:]))
    check("the bubble carries the reply's first words",
          any("ALPHA" in c for c in stopped), str(stopped[-1:]))
    check("the bubble has a Continue button that runs ccpush",
          any("--button2 Continue --button2-action /data/data/com.termux/files/usr/bin/ccpush" in c
              for c in stopped), str(stopped[-1:]))

    print("2  ccpush lands in the session and is answered", flush=True)
    before = len(s.text())
    r = subprocess.run(["python3", CCN, "push", "Reply with exactly the single word bravo, written in capital letters"],
                       capture_output=True, text=True, timeout=20,
                       env=dict(os.environ, PATH=SHIM_DIR + ":" + os.environ["PATH"]))
    check("ccpush reports it was sent", r.returncode == 0, r.stdout + r.stderr)
    answered = s.read_until(r"BRAVO", 90)
    check("the session ANSWERED the pushed message", answered, s.text()[before:][-400:])
    check("the stale bubbles were cleared on push",
          any("termux-notification-remove claude_stopped" in c for c in shim_calls()))
    check("ccpush works from the Termux spelling of home too",
          subprocess.run(["python3", CCN, "status"], capture_output=True, text=True).returncode == 0)

    print("3  a permission prompt -> Notification hook", flush=True)
    wait_for_log("stopped:", 60)
    s.type("Use the Bash tool to run exactly this command: cat /etc/hostname")
    asked = wait_for_log("needs: permission_prompt", 90)
    check("the Notification hook ran for the permission prompt", asked)
    needs = [c for c in shim_calls() if "--id claude_needs_you" in c]
    check("a 'needs a permission' bubble was posted", bool(needs), str(shim_calls()[-2:]))
    check("it is ongoing, so it stays until answered",
          any("--ongoing" in c for c in needs))
    # answer it: Esc declines, and we leave the session
    os.write(s.master, b"\x1b")
    time.sleep(1.5)
finally:
    s.close()
    try:
        os.remove(sock)
    except OSError:
        pass

print("4  one REAL bubble, unshimmed, so a person can see it arrived", flush=True)
r = subprocess.run(["python3", CCN, "needs"], input=json.dumps(
    {"hook_event_name": "Notification", "notification_type": "idle_prompt",
     "message": "test7: this bubble is real - the hooks are wired"}),
    capture_output=True, text=True, timeout=20)
check("the real post returned 0", r.returncode == 0, r.stdout + r.stderr)

import shutil; shutil.rmtree(SHIM_DIR, ignore_errors=True)
print(f"\n{count} checks, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
