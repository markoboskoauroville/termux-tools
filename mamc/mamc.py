#!/usr/bin/env python3
"""mamc - a Midnight Commander for the apps on this phone.

    mamc                  open the browser
    mamc --list           print the inventory and exit
    mamc --restore        restore something from the trash

WHAT IT IS FOR. The apps on this phone live in three places at once: a folder
in $HOME, a launcher script in one of two bin directories, and -- for the few
that were registered -- a descriptor in ~/.ma/apps. Nothing joins them up, so
removing an app means hunting its pieces by hand and hoping none are missed.

This joins them up. One list, every app, whatever it was installed by. Run one
to see whether you still want it, then remove it knowing what goes with it.

TWO RULES, BOTH DELIBERATE.

One app at a time. The launcher owns the process it starts, so there is never a
second server holding a port you are trying to use.

Q closes it. Every app, the same key, no Enter. The app does not need to know:
this program owns the terminal and the child process group, so Q is answered
here and the child is stopped from outside. An app that never learned to quit
politely still quits.

MOST-RUN FIRST. Every start is counted, and the list is ordered by that count,
so the apps you actually live in are at the top. ma's own counts are read in at
startup, so an app you have run fifteen times does not begin at zero.

IMPORTANT APPS. The ones you rely on are flagged (! in the list, their own
colour) and asking for one to be removed is answered twice: the ordinary y/N,
then a second screen that wants the word `remove` typed in full. Press ! to
flag or unflag whatever is selected.

NOTHING IS DELETED. Uninstall MOVES every piece into ~/.mamc/trash with a
manifest saying where each came from, so `mamc --restore` can put it back.
Emptying the trash is a separate, explicit act.
"""

import curses
import errno
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque

HOME = os.path.expanduser("~")
PREFIX = "/data/data/com.termux/files/usr"
BIN_DIRS = [os.path.join(PREFIX, "bin"),
            os.path.join(HOME, ".local", "bin"),
            os.path.join(HOME, "bin")]
MA_APPS = os.path.join(HOME, ".ma", "apps")
MA_STATS = os.path.join(HOME, ".ma", "stats")
MAMC_DIR = os.path.join(HOME, ".mamc")
TRASH = os.path.join(MAMC_DIR, "trash")
LOG_DIR = os.path.join(MAMC_DIR, "logs")
IMPORTANT_FILE = os.path.join(MAMC_DIR, "important.json")
RUNS_FILE = os.path.join(MAMC_DIR, "runs.json")

# Directories in $HOME that are configuration, caches or toolchains. An app
# never lives here, and walking .cargo looking for a Flask import is a way to
# make the browser take ten seconds to open.
SKIP_DIRS = {
    ".termux", ".local", ".cache", ".config", ".gnupg", ".ssh", ".npm",
    ".cargo", ".rustup", ".git", ".claude", ".vscode-server", "storage",
    ".pki", ".java", ".gradle", ".android", ".mamc", ".ma", "go", ".bun",
    ".nvm", ".deno", "tmp", ".tmp", ".pyenv", ".venv", "venv",
}
PROTECTED = {
    "claude", "claude-termux-update", "mamc", "ma", "ecosystem", "python",
    "talk",
    "python3", "pip", "node", "npm", "git", "bash", "login", "prove", "h2ph",
    "scan-build", "web", "termux-notification", "toml-update",
}

SKIP_WALK = {"__pycache__", "node_modules", ".git", "venv", ".venv",
             "site-packages", "dist-packages", "build", ".pytest_cache"}

SERVER_RE = re.compile(
    rb"(from|import)\s+flask|http\.server|BaseHTTPRequestHandler"
    rb"|socketserver|ThreadingHTTPServer|uvicorn|fastapi")
FLASK_RE = re.compile(rb"(from|import)\s+flask")
# PORT = 8081   /   PORT = int(os.environ.get("X_PORT", "8081"))
PORT_RE = re.compile(
    rb"""(?:PORT|DEFAULT_PORT|WANTED_PORT|PORT_START)\s*=\s*"""
    rb"""(?:int\(\s*os\.environ\.get\(\s*["'][^"']+["']\s*,\s*["'])?(\d{2,5})""")
# Plenty of small apps never name a constant: app.run(host=..., port=5000).
RUN_PORT_RE = re.compile(rb"""\.run\([^)]*?port\s*=\s*(\d{2,5})""", re.S)
NOT_APP_EXT = {".log", ".txt", ".json", ".md", ".html", ".cfg", ".conf",
               ".pid", ".lock", ".bak", ".tmp", ".sh", ".properties"}
IMPORTANT = {}
APPNAME_RE = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._+-]{1,40}$")
URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|0\.0\.0\.0|localhost)[:/][^\s\"']*")


# ---------------------------------------------------------------------------
#  Important apps
#
#  Some of these apps are used every day and some were experiments. The list
#  cannot tell them apart, and an uninstall is one keypress -- so the ones that
#  matter are flagged, drawn in their own colour, and made to ask twice.
#
#  The flag is stored against the app's PATH rather than its name, because the
#  name comes from an ma descriptor and can be renamed; the folder cannot be
#  renamed without the app breaking anyway.
# ---------------------------------------------------------------------------

def load_important():
    try:
        with open(IMPORTANT_FILE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_important(d):
    os.makedirs(MAMC_DIR, exist_ok=True)
    tmp = IMPORTANT_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=2, sort_keys=True)
    os.replace(tmp, IMPORTANT_FILE)


# ---------------------------------------------------------------------------
#  Run counts
#
#  The list is sorted by how often you actually start each app, so the ones you
#  live in rise to the top and the experiments sink. ma already counts runs for
#  the apps it registered (~/.ma/stats/<id>.runs), so those counts are read in
#  rather than started from zero -- MA Reader has been run 15 times and the
#  list should say so on the first open, not after the sixteenth.
# ---------------------------------------------------------------------------

def load_runs():
    try:
        with open(RUNS_FILE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_runs(d):
    os.makedirs(MAMC_DIR, exist_ok=True)
    tmp = RUNS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=2, sort_keys=True)
    os.replace(tmp, RUNS_FILE)


def note_run(app):
    runs = load_runs()
    rec = runs.get(app.ident) or {"count": 0, "last": 0}
    rec["count"] = int(rec.get("count", 0)) + 1
    rec["last"] = time.time()
    rec["name"] = app.name
    runs[app.ident] = rec
    save_runs(runs)
    app.runs = rec["count"]
    app.last_run = rec["last"]


def ma_stat_runs(desc):
    """What ma has counted for this app, if anything."""
    if not desc:
        return 0, 0
    base = os.path.join(MA_STATS, desc["_id"])
    def read(suffix):
        try:
            return int(open(base + suffix).read().strip() or 0)
        except Exception:
            return 0
    return read(".runs"), read(".last")


def ago(ts):
    if not ts:
        return "never"
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    if d < 86400 * 60:
        return f"{int(d // 86400)}d ago"
    return time.strftime("%d %b %Y", time.localtime(ts))


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


def dir_size(path):
    total = 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_WALK]
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


TERMUX_HOME = "/data/data/com.termux/files/home"


def unspell(path):
    """One home, two spellings. Say it the way this side says it.

    os.path.realpath resolves a symlink to the Termux spelling of home, and a
    `startswith(HOME)` test against /root then fails on a path that IS under
    home. That is how the `transcribe` launcher lost its app: the symlink
    pointed into MAHA_TRANSCRIBE_TERMUX_TERMINAL and the candidate was thrown
    away as being outside HOME.
    """
    if path.startswith(TERMUX_HOME):
        return HOME + path[len(TERMUX_HOME):]
    return path


def expand(p):
    p = p.replace("$PREFIX", PREFIX)
    p = p.replace("/data/data/com.termux/files/home", HOME)
    p = p.replace("$HOME", HOME)
    if p.startswith("~"):
        p = HOME + p[1:]
    return p


def port_is_up(port):
    if not port:
        return False
    s = socket.socket()
    s.settimeout(0.15)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        s.close()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


# ---------------------------------------------------------------------------
#  Discovery
#
#  Three sources, merged on the app's directory:
#
#    1. ~/.ma/apps/*.app     the registered ones. Authoritative: the descriptor
#                            names the command, the port, and every path the
#                            app owns, which is exactly what a clean uninstall
#                            needs and exactly what the other two sources can
#                            only guess at.
#    2. $HOME/<dir>          a folder holding a .py that imports a web server.
#                            This is what actually finds things -- most apps
#                            here were never registered.
#    3. the bin directories  launcher scripts, read for which directory they
#                            run. A launcher whose target is gone is an ORPHAN
#                            and is listed too: it is the one kind of rubbish
#                            you cannot see from the app side.
# ---------------------------------------------------------------------------

def read_ma_descriptors():
    out = {}
    if not os.path.isdir(MA_APPS):
        return out
    for fn in sorted(os.listdir(MA_APPS)):
        if not fn.endswith(".app"):
            continue
        path = os.path.join(MA_APPS, fn)
        d = {}
        try:
            for line in open(path, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
        except OSError:
            continue
        d["_file"] = path
        d["_id"] = fn[:-4]
        out[fn] = d
    return out


def scan_launchers():
    """command -> {path, bindir, candidates}

    A launcher names its app in whatever style its author felt like:

        exec python3 "$HOME/.x/app.py"      the path, inline
        APPDIR="$HOME/.x"; exec python ...  through a variable
        APP="/data/data/.../home/.x/app.py" spelled out in full
        (a symlink into the app's own repo)

    Rather than teach one regex all four, collect EVERY path under $HOME the
    script mentions and let the caller pick: the right one is the one that
    turns out to be a real app. A launcher that mentions no home path at all
    is a system binary and is dropped -- unless ma has registered it, which
    is decided later.
    """
    found = {}
    ref = re.compile(
        r"""(?:\$HOME|~|/data/data/com\.termux/files/home)/[A-Za-z0-9._+-]"""
        r"""[^"'\s:;)}]*""")
    for bd in BIN_DIRS:
        if not os.path.isdir(bd):
            continue
        for name in sorted(os.listdir(bd)):
            p = os.path.join(bd, name)
            if not (os.path.isfile(p) and os.access(p, os.X_OK)):
                continue
            if name in PROTECTED:
                continue
            cands = []
            real = unspell(os.path.realpath(p))
            if real != p and real.startswith(HOME):
                cands.append(os.path.dirname(real))
            try:
                with open(p, "rb") as fh:
                    head = fh.read(2)
                    if head == b"#!":
                        fh.seek(0)
                        txt = fh.read(65536).decode("utf-8", "replace")
                    else:
                        txt = ""
            except OSError:
                txt = ""
            for m in ref.finditer(txt):
                t = unspell(expand(m.group(0)).rstrip("/\"'"))
                if t.endswith(".py") or t.endswith(".sh"):
                    t = os.path.dirname(t)
                rel = os.path.relpath(t, HOME).split(os.sep)[0]
                if rel in SKIP_DIRS or rel.startswith("..") or t == HOME:
                    continue
                if not APPNAME_RE.match(rel):
                    continue
                if os.path.splitext(rel)[1] in NOT_APP_EXT:
                    continue
                if t not in cands:
                    cands.append(t)
            if cands or txt:
                found[name] = {"path": p, "bindir": bd, "candidates": cands,
                               "text": txt}
    return found


def launcher_port(text):
    """A launcher usually states the port plainly:  PORT="${X_PORT:-8081}"."""
    m = re.search(r'PORT[A-Z_]*="?\$\{[A-Z_]+:-(\d{2,5})\}', text or "")
    if m:
        return int(m.group(1))
    m = re.search(r'PORT[A-Z_]*="?(\d{2,5})"?\s*$', text or "", re.M)
    return int(m.group(1)) if m else None


def scan_home():
    """Directories (and loose .py files) in $HOME that hold a web server."""
    out = {}
    try:
        entries = sorted(os.listdir(HOME))
    except OSError:
        return out
    for name in entries:
        path = os.path.join(HOME, name)
        if name in SKIP_DIRS:
            continue
        if os.path.isfile(path) and name.endswith(".py"):
            try:
                blob = open(path, "rb").read(400000)
            except OSError:
                continue
            if SERVER_RE.search(blob):
                out[path] = {"dir": path, "entry": path, "loose": True,
                             "flask": bool(FLASK_RE.search(blob))}
            continue
        if not os.path.isdir(path):
            continue
        hits = []
        flask = False
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_WALK]
            if root.count(os.sep) - path.count(os.sep) > 2:
                dirs[:] = []
            for f in files:
                if not f.endswith(".py"):
                    continue
                fp = os.path.join(root, f)
                try:
                    blob = open(fp, "rb").read(400000)
                except OSError:
                    continue
                if SERVER_RE.search(blob):
                    hits.append(fp)
                    if FLASK_RE.search(blob):
                        flask = True
            if len(hits) > 6:
                break
        if hits:
            # The entry point is the biggest server file at the shallowest
            # depth -- app.py beside a 10k-line server.py is a helper, not
            # the thing you run.
            hits.sort(key=lambda p: (p.count(os.sep), -os.path.getsize(p)))
            out[path] = {"dir": path, "entry": hits[0], "loose": False,
                         "flask": flask}
    return out


def detect_port(entry, descriptor):
    if descriptor and descriptor.get("port"):
        try:
            return int(descriptor["port"])
        except ValueError:
            pass
    try:
        blob = open(entry, "rb").read(400000)
    except OSError:
        return None
    for rx in (PORT_RE, RUN_PORT_RE):
        m = rx.search(blob)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


class App:
    def __init__(self, name, path):
        self.name = name
        self.path = path            # app dir, or the loose .py file
        self.entry = None
        self.cmds = []              # [(command name, bin dir)]
        self.desc = None            # ma descriptor dict
        self.port = None
        self.flask = False
        self.loose = False
        self.orphan = False         # a launcher whose target is gone
        self.bin_only = False       # lives entirely in a bin script
        self.launcher_text = None   # its launcher, for port sniffing
        self.runs = 0               # how often it has been started
        self.last_run = 0
        self._size = None

    @property
    def size(self):
        if self._size is None:
            self._size = dir_size(self.path) if self.path else 0
        return self._size

    @property
    def ident(self):
        """Stable key for the flag file: the folder, or failing that the
        command. Both survive an ma rename; the display name does not."""
        if self.path:
            return self.path
        if self.cmds:
            return "cmd:" + self.cmds[0][0]
        return "name:" + self.name

    @property
    def important(self):
        return self.ident in IMPORTANT

    @property
    def registered(self):
        return self.desc is not None

    @property
    def cmd(self):
        if not self.cmds:
            return None
        names = [c for c, _ in self.cmds]
        if self.desc and self.desc.get("cmd") in names:
            return self.desc["cmd"]
        plain = [n for n in names if not n.endswith(("-update", "-cmd"))] or names
        stem = (self.desc.get("cmd") if self.desc else None) or \
            (os.path.basename(self.path).lstrip(".").replace(".", "")
             if self.path else "")
        kin = [n for n in plain if stem and stem in n.replace(".", "")]
        return min(kin or plain, key=len)

    @property
    def kind(self):
        if self.orphan:
            return "orphan"
        if self.bin_only:
            return "bin"
        return "flask" if self.flask else "server"

    def owns(self, path):
        """May this app claim to own this path?

        A descriptor's paths= and cache= are lines in a text file, and an app
        that claims paths=/etc is asking for /etc to be moved into the trash
        the next time it is uninstalled. Nobody has to be malicious for this:
        one wrong line, or an unexpanded variable resolving to "/", is enough.

        So a claim is only honoured inside the two places an app can actually
        live -- somewhere under $HOME, or a launcher in one of the bin
        directories -- and never on those directories THEMSELVES.
        """
        if not path:
            return False
        real = os.path.realpath(path)
        if real in ("/", HOME, PREFIX) or real in BIN_DIRS:
            return False
        roots = [HOME] + BIN_DIRS
        return any(real == r or real.startswith(r + os.sep) for r in roots)

    def targets(self):
        """Every path this app owns, for uninstall. Deduped, existing only."""
        out = []
        if self.path:
            out.append(self.path)
        for name, bd in self.cmds:
            out.append(os.path.join(bd, name))
        if self.desc:
            out.append(self.desc["_file"])
            for key in ("paths", "cache"):
                for chunk in self.desc.get(key, "").split():
                    out.append(expand(chunk))
            for suffix in (".runs", ".last"):
                out.append(os.path.join(MA_STATS, self.desc["_id"] + suffix))
        if self.cmd:
            out.append(os.path.join(HOME, ".cache", self.cmd))
        seen, keep = set(), []
        for p in out:
            if p and p not in seen and os.path.exists(p) and self.owns(p):
                seen.add(p)
                keep.append(p)
        return keep


def discover():
    global IMPORTANT
    IMPORTANT = load_important()
    descriptors = read_ma_descriptors()
    launchers = scan_launchers()
    homes = scan_home()
    apps = {}

    for path, info in homes.items():
        a = App(os.path.basename(path).lstrip("."), path)
        a.entry = info["entry"]
        a.flask = info["flask"]
        a.loose = info["loose"]
        apps[path] = a

    # Join a launcher to its app. The right candidate is the one that is a
    # real app; only if NONE of them exists is the launcher an orphan.
    for cname, info in launchers.items():
        cands = info["candidates"]
        target = next((c for c in cands if c in apps), None)
        if target is None:
            target = next((c for c in cands if os.path.isdir(c)), None)
            if target is not None and target not in apps:
                a = App(os.path.basename(target).lstrip("."), target)
                apps[target] = a
        if target is not None:
            apps[target].cmds.append((cname, info["bindir"]))
            if apps[target].launcher_text is None:
                apps[target].launcher_text = info["text"]
        elif cands:
            key = "orphan:" + cands[0]
            if key not in apps:
                a = App(os.path.basename(cands[0]).lstrip("."), None)
                a.orphan = True
                a.missing_target = cands[0]
                apps[key] = a
            apps[key].cmds.append((cname, info["bindir"]))

    for d in descriptors.values():
        cmd = d.get("cmd")
        # A protected command has no launcher entry by design, so its
        # descriptor would otherwise look like a registration pointing at
        # nothing. mamc's own descriptor is the obvious case: listing itself
        # as an orphan invites you to delete the thing you are holding.
        if cmd in PROTECTED:
            continue
        target = None
        for path, a in apps.items():
            if cmd and any(c == cmd for c, _ in a.cmds):
                target = path
                break
        if target is None and cmd:
            guess = os.path.join(HOME, "." + cmd)
            if guess in apps:
                target = guess
        if target is not None:
            apps[target].desc = d
            if d.get("name"):
                apps[target].name = d["name"]
            continue
        # No app directory, but the command is installed: the whole app lives
        # in the bin script (MA Player is one -- it runs an engine in bin).
        # That is a working app, not an orphan.
        if cmd and cmd in launchers:
            key = "bin:" + cmd
            a = App(d.get("name") or cmd, None)
            a.desc = d
            a.cmds.append((cmd, launchers[cmd]["bindir"]))
            a.launcher_text = launchers[cmd]["text"]
            a.bin_only = True
            apps[key] = a
        else:
            key = "ma:" + d["_id"]
            a = App(d.get("name") or d["_id"], None)
            a.desc = d
            a.orphan = True
            a.missing_target = f"registered, but {cmd or '?'} is not installed"
            apps[key] = a

    for a in apps.values():
        if a.desc and a.desc.get("port"):
            try:
                a.port = int(a.desc["port"])
            except ValueError:
                pass
        if a.port is None:
            a.port = launcher_port(a.launcher_text)
        if a.port is None and a.entry:
            a.port = detect_port(a.entry, None)

    runs = load_runs()
    for a in apps.values():
        mine = runs.get(a.ident) or {}
        theirs, their_last = ma_stat_runs(a.desc)
        a.runs = max(int(mine.get("count", 0)), theirs)
        a.last_run = max(float(mine.get("last", 0)), float(their_last))

    # Most-run first. Orphans always last -- they cannot be run at all, and
    # they are here to be cleared, not chosen.
    out = list(apps.values())
    out.sort(key=lambda a: (a.orphan, -a.runs, a.name.lower()))
    return out


# ---------------------------------------------------------------------------
#  The trash
#
#  Uninstall moves, it does not delete. Each removal is one folder under
#  ~/.mamc/trash holding the pieces and a manifest of where each came from.
#  Restoring is then just reading the manifest backwards.
# ---------------------------------------------------------------------------

def trash_app(app):
    os.makedirs(TRASH, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slot = os.path.join(TRASH, f"{stamp}_{app.name.replace('/', '_')}")
    files_dir = os.path.join(slot, "files")
    os.makedirs(files_dir, exist_ok=True)
    manifest = {"name": app.name, "when": time.time(),
                "when_h": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
    moved, failed = [], []
    for i, src in enumerate(app.targets()):
        stored = os.path.join(files_dir, f"{i:02d}_{os.path.basename(src)}")
        try:
            shutil.move(src, stored)
            manifest["items"].append({"src": src, "stored": stored})
            moved.append(src)
        except Exception as e:
            failed.append((src, str(e)))
    with open(os.path.join(slot, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return slot, moved, failed


def list_trash():
    out = []
    if not os.path.isdir(TRASH):
        return out
    for name in sorted(os.listdir(TRASH), reverse=True):
        mf = os.path.join(TRASH, name, "manifest.json")
        if os.path.isfile(mf):
            try:
                out.append((os.path.join(TRASH, name), json.load(open(mf))))
            except Exception:
                pass
    return out


def restore_slot(slot, manifest):
    ok, bad = [], []
    for item in manifest["items"]:
        src, stored = item["src"], item["stored"]
        if os.path.exists(src):
            bad.append((src, "already exists"))
            continue
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            shutil.move(stored, src)
            ok.append(src)
        except Exception as e:
            bad.append((src, str(e)))
    if not bad:
        shutil.rmtree(slot, ignore_errors=True)
    return ok, bad


# ---------------------------------------------------------------------------
#  Running one app
#
#  The child is started in its OWN SESSION (start_new_session=True) so it gets
#  a process group of its own. That is what makes Q work everywhere: the key is
#  read here, and the whole group is signalled from outside. An app that
#  ignores its own keyboard, or forks a helper, still goes down with it.
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self):
        self.proc = None
        self.app = None
        self.lines = deque(maxlen=600)
        self.started = 0.0
        self.logfile = None
        self.url = None

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def command_for(self, app):
        """What to run, and where. Preference order, most specific first."""
        if app.desc and app.desc.get("run"):
            # The descriptor's run= line carries the app's own environment
            # (host, port, MA_NESTED). Honour it verbatim.
            return ["bash", "-lc", app.desc["run"]], app.path or HOME
        if app.cmd:
            return [app.cmd], app.path or HOME
        if app.entry:
            return [sys.executable, app.entry], os.path.dirname(app.entry)
        return None, None

    def start(self, app):
        if self.alive:
            return "another app is running"
        argv, cwd = self.command_for(app)
        if not argv:
            return "nothing to run: no command and no entry point"
        os.makedirs(LOG_DIR, exist_ok=True)
        self.logfile = os.path.join(LOG_DIR, f"{app.name.replace('/', '_')}.log")
        self.lines.clear()
        self.url = None
        env = dict(os.environ)
        env.setdefault("MA_NESTED", "1")
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = subprocess.Popen(
                argv, cwd=cwd if cwd and os.path.isdir(cwd) else HOME,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, errors="replace",
                bufsize=1, start_new_session=True, env=env)
        except Exception as e:
            self.proc = None
            return f"could not start: {e}"
        self.app = app
        self.started = time.time()
        threading.Thread(target=self._pump, daemon=True).start()
        return None

    def _pump(self):
        proc, out = self.proc, []
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                self.lines.append(line)
                out.append(line)
                if self.url is None:
                    m = URL_RE.search(line)
                    if m:
                        self.url = m.group(0).rstrip(".,")
        except Exception:
            pass
        try:
            with open(self.logfile, "w", errors="replace") as fh:
                fh.write("\n".join(out))
        except Exception:
            pass

    def stop(self, timeout=4.0):
        """SIGTERM the group, then SIGKILL what is left."""
        if not self.proc:
            return
        try:
            pgid = os.getpgid(self.proc.pid)
        except OSError:
            pgid = None
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.proc.poll() is not None:
                break
            try:
                if pgid:
                    os.killpg(pgid, sig)
                else:
                    self.proc.send_signal(sig)
            except OSError as e:
                if e.errno != errno.ESRCH:
                    pass
            deadline = time.time() + (timeout if sig == signal.SIGTERM else 1.5)
            while time.time() < deadline and self.proc.poll() is None:
                time.sleep(0.08)
        self.proc.poll()


# ---------------------------------------------------------------------------
#  The browser
# ---------------------------------------------------------------------------

C_NORM, C_SEL, C_HEAD, C_DIM, C_OK, C_WARN, C_BAR, C_KEY, C_IMP, \
    C_IMPSEL = range(1, 11)


def init_colors():
    if not curses.has_colors():
        return False
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except Exception:
        bg = curses.COLOR_BLACK
    curses.init_pair(C_NORM, curses.COLOR_WHITE, bg)
    curses.init_pair(C_SEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(C_HEAD, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(C_DIM, curses.COLOR_BLUE, bg)
    curses.init_pair(C_OK, curses.COLOR_GREEN, bg)
    curses.init_pair(C_WARN, curses.COLOR_RED, bg)
    curses.init_pair(C_BAR, curses.COLOR_YELLOW, bg)
    curses.init_pair(C_KEY, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(C_IMP, curses.COLOR_MAGENTA, bg)
    curses.init_pair(C_IMPSEL, curses.COLOR_BLACK, curses.COLOR_MAGENTA)
    return True


class UI:
    def __init__(self, scr):
        self.scr = scr
        self.apps = []
        self.filtered = []
        self.idx = 0
        self.top = 0
        self.filter = ""
        self.msg = ""
        self.msg_bad = False
        self.runner = Runner()
        self.color = init_colors()

    # -- drawing helpers ---------------------------------------------------
    def attr(self, pair, bold=False):
        a = curses.color_pair(pair) if self.color else curses.A_NORMAL
        if bold:
            a |= curses.A_BOLD
        return a

    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        text = str(text)[: max(0, w - x - 1)]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def hline(self, y, ch="─"):
        h, w = self.scr.getmaxyx()
        self.put(y, 0, ch * (w - 1), self.attr(C_DIM))

    def note(self, text, bad=False):
        self.msg, self.msg_bad = text, bad

    # -- data --------------------------------------------------------------
    def reload(self, keep_name=None):
        self.apps = discover()
        self.apply_filter()
        if keep_name:
            for i, a in enumerate(self.filtered):
                if a.name == keep_name:
                    self.idx = i
                    break

    def apply_filter(self):
        f = self.filter.lower()
        self.filtered = [a for a in self.apps
                         if not f or f in a.name.lower()
                         or (a.cmd and f in a.cmd.lower())]
        self.idx = min(self.idx, max(0, len(self.filtered) - 1))

    @property
    def current(self):
        return self.filtered[self.idx] if self.filtered else None

    # -- panes -------------------------------------------------------------
    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        split = max(28, int(w * 0.52))

        title = " mamc — the apps on this phone "
        self.put(0, 0, title.ljust(w - 1), self.attr(C_HEAD, True))
        running = ""
        if self.runner.alive:
            running = f" ● running: {self.runner.app.name} "
            self.put(0, max(0, w - 1 - len(running)), running,
                     self.attr(C_HEAD, True))

        self.put(1, 0, f" {len(self.filtered)} apps, most-run first",
                 self.attr(C_DIM))
        if self.filter:
            self.put(1, 12, f"filter: {self.filter}_", self.attr(C_BAR, True))
        self.hline(2)

        list_h = h - 6
        if self.idx < self.top:
            self.top = self.idx
        if self.idx >= self.top + list_h:
            self.top = self.idx - list_h + 1

        for row in range(list_h):
            i = self.top + row
            y = 3 + row
            if i >= len(self.filtered):
                break
            a = self.filtered[i]
            sel = (i == self.idx)
            mark = "!" if a.important else ("R" if a.registered else " ")
            if a.orphan:
                badge, ba = "orphan", C_WARN
            elif port_is_up(a.port):
                badge, ba = "up", C_OK
            else:
                badge, ba = "", C_DIM
            size = human(a.size) if a.path else "-"
            runs = f"{a.runs}\u00d7" if a.runs else ""
            # Build the row to FIT the pane rather than formatting a fixed
            # 57-character line and letting the truncation decide what
            # survives. It always decided the same way: the runs column, the
            # rightmost one, was cut off every time -- so the sorting key the
            # whole list is ordered by was the one number never displayed.
            # Columns are dropped from the least useful inwards instead.
            width = split - 1
            fixed = 3 + 1 + len(runs.rjust(4)) + 1          # mark + gap + runs
            room = width - fixed
            cells = []
            if room >= 34:                                   # cmd survives
                cells.append(("cmd", 12))
            if room >= 22:
                cells.append(("port", 5))
            if room >= 16:
                cells.append(("size", 7))
            used = sum(cw + 1 for _, cw in cells)
            namew = max(8, room - used)
            row = f" {mark} {a.name[:namew]:{namew}}"
            # cw, not w: `for kind, w in cells` rebound the terminal width for
            # the whole rest of draw(). The key bar then measured itself
            # against a seven-column screen and drew nothing, and the detail
            # pane was sliced to a negative width. Nothing raised; the footer
            # simply was not there.
            for kind, cw in cells:
                if kind == "cmd":
                    row += f" {(a.cmd or '-')[:cw]:{cw}}"
                elif kind == "port":
                    row += f" {str(a.port or '-'):>{cw}}"
                else:
                    row += f" {size:>{cw}}"
            line = row + f" {runs:>4} "
            if sel:
                attr = self.attr(C_IMPSEL if a.important else C_SEL, True)
            else:
                attr = self.attr(C_IMP, True) if a.important else self.attr(C_NORM)
            self.put(y, 0, line.ljust(split - 1)[: split - 1], attr)
            if badge and not sel:
                self.put(y, split - 1 - len(badge) - 1, badge, self.attr(ba, True))
            elif badge:
                self.put(y, split - 1 - len(badge) - 1, badge, attr)

        for y in range(3, 3 + list_h):
            self.put(y, split - 1, "│", self.attr(C_DIM))

        self.draw_detail(split + 1, 3, w - split - 2, list_h)

        self.hline(h - 3)
        if self.msg:
            self.put(h - 2, 1, self.msg[: w - 2],
                     self.attr(C_WARN if self.msg_bad else C_OK, True))
        else:
            self.put(h - 2, 1,
                     "↑↓ move   enter run   i info   l log   "
                     "d uninstall   ! flag   r rescan", self.attr(C_DIM))
        keys = [("enter", "run"), ("d", "uninstall"), ("!", "flag"),
                ("t", "trash"), ("/", "filter"), ("q", "quit")]
        x = 1
        for k, label in keys:
            if x + len(k) + len(label) + 3 > w:
                break
            self.put(h - 1, x, f" {k} ", self.attr(C_KEY, True))
            x += len(k) + 2
            self.put(h - 1, x, label + " ", self.attr(C_NORM))
            x += len(label) + 2
        self.scr.refresh()

    def draw_detail(self, x, y, w, h):
        a = self.current
        if not a:
            self.put(y, x, "nothing here", self.attr(C_DIM))
            return
        rows = []
        rows.append((a.name, C_IMP if a.important else C_BAR, True))
        if a.important:
            rows.append(("IMPORTANT - asks twice before it goes", C_IMP, True))
        rows.append(("", C_NORM, False))
        if a.orphan:
            rows.append(("ORPHAN", C_WARN, True))
            rows.append(("a launcher whose app is gone", C_DIM, False))
            rows.append((getattr(a, "missing_target", "?"), C_NORM, False))
            rows.append(("", C_NORM, False))
        elif a.path is None:
            # The third state. An app can have no directory at all and still
            # be perfectly real: MA Player lives entirely in its bin script.
            # bin_only was added to the model and not to this pane, so
            # selecting that one row crashed the whole program on a.path.
            rows.append(("lives in its launcher, no folder of its own",
                         C_DIM, False))
            for c, d in a.cmds:
                rows.append((os.path.join(d, c).replace(HOME, "~"),
                             C_NORM, False))
            rows.append(("", C_NORM, False))
        else:
            rows.append((a.path.replace(HOME, "~"), C_NORM, False))
            rows.append((f"{human(a.size)}   {a.kind}", C_DIM, False))
            if a.entry:
                rows.append((f"entry  {os.path.relpath(a.entry, a.path) if not a.loose else os.path.basename(a.entry)}",
                             C_DIM, False))
            rows.append(("", C_NORM, False))
        rows.append((f"command   {a.cmd or '(none - run by path)'}",
                     C_NORM if a.cmd else C_DIM, False))
        if len(a.cmds) > 1:
            rows.append((f"also      {', '.join(c for c, _ in a.cmds[1:])}",
                         C_DIM, False))
        rows.append((f"port      {a.port or '(unknown)'}", C_NORM, False))
        rows.append((f"runs      {a.runs}"
                     + (f"   last {ago(a.last_run)}" if a.runs else ""),
                     C_OK if a.runs else C_DIM, False))
        if a.registered:
            rows.append((f"ma        registered  ({a.desc['_id']})", C_OK, False))
            if a.desc.get("alias"):
                rows.append((f"aliases   {a.desc['alias']}", C_DIM, False))
        else:
            rows.append(("ma        not registered", C_DIM, False))
        if port_is_up(a.port):
            rows.append(("", C_NORM, False))
            rows.append((f"● something is listening on {a.port}", C_OK, True))
        rows.append(("", C_NORM, False))
        rows.append(("removes:", C_DIM, False))
        for t in a.targets()[:6]:
            rows.append(("  " + t.replace(HOME, "~"), C_DIM, False))
        for i, (text, color, bold) in enumerate(rows):
            if i >= h:
                break
            self.put(y + i, x, text[:w], self.attr(color, bold))

    # -- modal screens -----------------------------------------------------
    def confirm(self, title, lines, danger=True):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        self.put(0, 0, f" {title} ".ljust(w - 1),
                 self.attr(C_WARN if danger else C_HEAD, True))
        y = 2
        for line in lines:
            if y >= h - 3:
                break
            self.put(y, 2, line[: w - 4], self.attr(C_NORM))
            y += 1
        self.put(h - 2, 2, "y = yes,  any other key = no",
                 self.attr(C_BAR, True))
        self.scr.refresh()
        self.scr.nodelay(False)
        k = self.scr.getch()
        return k in (ord("y"), ord("Y"))

    def confirm_word(self, title, lines, word="remove"):
        """The second gate. A key is too easy to press twice, so this one
        wants the word typed and Enter after it."""
        h, w = self.scr.getmaxyx()
        typed = ""
        self.scr.nodelay(False)
        while True:
            self.scr.erase()
            self.put(0, 0, f" {title} ".ljust(w - 1), self.attr(C_IMP, True))
            y = 2
            for line in lines:
                if y >= h - 5:
                    break
                self.put(y, 2, line[: w - 4], self.attr(C_NORM))
                y += 1
            self.put(h - 4, 2, f"type  {word}  and press enter to confirm",
                     self.attr(C_IMP, True))
            self.put(h - 3, 2, "anything else, or esc, keeps it",
                     self.attr(C_DIM))
            self.put(h - 2, 2, "> " + typed + "_", self.attr(C_BAR, True))
            self.scr.refresh()
            k = self.scr.getch()
            if k == 27:
                return False
            if k in (10, 13, curses.KEY_ENTER):
                return typed.strip().lower() == word
            if k in (curses.KEY_BACKSPACE, 127, 8):
                typed = typed[:-1]
            elif 32 <= k < 127:
                typed += chr(k)
                if len(typed) > 40:
                    return False

    def pager(self, title, lines):
        h, w = self.scr.getmaxyx()
        top = 0
        self.scr.nodelay(False)
        while True:
            self.scr.erase()
            self.put(0, 0, f" {title} ".ljust(w - 1), self.attr(C_HEAD, True))
            view = lines[top: top + h - 3]
            for i, line in enumerate(view):
                self.put(2 + i, 1, line[: w - 2], self.attr(C_NORM))
            self.put(h - 1, 1,
                     f"{top + 1}-{top + len(view)} of {len(lines)}   "
                     "↑↓ scroll   q back", self.attr(C_DIM))
            self.scr.refresh()
            k = self.scr.getch()
            if k in (ord("q"), ord("Q"), 27, curses.KEY_LEFT):
                return
            if k in (curses.KEY_DOWN, ord("j")):
                top = min(max(0, len(lines) - 1), top + 1)
            elif k in (curses.KEY_UP, ord("k")):
                top = max(0, top - 1)
            elif k == curses.KEY_NPAGE:
                top = min(max(0, len(lines) - 1), top + h - 4)
            elif k == curses.KEY_PPAGE:
                top = max(0, top - (h - 4))

    # -- the run screen ----------------------------------------------------
    def run_screen(self, app):
        """Start the app, then hold the terminal until Q.

        The whole point of this screen: it reads single keys with no Enter,
        and Q stops the child from outside. Nothing about the app has to
        cooperate.
        """
        err = self.runner.start(app)
        if err:
            self.note(err, bad=True)
            return
        note_run(app)
        ip = lan_ip()
        self.scr.nodelay(True)
        last = 0
        while True:
            now = time.time()
            if now - last > 0.12:
                last = now
                h, w = self.scr.getmaxyx()
                self.scr.erase()
                alive = self.runner.alive
                head = f" running: {app.name} "
                self.put(0, 0, head.ljust(w - 1),
                         self.attr(C_HEAD if alive else C_WARN, True))
                up = int(now - self.runner.started)
                state = (f"● up {up//60:d}m{up%60:02d}s" if alive
                         else f"exited, code {self.runner.proc.returncode}")
                self.put(1, 1, state,
                         self.attr(C_OK if alive else C_WARN, True))
                url = self.runner.url
                if not url and app.port:
                    url = f"http://127.0.0.1:{app.port}"
                if url:
                    self.put(2, 1, url, self.attr(C_BAR, True))
                    if ip and app.port:
                        self.put(2, len(url) + 3,
                                 f"(lan: http://{ip}:{app.port})",
                                 self.attr(C_DIM))
                self.hline(3)
                body = h - 6
                lines = list(self.runner.lines)[-body:]
                for i, line in enumerate(lines):
                    self.put(4 + i, 1, line[: w - 2], self.attr(C_NORM))
                self.hline(h - 2)
                tip = ("  Q  stop it and come back  "
                       if alive else "  Q  back   (it has already exited)  ")
                self.put(h - 1, 1, tip, self.attr(C_KEY, True))
                self.scr.refresh()
            try:
                k = self.scr.getch()
            except curses.error:
                k = -1
            if k in (ord("q"), ord("Q")):
                break
            if k == curses.KEY_RESIZE:
                last = 0
            time.sleep(0.05)
        self.scr.nodelay(False)
        if self.runner.alive:
            h, w = self.scr.getmaxyx()
            self.put(h - 1, 1, "  stopping...  ".ljust(w - 2),
                     self.attr(C_WARN, True))
            self.scr.refresh()
            self.runner.stop()
            self.note(f"{app.name} stopped")
        else:
            code = self.runner.proc.returncode if self.runner.proc else "?"
            self.note(f"{app.name} had already exited (code {code})",
                      bad=(code not in (0, None)))

    # -- actions -----------------------------------------------------------
    def act_uninstall(self, app):
        targets = app.targets()
        if not targets:
            self.note("nothing to remove", bad=True)
            return
        if self.runner.alive and self.runner.app is app:
            self.note("stop it first", bad=True)
            return
        lines = [f"{app.name} — {human(app.size)}", ""]
        lines += ["these move to the trash:"] + \
                 ["   " + t.replace(HOME, "~") for t in targets]
        lines += ["", "Nothing is deleted. `mamc --restore`, or t here,",
                  "puts it all back exactly where it was."]
        if not self.confirm("uninstall", lines):
            self.note("left alone")
            return
        if app.important:
            # The second ask is not a formality. It says what the first one
            # could not: that YOU marked this app as one you rely on, and it
            # makes the answer a whole word rather than the y your thumb has
            # already pressed once.
            if not self.confirm_word(
                    "IMPORTANT - asking a second time",
                    [f"{app.name} is flagged important.",
                     "",
                     "You flagged it, which means you use it.",
                     f"{len(targets)} pieces move to the trash.",
                     ""],
                    word="remove"):
                self.note("left alone - it is flagged important")
                return
        slot, moved, failed = trash_app(app)
        if failed:
            self.note(f"moved {len(moved)}, {len(failed)} failed", bad=True)
        else:
            self.note(f"{app.name}: {len(moved)} pieces moved to the trash")
        self.reload()

    def act_trash(self):
        slots = list_trash()
        if not slots:
            self.note("the trash is empty")
            return
        idx = 0
        self.scr.nodelay(False)
        while True:
            h, w = self.scr.getmaxyx()
            self.scr.erase()
            self.put(0, 0, " trash ".ljust(w - 1), self.attr(C_HEAD, True))
            self.put(1, 1, "r restore   x delete for good   q back",
                     self.attr(C_DIM))
            for i, (slot, mf) in enumerate(slots):
                if 3 + i >= h - 1:
                    break
                sel = (i == idx)
                line = (f" {mf['name'][:22]:22} {mf['when_h']:20} "
                        f"{len(mf['items'])} pieces ")
                self.put(3 + i, 0, line.ljust(w - 1),
                         self.attr(C_SEL, True) if sel else self.attr(C_NORM))
            self.scr.refresh()
            k = self.scr.getch()
            if k in (ord("q"), ord("Q"), 27):
                break
            elif k in (curses.KEY_DOWN, ord("j")):
                idx = min(len(slots) - 1, idx + 1)
            elif k in (curses.KEY_UP, ord("k")):
                idx = max(0, idx - 1)
            elif k in (ord("r"), ord("R")) and slots:
                slot, mf = slots[idx]
                ok, bad = restore_slot(slot, mf)
                self.note(f"restored {len(ok)}" +
                          (f", {len(bad)} could not be" if bad else ""),
                          bad=bool(bad))
                slots = list_trash()
                idx = min(idx, max(0, len(slots) - 1))
                if not slots:
                    break
            elif k in (ord("x"), ord("X")) and slots:
                slot, mf = slots[idx]
                if self.confirm("delete for good",
                                [f"{mf['name']}, {len(mf['items'])} pieces",
                                 "", "This one cannot be undone."]):
                    shutil.rmtree(slot, ignore_errors=True)
                    slots = list_trash()
                    idx = min(idx, max(0, len(slots) - 1))
                    self.note("gone for good")
                    if not slots:
                        break
        self.reload()

    def act_info(self, app):
        lines = [app.name, ""]
        if app.path:
            lines += [f"path    {app.path}", f"size    {human(app.size)}",
                      f"kind    {app.kind}"]
        if app.entry:
            lines.append(f"entry   {app.entry}")
        lines.append(f"port    {app.port or '(unknown)'}")
        lines.append(f"runs    {app.runs}   last {ago(app.last_run)}")
        lines.append(f"flagged {'important' if app.important else 'no'}")
        lines.append("")
        lines.append("commands:")
        lines += [f"   {c}   ({d})" for c, d in app.cmds] or ["   (none)"]
        lines.append("")
        if app.desc:
            lines.append("ma descriptor:")
            lines += [f"   {k}={v}" for k, v in app.desc.items()
                      if not k.startswith("_")]
        else:
            lines.append("not registered with ma")
        lines += ["", "owns:"]
        lines += ["   " + t for t in app.targets()]
        self.pager(f"info: {app.name}", lines)

    def act_log(self, app):
        path = os.path.join(LOG_DIR, f"{app.name.replace('/', '_')}.log")
        candidates = [path]
        if app.path:
            candidates.append(os.path.join(app.path, "server.log"))
        for c in candidates:
            if os.path.isfile(c):
                try:
                    lines = open(c, errors="replace").read().splitlines()
                except OSError:
                    continue
                self.pager(f"log: {os.path.basename(c)}",
                           lines[-2000:] or ["(empty)"])
                return
        self.note("no log yet", bad=True)

    # -- main loop ---------------------------------------------------------
    def loop(self):
        self.reload()
        typing_filter = False
        while True:
            self.draw()
            self.scr.nodelay(False)
            k = self.scr.getch()

            if typing_filter:
                if k in (10, 13, curses.KEY_ENTER, 27):
                    typing_filter = False
                    self.note("")
                elif k in (curses.KEY_BACKSPACE, 127, 8):
                    self.filter = self.filter[:-1]
                    self.apply_filter()
                elif 32 <= k < 127:
                    self.filter += chr(k)
                    self.apply_filter()
                continue

            if k in (ord("q"), ord("Q")):
                if self.runner.alive:
                    if self.confirm("quit",
                                    [f"{self.runner.app.name} is still running.",
                                     "", "Quitting stops it."], danger=False):
                        self.runner.stop()
                        return
                    continue
                return
            elif k in (curses.KEY_DOWN, ord("j")):
                self.idx = min(len(self.filtered) - 1, self.idx + 1)
            elif k in (curses.KEY_UP, ord("k")):
                self.idx = max(0, self.idx - 1)
            elif k == curses.KEY_NPAGE:
                self.idx = min(len(self.filtered) - 1, self.idx + 10)
            elif k == curses.KEY_PPAGE:
                self.idx = max(0, self.idx - 10)
            elif k == curses.KEY_HOME:
                self.idx = 0
            elif k == curses.KEY_END:
                self.idx = max(0, len(self.filtered) - 1)
            elif k == ord("/"):
                typing_filter = True
                self.note("typing a filter - enter when done")
            elif k in (10, 13, curses.KEY_ENTER, ord("\n")):
                a = self.current
                if not a:
                    continue
                if a.orphan:
                    self.note("orphan: nothing to run, but d clears it",
                              bad=True)
                elif self.runner.alive:
                    self.note("one at a time - stop the running one first",
                              bad=True)
                else:
                    self.run_screen(a)
            elif k in (ord("i"), ord("I")) and self.current:
                self.act_info(self.current)
            elif k in (ord("l"), ord("L")) and self.current:
                self.act_log(self.current)
            elif k in (ord("d"), ord("D"), curses.KEY_F8) and self.current:
                self.act_uninstall(self.current)
            elif k in (ord("!"), ord("f"), ord("F")) and self.current:
                a = self.current
                if a.important:
                    IMPORTANT.pop(a.ident, None)
                    self.note(f"{a.name} is no longer flagged")
                else:
                    IMPORTANT[a.ident] = a.name
                    self.note(f"{a.name} flagged important")
                save_important(IMPORTANT)
            elif k in (ord("t"), ord("T")):
                self.act_trash()
            elif k in (ord("r"), ord("R")):
                name = self.current.name if self.current else None
                self.note("rescanning...")
                self.draw()
                self.reload(keep_name=name)
                self.note(f"{len(self.apps)} apps")
            elif k == curses.KEY_RESIZE:
                pass


def cli_list():
    apps = discover()
    print(f"{'':2} {'app':24} {'command':16} {'port':>5} {'size':>8} "
          f"{'runs':>5}  {'last run':<10} state")
    for a in apps:
        mark = "!" if a.important else ("R" if a.registered else " ")
        state = "orphan" if a.orphan else ("up" if port_is_up(a.port) else "")
        print(f"{mark:2} {a.name[:24]:24} {(a.cmd or '-')[:16]:16} "
              f"{str(a.port or '-'):>5} {human(a.size) if a.path else '-':>8} "
              f"{a.runs:>5}  {ago(a.last_run) if a.runs else '':<10} {state}")
    print(f"\n{len(apps)} apps   R = registered with ma   "
          f"! = important, asks twice")


def cli_restore():
    slots = list_trash()
    if not slots:
        print("the trash is empty")
        return
    for i, (slot, mf) in enumerate(slots):
        print(f"  {i}  {mf['name']:24} {mf['when_h']}  "
              f"{len(mf['items'])} pieces")
    try:
        pick = input("\nrestore which? [number, or enter to cancel] ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not pick.isdigit() or int(pick) >= len(slots):
        return
    slot, mf = slots[int(pick)]
    ok, bad = restore_slot(slot, mf)
    for p in ok:
        print(f"  restored  {p}")
    for p, why in bad:
        print(f"  FAILED    {p}  ({why})")


def main():
    if "--list" in sys.argv or "-l" in sys.argv:
        return cli_list()
    if "--restore" in sys.argv:
        return cli_restore()
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return
    ui = None

    def run(scr):
        nonlocal ui
        curses.curs_set(0)
        scr.keypad(True)
        ui = UI(scr)
        ui.loop()

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    finally:
        if ui and ui.runner.alive:
            ui.runner.stop()


if __name__ == "__main__":
    main()
