#!/usr/bin/env python3
"""claudelogin - sign Claude Code in as a DIFFERENT account, with single keys.

The screen is the same kind as mamc's: a key bar at the bottom, every key one press, no Enter.

    o   sign the browser out of claude.ai, then open the login page in Chrome (mamc's open_page:
        termux-open-url <url> com.android.chrome), so it asks which account
    x   only the sign-out of the browser
    p   try a PRIVATE Chrome tab (Chrome often ignores this from another app)
    c   copy the login link to the clipboard
    v   paste the code from the clipboard into the login, and send it
    s   who am I signed in as
    r   start the login again
    q   quit

The workflow: claudelogin, o, sign in in the browser and copy the code it shows, come back, v.

Why the old account kept coming back: the browser is still signed in to claude.ai, and the login
page approves that account without asking. claudelogin logs Claude Code out first (no old token
to reuse), and o signs the browser out of claude.ai before it opens the login. A private tab was
tried first and Chrome did not honour it from another app (Marko, 12.10.2026).

Environment, for the tests: CLAUDELOGIN_CLAUDE (the claude command), CLAUDELOGIN_OPEN (a command
that takes the url), CLAUDELOGIN_CLIP (a command that prints the clipboard).
"""
import curses
import fcntl
import importlib.util
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = shlex.split(os.environ.get("CLAUDELOGIN_CLAUDE", "claude"))
URL_RE = re.compile(r"https://[^\s\x1b\"'<>]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\r")
CHROME = "com.android.chrome"
LOGOUT_URL = "https://claude.ai/logout"
sys.path.insert(0, os.path.join(HERE, ".."))
import theme  # noqa: E402  the orange terminal, shared by every screen


def version():
    try:
        m = re.search(r"^APP_VERSION *= *(\d+)", open(os.path.join(HERE, "..", "version.py")).read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def load_mamc_open_page():
    """One program, one place: the Chrome chain is mamc's, not a copy of it."""
    path = os.path.join(HERE, "..", "mamc", "mamc.py")
    try:
        spec = importlib.util.spec_from_file_location("mamc_for_claudelogin", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.open_page
    except Exception:
        return None


def bounded(cmd):
    return (["timeout", "-k", "5", "30"] + cmd) if shutil.which("timeout") else cmd


def open_url(url):
    """Returns what was used, or '' when nothing could be."""
    custom = os.environ.get("CLAUDELOGIN_OPEN")
    if custom:
        try:
            subprocess.Popen(shlex.split(custom) + [url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return shlex.split(custom)[0]
        except Exception:
            return ""
    fn = load_mamc_open_page()
    return fn(url) if fn else ""


def open_private(url):
    """Chrome's incognito tab. am prints its failures and still exits zero, so read the output."""
    if not shutil.which("am"):
        return ""
    cmd = ["am", "start", "--user", "0", "-a", "android.intent.action.VIEW", "-d", url,
           "-p", CHROME, "--ez", "create_new_tab", "true",
           "--ez", "com.google.android.apps.chrome.EXTRA_OPEN_NEW_INCOGNITO_TAB", "true"]
    try:
        r = subprocess.run(bounded(cmd), capture_output=True, text=True, timeout=35)
    except Exception:
        return ""
    out = (r.stdout + r.stderr).lower()
    if any(w in out for w in ("error", "exception", "not found", "does not exist", "unable to resolve")):
        return ""
    return "chrome private tab"


def clip_get():
    custom = os.environ.get("CLAUDELOGIN_CLIP")
    cmds = [shlex.split(custom)] if custom else [
        c for c in (["termux-clipboard-get"], ["pbpaste"], ["wl-paste"], ["xclip", "-o", "-selection", "clipboard"])
        if shutil.which(c[0])]
    for c in cmds:
        try:
            r = subprocess.run(bounded(c), capture_output=True, text=True, timeout=35)
            if r.returncode == 0:
                return r.stdout
        except Exception:
            continue
    return None


def clip_set(text):
    cmds = [c for c in (["termux-clipboard-set"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"])
            if shutil.which(c[0])]
    for c in cmds:
        try:
            subprocess.run(bounded(c), input=text, text=True, timeout=35, capture_output=True)
            return True
        except Exception:
            continue
    return False


def auth_status():
    try:
        r = subprocess.run(CLAUDE + ["auth", "status", "--json"], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)
    except Exception:
        return None


def logged_in_text(st):
    if not st or not st.get("loggedIn"):
        return ""
    who = st.get("email") or st.get("accountEmail") or st.get("authMethod") or "signed in"
    org = st.get("orgName") or st.get("subscriptionType") or ""
    return f"{who} {org}".strip()


class Login:
    """claude auth login, on a pty wide enough that its URL never wraps."""

    def __init__(self):
        self.master = None
        self.proc = None
        self.text = ""
        self.url = ""
        self.done = False
        self.rc = None

    def start(self):
        self.stop()
        self.text, self.url, self.done, self.rc = "", "", False, None
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))
        env = dict(os.environ, BROWSER="true", TERM="dumb")
        self.proc = subprocess.Popen(CLAUDE + ["auth", "login", "--claudeai"], stdin=slave, stdout=slave,
                                     stderr=slave, env=env, start_new_session=True, close_fds=True)
        os.close(slave)

    def pump(self):
        if self.master is None:
            return
        while True:
            r, _, _ = select.select([self.master], [], [], 0)
            if not r:
                break
            try:
                data = os.read(self.master, 65536)
            except OSError:
                data = b""
            if not data:
                break
            self.text += ANSI_RE.sub("", data.decode("utf-8", "replace"))
        if not self.url:
            m = URL_RE.search(self.text)
            if m:
                self.url = m.group(0).rstrip(".,)")
        if self.proc and self.proc.poll() is not None and not self.done:
            self.done, self.rc = True, self.proc.returncode

    def send(self, s):
        if self.master is not None and not self.done:
            os.write(self.master, s.encode())
            return True
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass
        self.master = self.proc = None


def logout():
    try:
        subprocess.run(CLAUDE + ["auth", "logout"], capture_output=True, timeout=60)
    except Exception:
        pass
    try:
        os.remove(os.path.expanduser("~/.claude/.credentials.json"))
    except OSError:
        pass


class UI:
    def __init__(self, scr):
        self.scr, self.login = scr, Login()
        self.msg, self.bad = "", False
        self.pasted, self.who = False, ""
        self.last_check = 0.0
        curses.curs_set(0)
        curses.start_color()
        cols = theme.colors()
        for i, role in enumerate(("norm", "head", "dim", "ok", "warn", "key"), 1):
            curses.init_pair(i, *cols[role])
        scr.bkgd(" ", curses.color_pair(1))
        scr.timeout(100)
        scr.keypad(True)
        self.say("logging out the current account...")
        self.draw()
        logout()
        self.say("")
        self.login.start()

    def say(self, m, bad=False):
        self.msg, self.bad = m, bad

    def put(self, y, x, s, pair=1, bold=False):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w - 1:
            try:
                self.scr.addstr(y, x, s[: w - 1 - x], curses.color_pair(pair) | (curses.A_BOLD if bold else 0))
            except curses.error:
                pass

    @staticmethod
    def wrap(s, width):
        return [s[i:i + width] for i in range(0, len(s), width)] or [""]

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        self.put(0, 0, f" claudelogin v{version()} ".ljust(w - 1), 2, True)
        self.put(2, 1, "1  logged out", 4)
        if self.login.url:
            lines = self.wrap(self.login.url, max(10, w - 6))[:6]
            self.put(3, 1, "2  login page ready - press o", 4)
            for i, line in enumerate(lines):
                self.put(4 + i, 4, line, 3)
            y = 4 + len(lines)
        else:
            self.put(3, 1, "2  waiting for the login link...", 3)
            y = 4
        y += 1
        if self.who:
            self.put(y, 1, f"3  signed in: {self.who}", 4, True)
        elif self.login.done:
            self.put(y, 1, f"3  login ended (exit {self.login.rc}) - press r to try again", 5)
        elif self.pasted:
            self.put(y, 1, "3  code sent - waiting for Claude to confirm...", 3)
        else:
            self.put(y, 1, "3  sign in in the browser, copy the code, come back, press v", 3)
        if self.msg:
            self.put(y + 2, 1, self.msg, 5 if self.bad else 4)
        self.put(h - 3, 1, "o signs the browser out of claude.ai, then opens the login, so it asks", 3)
        self.put(h - 2, 1, "which account. (It signs you out of claude.ai in Chrome as well.)", 3)
        keys = [("o", "open"), ("v", "paste code"), ("x", "sign out"), ("c", "copy link"),
                ("s", "status"), ("r", "again"), ("q", "quit")]
        x = 1
        for k, label in keys:
            if x + len(k) + len(label) + 3 > w:
                break
            self.put(h - 1, x, f" {k} ", 6, True)
            x += len(k) + 2
            self.put(h - 1, x, label + " ", 1)
            x += len(label) + 2
        self.scr.refresh()

    def open_fresh(self, url):
        """The browser signed in to claude.ai is what picks the old account. Chrome does not honour
        a private-tab request from another app, so sign it out instead, give that page time to
        finish, then open the login: it has no account to reuse and asks which one."""
        if not open_url(LOGOUT_URL):
            self.say("could not open a browser - press c and paste the link yourself", True)
            return
        time.sleep(float(os.environ.get("CLAUDELOGIN_DELAY", "4")))
        used = open_url(url)
        self.say("login page opened - sign in, copy the code, come back, press v" if used else
                 "could not open the login - press c and paste the link yourself", not used)

    def need_url(self):
        if not self.login.url:
            self.say("no login link yet - wait a moment", True)
            return False
        return True

    def check_signed_in(self):
        t = logged_in_text(auth_status())
        if t:
            self.who = t
            self.say("done. press q to leave.")
        return bool(t)

    def key(self, k):
        c = chr(k).lower() if 0 <= k < 256 else ""
        if c == "q":
            return False
        if c == "o":
            if self.need_url():
                self.say("signing the browser out of claude.ai first, then the login opens...")
                threading.Thread(target=self.open_fresh, args=(self.login.url,), daemon=True).start()
        elif c == "x":
            used = open_url(LOGOUT_URL)
            self.say("opened claude.ai/logout - the browser forgets the old account" if used else
                     "could not open a browser", not used)
        elif c == "p":
            if self.need_url():
                if open_private(self.login.url):
                    self.say("opened in a private Chrome tab")
                else:
                    self.say("no private tab from here - press c, paste the link into a private tab", True)
        elif c == "c":
            if self.need_url():
                ok = clip_set(self.login.url)
                self.say("link copied" if ok else "no clipboard here (pkg install termux-api)", not ok)
        elif c == "v":
            text = clip_get()
            if text is None:
                self.say("cannot read the clipboard (pkg install termux-api, and the Termux:API app)", True)
            else:
                code = "".join(text.split())
                if len(code) < 8:
                    self.say("the clipboard holds no code - copy it in the browser first", True)
                elif not self.login.send(code + "\r"):
                    self.say("the login is not running - press r", True)
                else:
                    self.pasted = True
                    self.say(f"pasted {len(code)} characters")
        elif c == "s":
            self.say("checking...")
            self.draw()
            if not self.check_signed_in():
                self.say("not signed in yet", True)
        elif c == "r":
            self.pasted, self.who = False, ""
            self.login.start()
            self.say("login restarted")
        return True

    def loop(self):
        while True:
            self.login.pump()
            if not self.who and (self.pasted or (self.login.done and self.login.rc == 0)) \
                    and time.time() - self.last_check > 2:
                self.last_check = time.time()
                self.check_signed_in()
            self.draw()
            k = self.scr.getch()
            if k != -1 and not self.key(k):
                break
        self.login.stop()


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if argv[:1] == ["--status"]:
        t = logged_in_text(auth_status())
        print(t or "not signed in")
        return 0 if t else 1
    if argv[:1] == ["--logout"]:
        logout()
        print("logged out")
        return 0
    if not sys.stdin.isatty():
        print("claudelogin needs a terminal", file=sys.stderr)
        return 1
    curses.wrapper(lambda s: UI(s).loop())
    t = logged_in_text(auth_status())
    print(f"signed in: {t}" if t else "not signed in")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
