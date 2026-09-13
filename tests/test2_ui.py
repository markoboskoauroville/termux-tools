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
import struct
import subprocess
import sys
import termios
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vt import Screen   # noqa: E402  the screen model, tests/vt.py

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
        self.vt = Screen(rows, cols)
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
                    chunk = os.read(self.m, 200000)
                except OSError:
                    break
                self.buf += chunk
                self.vt.feed(chunk)

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
            # Two places to look. The stream since clear() holds anything
            # that was drawn, including a message that has already gone.
            # The screen model holds what is there NOW, whole -- curses
            # sends only the cells that changed, so a word on screen may
            # never have crossed the pty in one piece (tests/vt.py).
            hay = self.text + "\n" + self.screen()
            needle = marker
            if lower:
                hay, needle = hay.lower(), marker.lower()
            if needle in hay:
                return True
            self.pump(0.3)
        return False

    def on_screen(self, marker, lower=False):
        """Only what is on the screen at this moment, nothing that was."""
        scr = self.screen()
        return (marker.lower() in scr.lower()) if lower else (marker in scr)

    @property
    def text(self):
        return re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"",
                      self.buf).decode("utf-8", "replace")

    def screen(self):
        """What is on screen now, row by row, from the screen model."""
        return self.vt.text()

    def clear(self):
        self.buf = b""

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            time.sleep(0.4)
            if self.p.poll() is None:
                self.p.kill()
        os.close(self.m)


print("the first screen is folders")
u = UI()
s = u.screen()
check("title is drawn", "mamc" in s)
check("says how it is sorted", "most-run first" in s)
check("says it is folders", "folders," in s)
check("the key bar offers open", u.expect("open"))
check("a folder is listed", "Commute" in s and "Tools" in s)
check("run counts are shown", re.search(r"\d+\u00d7", s) is not None)
check("the detail pane lists the folder's apps", u.expect("enter opens it"))

print("opening a folder, and backing out of it")
u.clear(); u.send(b"\n", 1.0)
check("enter opens the folder", u.expect("apps, most-run first", 12) and "\u25c2" in u.text)
check("the key bar now offers run and back", u.expect("uninstall", 12) and u.expect("back", 12))
check("an app row is drawn", u.expect("\u00d7", 12) or u.expect("-", 12))
u.clear(); u.send(b"\x1bOD", 1.0)          # left arrow, application mode
check("left arrow goes back to the folders", u.expect("folders,", 12))
u.clear(); u.send(b"d", 1.0)
check("d on a folder says to open it first", u.expect("that is a folder", 12, lower=True))

print("the filter searches every folder at once")
u.clear(); u.send(b"/"); u.send(b"nightcommute"); u.send(b"\n", 1.0)
check("an app is found without opening its folder", u.expect("night.commute", 12))
check("the header says how many match", u.expect("apps match", 12))
u.clear(); u.send(b"\x1bOD", 1.0)
check("left arrow clears the filter, back to folders", u.expect("folders,", 12))

print("moving an app to another folder")
import json as _json
CATS = os.path.expanduser("~/.mamc/categories.json")
_cats_before = open(CATS).read() if os.path.exists(CATS) else None
u.clear(); u.send(b"/"); u.send(b"openrouter"); u.send(b"\n", 1.0)
u.clear(); u.send(b"c", 1.0)
# curses redraws only what changed, and " m" at the top-left was already
# there from " mamc", so the stream reads "ove openrouter to". The second
# line is new in full.
check("c opens the move screen", u.expect("now in ", 12))
u.clear(); u.send(b"j" * 15, 0.8); u.send(b"\n", 1.0)     # the last entry: a new folder
check("the last choice asks for a new name", u.expect("a new folder", 12))
u.clear(); u.send(b"Zeta"); u.send(b"\n", 1.5)
check("it says where it went", u.expect("openrouter \u2192 Zeta", 12))
_c = _json.load(open(CATS))
check("the choice is written to disk, keyed by path",
      _c.get(os.path.expanduser("~/openrouter")) == "Zeta", str(_c))
u.clear(); u.send(b"\x1bOD", 1.0)
check("the new folder is on the first screen", u.expect("Zeta", 12))
u.clear(); u.send(b"/"); u.send(b"openrouter"); u.send(b"\n", 1.0)
u.clear(); u.send(b"c", 1.0); u.send(b"k" * 15, 0.8); u.send(b"\n", 1.5)
_c = _json.load(open(CATS))
check("choosing an existing folder replaces the choice",
      _c.get(os.path.expanduser("~/openrouter")) != "Zeta", str(_c))
u.clear(); u.send(b"\x1bOD", 1.0)
check("the empty folder is gone from the first screen", u.expect("folders,", 12) and "Zeta" not in u.screen())
if _cats_before is None:
    os.remove(CATS)
else:
    open(CATS, "w").write(_cats_before)
u.clear(); u.send(b"r", 3.0)

print("navigation and the detail pane")
u.clear(); u.send(b"\n", 1.0)      # open the first folder: the flows below want app rows
check("inside a folder again", u.expect("apps, most-run first", 12))
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

print("a filter that matches nothing, and every navigation key on it")
# The monkey found this one: min(len - 1, idx + 1) on an empty list is -1,
# and filtered[-1] of nothing raised IndexError out of draw(). A row-walk
# over an empty list is a case the suite never pressed on purpose.
u.clear(); u.send(b"/"); u.send(b"\x7f" * 14); u.send(b"zzqx-nothing"); u.send(b"\n", 1.0)
check("it says nothing matches", u.expect("0 apps match", 12) and u.expect("nothing here", 12))
# End and Home in application cursor mode (ESC O F / ESC O H), the form a
# real terminal sends while keypad(True) is on. The CSI form (ESC [ F) is
# not one key here: it is Esc, which is "back", and then two letters.
for key in (b"\x1bOB", b"j", b"\x1bOA", b"k", b"\x1b[6~", b"\x1b[5~", b"\x1bOF", b"\x1bOH", b"\n", b"i", b"c", b"d"):
    u.send(key, 0.3)
u.pump(1.0)
check("Down, Up, PageDown, PageUp, End, Home, Enter, i, c, d on the empty list do not crash it",
      u.p.poll() is None and "Traceback" not in u.text, f"rc={u.p.poll()}")
check("and the screen still shows the empty list", u.on_screen("0 apps match") and u.on_screen("nothing here"),
      repr(u.screen()[:300]))
u.clear(); u.send(b"\x7f", 1.0)
check("backspace clears the filter and the list is back", u.expect("most-run first", 12) and not u.on_screen("apps match"),
      repr(u.screen()[:300]))
u.clear(); u.send(b"/"); u.send(b"tpedit"); u.send(b"\n", 1.0)
check("the filter still works afterwards", u.expect("1 apps match", 12) and u.on_screen("tpedit"), repr(u.screen()[:300]))

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
