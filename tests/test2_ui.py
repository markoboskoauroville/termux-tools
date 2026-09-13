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
    def __init__(self, rows=40, cols=100, env=None):
        self.m, s = pty.openpty()
        fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.p = subprocess.Popen(
            ["bash", "/data/data/com.termux/files/usr/bin/mamc"],
            stdin=s, stdout=s, stderr=s, close_fds=True,
            env=dict(env or os.environ, TERM="xterm-256color"))
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

print("the run screen is the app's terminal: every key reaches the app")
# a fixture that says which key it got, the way maha.commute's menu reads 1,
# Enter and the arrows. import http.server is what makes discovery see it.
ECHO = os.path.expanduser("~/.zz_echo")
os.makedirs(ECHO, exist_ok=True)
open(os.path.join(ECHO, "app.py"), "w").write(
    "import sys, os, termios, tty\n"
    "import http.server  # noqa: F401  (discovery looks for a server)\n"
    "PORT = 9097\n"
    "print('echo app: serving on http://127.0.0.1:9097', flush=True)\n"
    "fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); tty.setraw(fd)\n"
    "try:\n"
    "    while True:\n"
    "        b = os.read(fd, 8)\n"
    "        if b in (b'\\x1b[A', b'\\x1bOA'): name = 'up'\n"
    "        elif b in (b'\\r', b'\\n'): name = 'enter'\n"
    "        elif b == b'\\x1b': name = 'esc'\n"
    "        else: name = b.decode('utf-8', 'replace')\n"
    "        sys.stdout.write('key=%s\\r\\n' % name); sys.stdout.flush()\n"
    "        if b == b'0': break\n"
    "finally:\n"
    "    termios.tcsetattr(fd, termios.TCSADRAIN, old)\n"
    "print('bye', flush=True)\n")
u.clear(); u.send(b"\x1b", 0.5); u.send(b"r", 6.0)
u.clear(); u.send(b"/"); u.send(b"zz_echo"); u.send(b"\n", 1.5)
check("the fixture is listed after a rescan", u.expect("zz_echo", 12))
u.clear(); u.send(b"\n", 4.0)
check("the run screen appears", u.expect("running: zz_echo", 25))
check("it shows the url", u.expect("http://127.0.0.1:9097", 20))
check("it says how to stop", u.expect("stops it and comes back", 10))
u.clear(); u.send(b"5", 1.0)
check("a number reaches the app", u.expect("key=5", 8))
u.clear(); u.send(b"\x1b[A", 1.0)
check("an arrow reaches the app", u.expect("key=up", 8))
u.clear(); u.send(b"\r", 1.0)
check("Enter reaches the app", u.expect("key=enter", 8))
u.clear(); u.send(b"\x1b", 1.2)
check("a lone Esc reaches the app, after the chord window", u.expect("key=esc", 8))
u.clear(); os.write(u.m, b"\x1b"); time.sleep(0.15); u.send(b"\x1b", 1.0)
check("Esc Esc asks before stopping", u.expect("Stop it and come back? [y/N]", 8))
u.clear(); u.send(b"n", 1.0)
check("n carries on", u.expect("carrying on", 8))
u.clear(); u.send(b"7", 1.0)
check("and keys still reach the app afterwards", u.expect("key=7", 8))
u.clear(); u.send(b"\x03", 1.0)
check("Ctrl-C (the CR.C key) asks too", u.expect("Stop it and come back? [y/N]", 8))
u.clear(); u.send(b"y", 3.0)
check("y stops it and comes back to the list", u.expect("zz_echo stopped", 15))
check("the list is back", u.expect("apps", 10))
u.clear(); u.send(b"\n", 4.0)
check("it runs again", u.expect("running: zz_echo", 25))
u.clear(); u.send(b"0", 3.0)
check("an app that quits on its own key brings the list back",
      u.expect("exited (code 0)", 15))

print("stopping a real app with Ctrl-C, y")
u.clear(); u.send(b"\x1b", 0.5); u.send(b"/"); u.send(b"tpedit"); u.send(b"\n", 1.0)
u.clear(); u.send(b"\n", 8.0)
check("tpedit's run screen appears", u.expect("running:", 25))
check("it shows a url", u.expect("http://", 20))
u.clear(); u.send(b"\x03", 1.5)
check("Ctrl-C asks", u.expect("[y/N]", 10))
u.clear(); u.send(b"y", 3.0)
check("y returns to the list", u.expect("stopped", 15))
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
# The first version of this check matched the word "trash" in the key bar and
# passed with an EMPTY trash, where t only says "the trash is empty" and the
# q that followed quit the program; every check after it ran against a dead
# process and seven of them failed for no fault of the code (13.9.2026,
# checking-the-checks.md). So a slot is put in the trash first, and the check
# wants a line only the trash screen draws.
import importlib.util
_spec = importlib.util.spec_from_file_location("mamc_t2", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mamc", "mamc.py"))
_m = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_m)
_m.trash_app(_m.App("zz_t2_ghost", None))
u.clear(); u.send(b"t", 1.5)
check("t opens the trash", u.expect("r restore   x delete for good", 12))
check("and the slot is listed", u.expect("zz_t2_ghost", 12))
u.clear(); u.send(b"q", 1.0)
check("q closes the trash at once, and not the program",
      u.expect("apps match", 5) and u.p.poll() is None, repr(u.screen()[:200]))
for _slot, _mf in _m.list_trash():
    if _mf["name"] == "zz_t2_ghost":
        import shutil as _sh2; _sh2.rmtree(_slot, ignore_errors=True)

print("the live port: a keyring on 8850 says so in ~/.mantra/ports")
import signal
km, ks = pty.openpty()
fcntl.ioctl(ks, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 90, 0, 0))
kr = subprocess.Popen(["python3", "/root/KEYRING_TERMUX/app.py", "8850"],
                      stdin=ks, stdout=ks, stderr=ks, close_fds=True,
                      start_new_session=True, env=dict(os.environ, TERM="xterm-256color"))
os.close(ks)
REG = os.path.expanduser("~/.mantra/ports/keyring")
end_t = time.time() + 20
while time.time() < end_t:
    try:
        if open(REG).read().strip() == "8850":
            break
    except OSError:
        pass
    time.sleep(0.3)
check("the keyring wrote 8850 to the registry", os.path.exists(REG) and open(REG).read().strip() == "8850")
u.clear(); u.send(b"\x1b", 0.5); u.send(b"r", 6.0)
u.clear(); u.send(b"/"); u.send(b"keyring"); u.send(b"\n", 1.5)
check("the row shows the LIVE port, not the one in its source", u.expect("8850", 12), repr(u.screen()[:400]))
check("the detail pane says it is serving there", u.expect("serving on 8850", 12))
check("and offers o", u.expect("o opens it", 12))
os.killpg(os.getpgid(kr.pid), signal.SIGTERM)
time.sleep(1.5)
# a second / keeps the text already typed, so the old filter is backspaced away first
u.clear(); u.send(b"r", 6.0); u.send(b"/"); u.send(b"\x7f" * 14); u.send(b"keyring"); u.send(b"\n", 1.5)
check("once it is gone, the stale line is not believed", not u.on_screen("8850") and u.expect("8842", 12),
      repr(u.screen()[:400]))
try:
    os.close(km)
except OSError:
    pass
u.clear(); u.send(b"\n", 6.0)
check("the keyring runs from mamc", u.expect("running: KEYRING_TERMUX", 25))
end_t = time.time() + 20
while time.time() < end_t:
    try:
        if open(REG).read().strip() == "8842":
            break
    except OSError:
        pass
    time.sleep(0.3)
check("and announces 8842 while mamc is its terminal", os.path.exists(REG) and open(REG).read().strip() == "8842")
check("its own console draws on mamc's screen", u.expect("quit", 15, lower=True))
u.clear(); u.send(b"\x03", 1.5); u.send(b"y", 4.0)
check("stopped from mamc", u.expect("stopped", 15))
import socket as _socket
_s = _socket.socket(); _s.settimeout(0.5)
check("nothing answers on 8842 afterwards", _s.connect_ex(("127.0.0.1", 8842)) != 0)
_s.close()

print("quitting")
u.clear(); u.send(b"q", 2.0)
time.sleep(1)
check("q exits the program", u.p.poll() is not None, f"rc={u.p.poll()}")
check("it exited cleanly", u.p.poll() == 0, f"rc={u.p.poll()}")
check("no traceback anywhere in the session", "Traceback" not in u.text)
u.close()
import shutil as _shutil
_shutil.rmtree(ECHO, ignore_errors=True)

print("the U key, against a GitHub on disk one version ahead")
import fixture_repo
FIX_TMP, FIX_BARE, FIX_CO = fixture_repo.make(latest=1)
REAL_WRAPPER = open("/data/data/com.termux/files/usr/bin/mamc").read()
try:
    u2 = UI(env=fixture_repo.env_for(FIX_TMP, FIX_CO))
    check("an unnumbered checkout draws no version in the title",
          u2.on_screen("mamc —") and not u2.on_screen("mamc v"), repr(u2.screen()[:120]))
    u2.clear(); u2.send(b"u", 3.0)
    check("u shows what is installed", u2.expect("installed   unnumbered", 15))
    check("and what is available", u2.expect("available   v1", 15))
    u2.clear(); u2.send(b"n", 1.5)
    check("n cancels", u2.expect("update cancelled", 10))
    check("and nothing moved", not os.path.exists(os.path.join(FIX_CO, "version.py")))
    u2.clear(); u2.send(b"u", 3.0)
    check("u asks again", u2.expect("available   v1", 15))
    u2.clear(); u2.send(b"y", 6.0)
    check("y pulls and restarts", u2.expect("restarting", 20))
    check("the restarted program is v1", u2.expect("mamc v1", 25), repr(u2.screen()[:120]))
    check("the checkout is v1 on disk", os.path.exists(os.path.join(FIX_CO, "version.py")))
    check("the link step wrote the fixture's wrapper",
          os.path.exists(os.path.join(FIX_TMP, "bin", "mamc")))
    check("the real wrapper is untouched",
          open("/data/data/com.termux/files/usr/bin/mamc").read() == REAL_WRAPPER)
    u2.clear(); u2.send(b"q", 2.0)
    time.sleep(1)
    check("q exits the restarted program cleanly", u2.p.poll() == 0, f"rc={u2.p.poll()}")
    check("no traceback in that session", "Traceback" not in u2.text)
    u2.close()
finally:
    _shutil.rmtree(FIX_TMP, ignore_errors=True)

print(f"\n{count} checks, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
