"""The test harness: claudelogin started through its real entry point, on a pty, with the
stand-ins in fake_bin/ first on the PATH and a sandbox HOME, so nothing here can touch the real
~/.mantra/ports, the real clipboard or the real claude login.

Every session gets its own folder: FAKE_DIR (what the stand-ins write) and HOME.
"""
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

TESTS = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(TESTS)
APP = os.path.join(APP_DIR, "claudelogin.py")
FAKE_BIN = os.path.join(TESTS, "fake_bin")
ANSI = re.compile(r"\x1b\][^\x07]*\x07|\x1b\[[0-?]*[ -/]*[@-~]")

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", name, ("   " + str(detail)[:400]) if (detail and not ok) else ""))
    return ok


def finish(title):
    print("\n%s: %d passed, %d failed" % (title, len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


class Session:
    def __init__(self, args=(), env=None, packages="org.mozilla.firefox", path_extra=None,
                 drop=(), tty=True, cwd=None, pyflags=()):
        self.dir = tempfile.mkdtemp(prefix="cl-test-")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home)
        bin_dir = os.path.join(self.dir, "bin")
        os.makedirs(bin_dir)
        # the stand-ins, minus the ones a test wants absent
        for name in os.listdir(FAKE_BIN):
            if name not in drop:
                os.symlink(os.path.join(FAKE_BIN, name), os.path.join(bin_dir, name))
        base = ["/usr/local/bin", "/usr/bin", "/bin"]
        e = {"PATH": ":".join([bin_dir] + (path_extra or []) + base), "HOME": self.home,
             "FAKE_DIR": self.dir, "FAKE_PACKAGES": packages, "TERM": "xterm",
             "LANG": "C.UTF-8", "CLAUDELOGIN_CLIP_EVERY": "0.2"}
        e.update(env or {})
        self.env = e
        self.out = ""
        argv = [sys.executable] + list(pyflags) + [APP] + list(args)
        if tty:
            self.pid, self.fd = pty.fork()
            if self.pid == 0:
                if cwd:
                    os.chdir(cwd)
                os.execve(sys.executable, argv, e)
        else:
            self.proc = subprocess.Popen(argv, env=e, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         cwd=cwd)
            self.pid, self.fd = self.proc.pid, self.proc.stdout.fileno()
        self.tty = tty
        self.status = None

    def pump(self, secs=0.1):
        end = time.time() + secs
        while True:
            left = end - time.time()
            if left <= 0:
                return
            try:
                r, _, _ = select.select([self.fd], [], [], left)
            except (OSError, ValueError):
                return
            if r:
                try:
                    d = os.read(self.fd, 65536)
                except OSError:
                    return
                if not d:
                    return
                self.out += d.decode("utf-8", "replace")

    def text(self):
        return ANSI.sub("", self.out).replace("\r", "")

    def wait_for(self, pattern, secs=10):
        end = time.time() + secs
        while time.time() < end:
            m = re.search(pattern, self.text())
            if m:
                return m
            self.pump(0.1)
            if self.exited():
                self.pump(0.2)
                return re.search(pattern, self.text())
        return None

    def send(self, line):
        os.write(self.fd, (line + "\r").encode())

    def exited(self):
        if self.status is not None:
            return True
        try:
            pid, st = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid:
            self.status = os.waitstatus_to_exitcode(st)
            return True
        return False

    def wait_exit(self, secs=10):
        end = time.time() + secs
        while time.time() < end:
            self.pump(0.1)
            if self.exited():
                self.pump(0.2)
                return self.status
        return None

    def file(self, name):
        try:
            with open(os.path.join(self.dir, name)) as f:
                return f.read()
        except OSError:
            return ""

    def set_clip(self, text):
        with open(os.path.join(self.dir, "clip.new"), "w") as f:
            f.write(text)
        os.replace(os.path.join(self.dir, "clip.new"), os.path.join(self.dir, "clip"))

    def wait_file(self, name, secs=10):
        end = time.time() + secs
        while time.time() < end:
            v = self.file(name)
            if v:
                return v
            self.pump(0.1)
        return ""

    def port(self):
        m = re.search(r"http://127\.0\.0\.1:(\d+)", self.text())
        return int(m.group(1)) if m else None

    def code(self):
        return "Ab3dEf6hIj9kLmN0pQ_-xyz#" + self.wait_file("state").strip()

    def close(self):
        if not self.exited():
            try:
                os.kill(self.pid, signal.SIGTERM)
            except OSError:
                pass
            if self.wait_exit(5) is None:
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except OSError:
                    pass
                self.wait_exit(2)
        shutil.rmtree(self.dir, ignore_errors=True)


def http(port, path, body=None, headers=None, host=None):
    """(status, json-or-text). The page's own headers unless a test replaces them."""
    h = {"X-Claudelogin": "1", "Content-Type": "application/json"}
    if headers is not None:
        h = headers
    if host:
        h = dict(h, Host=host)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, headers=h,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode()
            status = r.status
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except OSError as e:
        return 0, str(e)
    if "json" in ctype:
        return status, json.loads(raw)
    return status, raw
