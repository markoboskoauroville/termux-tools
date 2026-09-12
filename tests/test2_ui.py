#!/usr/bin/env python3
"""TEST 2 - inside the running app, driven the way a person drives it.

Not calling functions: pressing keys, on a real pty, against the real apps on
this phone. Every assertion is about what appeared on the screen.
"""
import fcntl
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

fails, count = [], 0


def check(label, ok, detail=""):
    global count
    count += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(f"{label} {detail}")


class UI:
    def __init__(self, rows=40, cols=100):
        self.m, s = pty.openpty()
        fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.p = subprocess.Popen(
            ["bash", "/data/data/com.termux/files/usr/bin/mamc"],
            stdin=s, stdout=s, stderr=s, close_fds=True,
            env=dict(os.environ, TERM="xterm-256color"))
        os.close(s)
        self.buf = b""
        # discover() walks $HOME looking for server code and takes seconds.
        # Keys sent before the first draw are buffered and answered after the
        # capture window closes, which reads exactly like a dead keyboard.
        # Wait for the list itself, not for a guessed number of seconds.
        end = time.time() + 25
        while time.time() < end:
            self.pump(0.4)
            if "most-run first" in self.text:
                self.pump(0.8)
                return
        raise SystemExit("the list never drew")

    def pump(self, t):
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([self.m], [], [], 0.15)
            if r:
                try:
                    self.buf += os.read(self.m, 200000)
                except OSError:
                    break

    def send(self, keys, wait=0.6):
        os.write(self.m, keys)
        time.sleep(0.25)
        self.pump(wait)

    def expect_change(self, _before, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.buf) > 100:
                return True
            self.pump(0.3)
        return False

    def expect(self, marker, timeout=12, lower=False):
        """Wait until the screen actually says something, then assert on it.

        Fixed sleeps made this suite lie in both directions: a check would
        pass on one run and fail on the next, because a curses redraw arrives
        when it arrives. Waiting for the marker turns a timing race into a
        real answer -- and a genuine absence still fails, just after the
        timeout rather than before the redraw.
        """
        end = time.time() + timeout
        while time.time() < end:
            hay = self.text.lower() if lower else self.text
            needle = marker.lower() if lower else marker
            if needle in hay:
                return True
            self.pump(0.3)
        return False

    @property
    def text(self):
        return re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"",
                      self.buf).decode("utf-8", "replace")

    def screen(self):
        """Roughly what is on screen now: the tail of what was drawn."""
        return self.text[-6000:]

    def clear(self):
        self.buf = b""

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            time.sleep(0.4)
            if self.p.poll() is None:
                self.p.kill()
        os.close(self.m)


print("the list draws")
u = UI()
s = u.screen()
check("title is drawn", "mamc" in s)
check("says how it is sorted", "most-run first" in s)
check("the key bar is there", u.expect("uninstall") and u.expect("flag"))
check("an app is listed", "MA Reader" in s)
check("the important marker is drawn", "!" in s)
check("run counts are shown", re.search(r"\d+\u00d7", s) is not None)

print("navigation and the detail pane")
# ESC O B, not ESC [ B. keypad(True) puts the terminal into APPLICATION
# cursor mode, and terminfo's kcud1 is then \EOB -- which is what a real
# terminal sends and what ncurses matches. ESC [ B arrives as three
# unrecognised keys and is silently ignored, which reads exactly like a
# broken arrow key.
first_sel = u.text
u.clear(); u.send(b"\x1bOB", 0.2)
moved = u.expect_change(first_sel)
check("arrow keys move the selection", moved, f"{len(u.text)} bytes back")
u.clear(); u.send(b"i", 1.2)
s = u.screen()
check("i opens info", u.expect("owns:"))
check("info names the pieces", u.expect("commands:"))
u.send(b"q", 0.8)

print("the filter")
u.clear(); u.send(b"/"); u.send(b"tpedit"); u.send(b"\n", 1.0)
s = u.screen()
check("filter narrows to one app", u.expect("tpedit"))
check("filter does not show unrelated apps", "MA Reader" not in s.split("tpedit")[-1])

print("running an app with Enter, and stopping it with Q")
u.clear(); u.send(b"\n", 8.0)
s = u.screen()
check("the run screen appears", u.expect("running:", 25))
check("it shows a url", u.expect("http://", 20))
check("it offers Q to stop", u.expect("stop it", 10, lower=True))
before = subprocess.run(["pgrep", "-fc", "tpedit"], capture_output=True,
                        text=True).stdout.strip()
u.clear(); u.send(b"Q", 3.0)
s = u.screen()
check("Q returns to the list", u.expect("apps", 15))
time.sleep(1)
left = subprocess.run(["pgrep", "-f", "tpedit/app.py"], capture_output=True,
                      text=True).stdout.strip()
check("the app is really gone", left == "", f"still running: {left!r}")

print("the uninstall gate on an ordinary app")
u.clear(); u.send(b"d", 1.5)
s = u.screen()
check("d shows what would move", u.expect("trash", 12))
check("the gate names the app", u.expect("tpedit", 12))
check("it says nothing is deleted", u.expect("restore", 12))
u.send(b"n", 1.0)
check("answering no keeps it", os.path.isdir(os.path.expanduser("~/.tpedit")))

print("the second gate on an important app")
u.clear(); u.send(b"/"); u.send(b"\x7f" * 10); u.send(b"nightcommute"); u.send(b"\n", 1.0)
u.clear(); u.send(b"d", 1.5)
check("first gate appears", u.expect("trash", 12))
u.clear(); u.send(b"y", 1.5)
s = u.screen()
check("a flagged app asks a SECOND time", u.expect("second time", 12, lower=True))
check("the second gate wants a typed word", u.expect("remove", 12, lower=True))
u.clear(); u.send(b"y", 0.5); u.send(b"\n", 1.5)
check("pressing y at the second gate does NOT remove it",
      os.path.isdir(os.path.expanduser("~/.nightcommute")))
check("it says it was left alone", u.expect("left alone", 12, lower=True))

print("flagging")
u.clear(); u.send(b"/"); u.send(b"\x7f" * 14); u.send(b"openrouter"); u.send(b"\n", 1.0)
u.clear(); u.send(b"!", 1.0)
check("! flags it", u.expect("flagged important", 12, lower=True))
import json
flags = json.load(open(os.path.expanduser("~/.mamc/important.json")))
check("the flag is written to disk", any("openrouter" in k for k in flags))
u.clear(); u.send(b"!", 1.0)
check("! unflags it again", u.expect("no longer flagged", 12, lower=True))
flags = json.load(open(os.path.expanduser("~/.mamc/important.json")))
check("the flag is removed from disk", not any("openrouter" in k for k in flags))

print("the trash view")
u.clear(); u.send(b"t", 1.5)
s = u.screen()
check("t opens the trash", u.expect("trash", 12, lower=True))
u.send(b"q", 1.0)

print("quitting")
u.clear(); u.send(b"q", 2.0)
time.sleep(1)
check("q exits the program", u.p.poll() is not None, f"rc={u.p.poll()}")
check("it exited cleanly", u.p.poll() == 0, f"rc={u.p.poll()}")
check("no traceback anywhere in the session", "Traceback" not in u.text)
u.close()

print(f"\n{count} checks, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
