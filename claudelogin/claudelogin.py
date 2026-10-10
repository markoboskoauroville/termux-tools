#!/usr/bin/env python3
"""claudelogin - sign Claude Code in from Termux with one word.

    claudelogin              start `claude auth login`, open its link in Firefox, catch the code
    claudelogin --no-page    the same without the local page (no Flask needed)
    claudelogin --version    the installed version, one whole number

WHAT IT DOES. Signing Claude Code in on the phone was five steps by hand: run
`claude auth login`, long-press the link it prints, copy it, open a browser and
paste it, sign in, copy the code the page shows, find Termux again and paste it.
This does the parts a machine can do:

    1  runs `claude auth login` on its own pty, as a terminal would
    2  reads the sign-in link out of what it prints
    3  opens that link in Firefox (Marko, 10.10.2026: this one is always Firefox),
       and copies it to the clipboard as well, in case
    4  watches the clipboard: when you tap Copy on the page that shows the code,
       the code is recognised and pasted into claude for you
    5  says "signed in", and Enter starts claude in this folder

THE CODE IS RECOGNISED BY ITS SHAPE, NEVER GUESSED. Claude's callback page shows
`<code>#<state>`, and <state> is a random string that is also in the link this
login opened. Only a clipboard text ending in `#` plus THAT state is sent, so
nothing else that passes through the clipboard (the link itself, a password, an
old code from an earlier attempt) is ever typed into claude.

THREE WAYS TO PASTE, whichever is nearest: the clipboard (automatic, needs the
Termux:API app), the page this serves on 127.0.0.1 (o opens it in Firefox), or
the terminal itself: paste the code here and press Enter.

THE PAGE binds 127.0.0.1 only, because a login code is a credential
(termux-app.md §7), and every request must name a loopback Host, a matching
Origin when there is one, and carry X-Claudelogin: 1 on the API, so another tab
cannot post into it. The port is 8500 or the next free one (ports.md), written
to ~/.mantra/ports/claudelogin while it runs.

Nothing is written to disk but that port line. The code is never logged.

Keys, each followed by Enter: q quit · o open the page · l open the login link
again · r restart with a new link · c copy the link again · u check for update.
"""

import codecs
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.realpath(__file__))
HOME = os.path.expanduser("~")
CMD = "claudelogin"
PREFERRED_PORT = 8500                      # ports.md §2: the 8500 block, new 10.10.2026
PORT_SPAN = 16                             # preferred, then the next fifteen, then any
PORTS_DIR = os.path.join(HOME, ".mantra", "ports")
# Firefox first, by Marko's word for this app; the betas after; then the default browser.
# By package, never by activity: activity names change between versions (termux-app.md §5).
FIREFOX = ("org.mozilla.firefox", "org.mozilla.firefox_beta", "org.mozilla.fenix")
# the claude to run: tests point this at a stand-in
CLAUDE = os.environ.get("CLAUDELOGIN_CLAUDE") or "claude"
# Claude Code under proot-distro takes seconds to start on a phone; a minute and a half without
# a link is worth saying out loud, not worth giving up on
URL_TIMEOUT = float(os.environ.get("CLAUDELOGIN_URL_TIMEOUT") or 90)
CLIP_EVERY = float(os.environ.get("CLAUDELOGIN_CLIP_EVERY") or 1.0)
API_TIMEOUT = 15                           # every Termux:API call is bounded (proot-working §3)
PTY_COLS = 1000                            # wide, so no line is ever wrapped through the link
PASTE_PROMPT = "Paste code here if prompted"
VERSION_RE = re.compile(r"^APP_VERSION\s*=\s*(\d+)\s*(?:#.*)?$", re.M)


def read_version(text):
    m = VERSION_RE.search(text or "")
    return int(m.group(1)) if m else 0


def installed_version():
    try:
        with open(os.path.join(HERE, "version.py")) as f:
            return read_version(f.read())
    except OSError:
        return 0


APP_VERSION = installed_version()


# ---------------------------------------------------------------------------
#  The mechanism: pure functions, Test 1 reads these alone
# ---------------------------------------------------------------------------

# OSC (a hyperlink: ESC ] 8 ;; url BEL), CSI (colours, cursor), and the two-byte escapes
ANSI_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
URL_RE = re.compile(r"https://[^\s\x00-\x1f\"'<>]+")


def strip_ansi(text):
    return ANSI_RE.sub("", text or "")


def find_login_url(raw):
    """The sign-in link in what claude printed so far, or None.

    Measured 10.10.2026, Claude Code 2.1.296: the link is printed twice on one
    line, once inside an OSC 8 hyperlink and once as plain text; stripping the
    escapes leaves the plain one. A link that runs to the very end of what has
    arrived may be cut mid-read, so it counts only once something follows it.
    """
    text = strip_ansi(raw)
    for m in URL_RE.finditer(text):
        if m.end() >= len(text):
            return None
        url = m.group(0).rstrip(".,;)")
        if "oauth/authorize" in url:
            return url
    return None


def url_state(url):
    try:
        return (parse_qs(urlsplit(url or "").query).get("state") or [""])[0]
    except ValueError:
        return ""


CODE_HEAD = re.compile(r"[A-Za-z0-9_\-.~]{8,}")


def clean_code(text):
    """A pasted code with the whitespace a paste brings taken out, or "" when it
    cannot be a code. Control characters refuse the whole paste: they would reach
    claude's terminal as keys (a Ctrl-C, an Escape)."""
    if not isinstance(text, str):
        return ""
    s = "".join(text.split())
    if not s or len(s) > 4096:
        return ""
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        return ""
    return s


def looks_like_code(text, state):
    """Is this THE code for the login whose link carried `state`?"""
    s = clean_code(text)
    if not s or not state:
        return False
    head, sep, tail = s.rpartition("#")
    return bool(sep) and tail == state and bool(CODE_HEAD.fullmatch(head))


def outcome_line(raw, sent=""):
    """What claude said after the paste prompt: its last line that is not our own
    code echoed back by the pty."""
    text = strip_ansi(raw).replace("\r", "\n")
    after = text.split(PASTE_PROMPT)[-1]
    lines = [l.strip(" >").strip() for l in after.split("\n")]
    lines = [l for l in lines if l and (not sent or sent not in l)]
    return lines[-1] if lines else ""


def last_line(raw):
    lines = [l.strip() for l in strip_ansi(raw).replace("\r", "\n").split("\n") if l.strip()]
    return lines[-1] if lines else ""


LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")


def request_allowed(host, origin, header, path, port):
    """The three checks from localguard.py (termux-app.md §7). Host must be a
    loopback name at our port: that is what stops DNS rebinding, which binding to
    127.0.0.1 alone does not. Origin, when sent, must be this page. The API wants a
    header no cross-site form can set."""
    allowed = {"%s:%d" % (n, port) for n in LOOPBACK_NAMES}
    if (host or "").lower() not in allowed:
        return False
    if origin and origin.lower() not in {"http://" + a for a in allowed}:
        return False
    if path.startswith("/api/") and header != "1":
        return False
    return True


AM_FAILURE = re.compile(r"\berror\b|exception|not found|does not exist|unable to resolve", re.I)


def opener_failed(returncode, output):
    """`am start` prints its failures and exits zero (termux-app.md §5): read the
    words. "brought to the front" is a warning that it reused a tab, not a failure."""
    if returncode != 0:
        return True
    out = output or ""
    if "brought to the front" in out:
        return False
    return bool(AM_FAILURE.search(out))


# ---------------------------------------------------------------------------
#  The port (ports.md: never fails to open, says which, writes the live line)
# ---------------------------------------------------------------------------

def port_answers(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def bind_port(preferred=PREFERRED_PORT, span=PORT_SPAN, host="127.0.0.1"):
    """A listening socket on `preferred`, the next fifteen, or any. Returns
    (socket, port). Only a port that ANSWERS is taken: one in TIME_WAIT is free,
    which is why SO_REUSEADDR (the U key's restart lands on the same number)."""
    for port in range(preferred, preferred + span):
        if port_answers(port, host):
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            s.listen(64)
            return s, port
        except OSError:
            s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    s.listen(64)
    return s, s.getsockname()[1]


def announce(port):
    try:
        os.makedirs(PORTS_DIR, exist_ok=True)
        path = os.path.join(PORTS_DIR, CMD)
        with open(path + ".new", "w") as f:
            f.write("%d\n" % port)
        os.chmod(path + ".new", 0o600)
        os.replace(path + ".new", path)
    except OSError:
        pass


def forget(port):
    """Remove the line, but only if it is still ours: a second copy started since owns it."""
    path = os.path.join(PORTS_DIR, CMD)
    try:
        with open(path) as f:
            if f.read().strip() == str(port):
                os.remove(path)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
#  Android: bounded calls to Termux:API and am
# ---------------------------------------------------------------------------

def run_bounded(cmd, secs, stdin_text=None):
    """Run with a deadline, through timeout(1) so the whole group dies with it
    (a hung Termux:API call is a phantom process Android counts). None when it
    could not run or ran out of time."""
    if not shutil.which(cmd[0]):
        return None
    full = (["timeout", "-k", "5", str(secs)] + cmd) if shutil.which("timeout") else cmd
    try:
        r = subprocess.run(full, input=stdin_text, capture_output=True, text=True,
                           timeout=secs + 10)
    except (OSError, subprocess.SubprocessError):
        return None
    return None if r.returncode in (124, 137) else r


_HAS_PKG = {}


def has_package(pkg):
    if pkg not in _HAS_PKG:
        r = run_bounded(["pm", "list", "packages", pkg], 10)
        _HAS_PKG[pkg] = bool(r) and ("package:" + pkg) in r.stdout.split()
    return _HAS_PKG[pkg]


def firefox_package():
    for pkg in FIREFOX:
        if has_package(pkg):
            return pkg
    return ""


def open_url(url):
    """Firefox; without Firefox, the default browser; off the phone, xdg-open or
    open. Returns the name of what opened it, or "" when nothing could."""
    chain = []
    if shutil.which("termux-open-url"):
        pkg = firefox_package()
        if pkg:
            chain.append((["termux-open-url", url, pkg], "Firefox"))
        chain.append((["termux-open-url", url], "the default browser"))
    for other in ("xdg-open", "open"):
        if shutil.which(other):
            chain.append(([other, url], other))
    for cmd, name in chain:
        r = run_bounded(cmd, 30)
        if r is not None and not opener_failed(r.returncode, r.stdout + r.stderr):
            return name
    return ""


def clipboard_get():
    r = run_bounded(["termux-clipboard-get"], API_TIMEOUT)
    return r.stdout if (r is not None and r.returncode == 0) else None


def clipboard_set(text):
    r = run_bounded(["termux-clipboard-set"], API_TIMEOUT, stdin_text=text)
    return r is not None and r.returncode == 0


# ---------------------------------------------------------------------------
#  The login: `claude auth login` on a pty
# ---------------------------------------------------------------------------

class Login:
    """One `claude auth login` at a time. States:

        starting   claude is running, no link yet
        waiting    the link is known; claude waits at its paste prompt
        checking   a code was typed into claude; it is asking Anthropic
        signed_in  claude exited 0
        failed     claude exited otherwise, or could not be started
    """

    def __init__(self, argv, on_event):
        self.argv = argv
        self.on_event = on_event
        self.lock = threading.RLock()
        self.gen = 0
        self._reset()

    def _reset(self):
        self.state = "idle"
        self.url = ""
        self.oauth_state = ""
        self.message = ""
        self.source = ""
        self.sent = ""
        self.pid = None
        self.fd = None
        self.buf = ""

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "url": self.url, "message": self.message,
                    "source": self.source}

    def emit(self, gen, kind, data=""):
        if gen == self.gen:
            self.on_event(kind, data, gen)

    def start(self):
        with self.lock:
            self.stop()
            self.gen += 1
            gen = self.gen
            self._reset()
            self.state = "starting"
            try:
                pid, fd = pty.fork()
            except OSError as e:
                self.state, self.message = "failed", "could not open a terminal for claude: %s" % e
                self.emit(gen, "failed", self.message)
                return
            if pid == 0:
                try:
                    env = dict(os.environ)
                    env["COLUMNS"] = str(PTY_COLS)
                    env.setdefault("TERM", "xterm-256color")
                    # we open the link ourselves; claude's own attempt (inside the proot it
                    # finds no browser anyway) must not open a second tab
                    env["BROWSER"] = "true"
                    os.execvpe(self.argv[0], self.argv, env)
                except Exception as e:
                    os.write(2, ("claudelogin: cannot run %s: %s\n" % (self.argv[0], e)).encode())
                os._exit(127)
            try:
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, PTY_COLS, 0, 0))
            except OSError:
                pass
            self.pid, self.fd = pid, fd
        threading.Thread(target=self._read, args=(gen, pid, fd), daemon=True).start()

    def _read(self, gen, pid, fd):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        started = time.time()
        said_slow = False
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                with self.lock:
                    if gen != self.gen:
                        break
                    self.buf = (self.buf + dec.decode(data))[-200000:]
                    if not self.url:
                        url = find_login_url(self.buf)
                        if url:
                            self.url, self.oauth_state = url, url_state(url)
                            self.state = "waiting"
                            self.emit(gen, "url", url)
            if not said_slow and self.state == "starting" and time.time() - started > URL_TIMEOUT:
                said_slow = True
                self.emit(gen, "slow", last_line(self.buf))
        try:
            _, status = os.waitpid(pid, 0)
            code = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            code = -1
        try:
            os.close(fd)
        except OSError:
            pass
        with self.lock:
            if gen != self.gen:
                return
            self.pid = self.fd = None
            if code == 0:
                self.state = "signed_in"
                self.message = outcome_line(self.buf, self.sent) or "signed in"
            else:
                self.state = "failed"
                said = outcome_line(self.buf, self.sent) if self.url else last_line(self.buf)
                self.message = said or "claude stopped (exit %s) before saying why" % code
            self.emit(gen, self.state, self.message)

    def submit(self, code, source):
        """Type the code into claude. Returns (True, "") or (False, why not)."""
        with self.lock:
            if self.state != "waiting" or self.fd is None:
                why = {"checking": "a code is already being checked",
                       "signed_in": "already signed in",
                       "starting": "claude has not shown a link yet"}.get(
                           self.state, "no login is waiting for a code")
                return False, why
            try:
                os.write(self.fd, (code + "\r").encode())
            except OSError as e:
                return False, "could not type into claude: %s" % e
            self.state, self.source, self.sent = "checking", source, code
            self.emit(self.gen, "sent", source)
            return True, ""

    def stop(self):
        with self.lock:
            pid = self.pid
            self.gen += 1
            self.pid = None
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                return

            def reap():
                time.sleep(2)
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
            threading.Thread(target=reap, daemon=True).start()


# ---------------------------------------------------------------------------
#  The page
# ---------------------------------------------------------------------------

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#0B0D10"/>
<circle cx="25" cy="32" r="11" fill="none" stroke="#F59E0B" stroke-width="6"/>
<path d="M36 32h20M48 32v9M55 32v6" stroke="#F59E0B" stroke-width="6" stroke-linecap="round"/>
</svg>"""


def make_app(core):
    from flask import Flask, Response, abort, jsonify, request

    app = Flask(__name__)

    @app.before_request
    def guard():
        if not request_allowed(request.headers.get("Host", ""), request.headers.get("Origin"),
                               request.headers.get("X-Claudelogin"), request.path, core.port):
            abort(403)

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.get("/")
    def page():
        with open(os.path.join(HERE, "claudelogin.html"), encoding="utf-8") as f:
            html = f.read().replace("{{VERSION}}", str(APP_VERSION))
        return Response(html, mimetype="text/html")

    @app.get("/favicon.svg")
    @app.get("/favicon.ico")
    def favicon():
        return Response(FAVICON, mimetype="image/svg+xml")

    @app.get("/api/state")
    def state():
        return jsonify(core.state())

    @app.post("/api/code")
    def code():
        body = request.get_json(silent=True) or {}
        ok, why = core.paste(body.get("code"), "the page")
        return jsonify({"ok": ok, "error": why, **core.state()})

    @app.post("/api/clipboard")
    def from_clipboard():
        ok, why = core.paste_clipboard()
        return jsonify({"ok": ok, "error": why, **core.state()})

    @app.post("/api/open")
    def reopen():
        threading.Thread(target=core.open_login, daemon=True).start()
        return jsonify({"ok": True, **core.state()})

    @app.post("/api/restart")
    def restart():
        core.login.start()
        return jsonify({"ok": True, **core.state()})

    return app


def serve(app, sock):
    """Waitress (thread pooled, pure Python, works in Termux), else Flask's own
    server with a word about it: a broken install degrades, it does not die."""
    try:
        import logging
        import waitress
        logging.getLogger("waitress").setLevel(logging.ERROR)
        server = waitress.create_server(app, sockets=[sock], threads=4)
        threading.Thread(target=server.run, daemon=True).start()
        return "waitress"
    except ImportError:
        from werkzeug.serving import make_server
        port = sock.getsockname()[1]
        sock.close()
        server = make_server("127.0.0.1", port, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return "flask's own server (pip install waitress)"


# ---------------------------------------------------------------------------
#  The console
# ---------------------------------------------------------------------------

if sys.stdout.isatty():
    AM, OK, BAD, DIM, OFF = "\033[38;5;214m", "\033[1;32m", "\033[1;31m", "\033[0;90m", "\033[0m"
else:
    AM = OK = BAD = DIM = OFF = ""


class Core:
    def __init__(self, page=True):
        self.page = page
        self.port = 0
        self.browser = ""
        self.clip_ok = bool(shutil.which("termux-clipboard-get"))
        self.out_lock = threading.Lock()
        self.done = threading.Event()
        self.login = Login([CLAUDE, "auth", "login"], self.on_event)

    def say(self, text):
        with self.out_lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def state(self):
        s = self.login.snapshot()
        s.update(version=APP_VERSION, browser=self.browser, clipboard=self.clip_ok,
                 cwd=os.getcwd())
        return s

    # -- events from the login ------------------------------------------------

    def on_event(self, kind, data, gen):
        if kind == "url":
            self.say("  %s2%s  the sign-in link:" % (AM, OFF))
            self.say("     %s%s%s" % (DIM, data, OFF))
            threading.Thread(target=self.after_url, args=(gen,), daemon=True).start()
        elif kind == "slow":
            self.say("  %sclaude has shown no link in %d s.%s It said: %s" % (
                AM, URL_TIMEOUT, OFF, data or "nothing yet"))
            self.say("     still waiting.  r starts again,  q quits")
        elif kind == "sent":
            self.say("  %s4%s  code from %s typed into claude, checking it..." % (AM, OFF, data))
        elif kind == "signed_in":
            self.say("  %sdone%s  signed in.  %s%s%s" % (OK, OFF, DIM, data, OFF))
            if sys.stdin.isatty():
                self.say("\n  Enter starts %sclaude%s in %s,  q quits" % (AM, OFF, os.getcwd()))
            else:
                self.done.set()
        elif kind == "failed":
            self.say("  %snot signed in%s  %s" % (BAD, OFF, data))
            self.say("     r tries again with a new link,  q quits")
            if not sys.stdin.isatty():
                self.done.set()

    def after_url(self, gen):
        """Open, copy, then watch the clipboard: in that order, opening first,
        because the browser is what he is waiting for."""
        url = self.login.url
        self.browser = open_url(url)
        if self.browser:
            self.say("     opened in %s" % self.browser)
        else:
            self.say("  %sno browser could be opened%s: long-press the link above" % (BAD, OFF))
        if clipboard_set(url):
            self.say("     and copied, in case")
        self.say("  %s3%s  sign in, then tap %sCopy Code%s on the page that shows it." % (
            AM, OFF, AM, OFF))
        self.watch_clipboard(gen)

    def watch_clipboard(self, gen):
        if not self.clip_ok:
            self.say("     %sno termux-clipboard-get%s (pkg install termux-api, and the Termux:API"
                     " app): paste the code here and press Enter" % (DIM, OFF))
            return
        self.say("     watching the clipboard for it; or paste it here and press Enter")
        seen, misses, warned = None, 0, False
        while self.login.gen == gen and self.login.state == "waiting":
            clip = clipboard_get()
            if clip is None:
                misses += 1
                if misses >= 3 and not warned:
                    warned = True
                    self.say("     %sthe clipboard is not answering%s (is the Termux:API app"
                             " installed?): paste the code here and press Enter" % (DIM, OFF))
            else:
                misses = 0
                if clip != seen:
                    seen = clip
                    if looks_like_code(clip, self.login.oauth_state) and self.login.gen == gen:
                        self.login.submit(clean_code(clip), "the clipboard")
            time.sleep(CLIP_EVERY)

    # -- actions --------------------------------------------------------------

    def paste(self, text, source):
        code = clean_code(text)
        if len(code) < 8:
            return False, "that is not a code: it is the long text the sign-in page shows"
        return self.login.submit(code, source)

    def paste_clipboard(self):
        clip = clipboard_get()
        if clip is None:
            return False, "the clipboard could not be read (Termux:API)"
        return self.paste(clip, "the clipboard")

    def open_login(self):
        url = self.login.url
        if not url:
            return
        name = open_url(url)
        self.say("     the link again, in %s" % (name or "nothing: no browser could be opened"))

    def open_page(self):
        if not self.port:
            self.say("     the page is off (--no-page, or no Flask)")
            return
        url = "http://127.0.0.1:%d/" % self.port
        self.say("     %s in %s" % (url, open_url(url) or "nothing: no browser could be opened"))

    # -- the run --------------------------------------------------------------

    def run(self):
        self.say("\n  %sclaudelogin%s v%d   Claude Code sign-in\n" % (AM, OFF, APP_VERSION))
        if self.page:
            self.start_page()
        self.say("  %s1%s  starting claude auth login..." % (AM, OFF))
        self.login.start()
        try:
            if sys.stdin.isatty():
                return self.console()
            self.done.wait()
            return 0 if self.login.state == "signed_in" else 1
        finally:
            self.login.stop()
            if self.port:
                forget(self.port)

    def start_page(self):
        try:
            app = make_app(self)
        except ImportError:
            self.say("  %spage off%s  no Flask: pip install flask waitress" % (DIM, OFF))
            return
        sock, self.port = bind_port()
        how = serve(app, sock)
        announce(self.port)
        moved = "" if self.port == PREFERRED_PORT else "  (%d was taken)" % PREFERRED_PORT
        self.say("  page   http://127.0.0.1:%d%s   127.0.0.1 only: a login code is a key" % (
            self.port, moved))
        if how != "waitress":
            self.say("         served by %s" % how)

    def console(self):
        self.say("  %sq quit · o open page · l login link again · r restart · c copy link ·"
                 " u update%s" % (DIM, OFF))
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                return 0 if self.login.state == "signed_in" else 1
            word = line.strip()
            if word == "" and self.login.state == "signed_in":
                self.start_claude()
            elif word == "":
                continue
            elif word.lower() == "q":
                return 0 if self.login.state == "signed_in" else 1
            elif word.lower() == "o":
                self.open_page()
            elif word.lower() == "l":
                threading.Thread(target=self.open_login, daemon=True).start()
            elif word.lower() == "r":
                self.say("  %s1%s  starting again, with a new link..." % (AM, OFF))
                self.login.start()
            elif word.lower() == "c":
                ok = bool(self.login.url) and clipboard_set(self.login.url)
                self.say("     copied" if ok else "     nothing copied")
            elif word.lower() == "u":
                self.update()
            else:
                ok, why = self.paste(word, "the terminal")
                if not ok:
                    self.say("     %s%s%s" % (BAD, why, OFF))

    def start_claude(self):
        """Hand the terminal to claude: this process becomes it."""
        if self.port:
            forget(self.port)
        self.say("")
        try:
            os.execvp(CLAUDE, [CLAUDE])
        except OSError as e:
            self.say("  %scould not start claude%s: %s" % (BAD, OFF, e))

    def update(self):
        """The U key (termux-app.md §10): fetch, installed -> available, y, pull."""
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

        def git(*args, timeout=25):
            return subprocess.run(["git", "-C", HERE] + list(args), capture_output=True,
                                  text=True, timeout=timeout, env=env)
        try:
            root = git("rev-parse", "--show-toplevel").stdout.strip()
            if not root:
                self.say("     not a git checkout: reinstall with the line in README.md")
                return
            rel = os.path.relpath(os.path.join(HERE, "version.py"), root)
            if git("fetch", "-q", "origin", "main").returncode != 0:
                self.say("     could not reach GitHub (a private repository needs gh auth login);"
                         " nothing changed")
                return
            available = read_version(git("show", "origin/main:" + rel).stdout)
        except (OSError, subprocess.SubprocessError) as e:
            self.say("     update check failed: %s" % e)
            return
        self.say("     installed v%d -> available v%d" % (APP_VERSION, available))
        if available <= APP_VERSION:
            self.say("     up to date")
            return
        self.say("     y and Enter to update")
        try:
            if input().strip().lower() != "y":
                self.say("     not updated")
                return
        except EOFError:
            return
        if git("pull", "-q", "--ff-only", "origin", "main", timeout=120).returncode != 0:
            self.say("     git pull failed; nothing changed")
            return
        subprocess.run(["bash", os.path.join(HERE, "install.sh"), "link"])
        self.login.stop()
        if self.port:
            forget(self.port)
        self.say("     restarting...")
        os.execv(sys.executable, [sys.executable, os.path.realpath(__file__)] + sys.argv[1:])


def main(argv):
    if "--version" in argv:
        print("claudelogin v%d" % APP_VERSION)
        return 0
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    if not shutil.which(CLAUDE):
        print("  claudelogin: no `%s` on the PATH. Install Claude Code first"
              " (modules/claude-code-termux.md in MANTRA_MANIFEST)." % CLAUDE)
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    return Core(page="--no-page" not in argv).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
