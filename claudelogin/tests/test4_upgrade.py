#!/usr/bin/env python3
"""Test 4: the upgrade. Closes "it works on a machine that never ran the old version".

A GitHub on disk (a bare repository holding this checkout's claudelogin/ at v1), installed for
real by install.sh into a sandbox; then origin moves to v2 and the update is taken both ways:
the installer run again, and the u key in a running claudelogin. The commands on the PATH are
written into a sandbox (CL_BIN, CL_LOCAL_BIN), never the real ones.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import APP_DIR, FAKE_BIN, Session, check, finish, http  # noqa: E402

print("Test 4: the upgrade")

T = tempfile.mkdtemp(prefix="cl-up-")
HOME = os.path.join(T, "home")
BIN = os.path.join(T, "prefix-bin")
LBIN = os.path.join(HOME, ".local", "bin")
BARE = os.path.join(T, "origin.git")
WORK = os.path.join(T, "author")
os.makedirs(HOME)
GENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t", GIT_TERMINAL_PROMPT="0")


def sh(*cmd, cwd=None, env=None):
    return subprocess.run(list(cmd), cwd=cwd, env=env or GENV, capture_output=True, text=True)


def env_for(home=HOME):
    path = ":".join([BIN, FAKE_BIN, os.path.dirname(sys.executable), "/usr/bin", "/bin"])
    return dict(GENV, HOME=home, PATH=path, CL_BIN=BIN, CL_LOCAL_BIN=LBIN,
                CL_REPO_URL=BARE, CL_BRANCH="main",
                FAKE_DIR=T, FAKE_PACKAGES="org.mozilla.firefox")


def set_version(n):
    p = os.path.join(WORK, "claudelogin", "version.py")
    s = open(p).read()
    open(p, "w").write(re.sub(r"(?m)^APP_VERSION = \d+", "APP_VERSION = %d" % n, s))
    sh("git", "commit", "-qam", "v%d" % n, cwd=WORK)
    sh("git", "push", "-q", "origin", "main", cwd=WORK)


try:
    # the GitHub on disk, at v1
    sh("git", "init", "-q", "--bare", "-b", "main", BARE)
    sh("git", "clone", "-q", BARE, WORK)
    shutil.copytree(APP_DIR, os.path.join(WORK, "claudelogin"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    # the rest of the suite, which installing claudelogin alone must not bring
    os.makedirs(os.path.join(WORK, "mamc"))
    open(os.path.join(WORK, "mamc", "mamc.py"), "w").write("# mamc\n")
    open(os.path.join(WORK, "install-termux.sh"), "w").write("# the suite's installer\n")
    sh("git", "checkout", "-q", "-b", "main", cwd=WORK)
    sh("git", "add", "-A", cwd=WORK)
    sh("git", "commit", "-qm", "v1", cwd=WORK)
    set_version(1)
    r = sh("git", "push", "-q", "origin", "main", cwd=WORK)

    # a stale command already on the PATH, as an older install would leave
    os.makedirs(BIN)
    stale = os.path.join(BIN, "claudelogin")
    open(stale, "w").write("#!/bin/sh\necho stale\n")
    os.chmod(stale, 0o755)
    old_inode = os.stat(stale).st_ino

    # a ~/termux-tools checkout the phone already has (mamc's): installing claudelogin leaves it be
    os.makedirs(os.path.join(HOME, "termux-tools"))
    open(os.path.join(HOME, "termux-tools", "mine"), "w").write("untouched\n")

    # 1. the installer, for real, from the GitHub on disk
    r = sh("bash", os.path.join(WORK, "claudelogin", "install.sh"), env=env_for())
    check("install: exits 0", r.returncode == 0, r.stdout[-500:] + r.stderr[-500:])
    check("install: the dependency table came first", r.stdout.find("python") < r.stdout.find("done"))
    check("install: cloned into ~/claudelogin", os.path.isfile(os.path.join(HOME, "claudelogin", "claudelogin", "claudelogin.py")))
    on_disk = sorted(f for f in os.listdir(os.path.join(HOME, "claudelogin")) if f != ".git")
    check("install: ONLY claudelogin is on disk, not the suite", on_disk == ["claudelogin"], on_disk)
    check("install: ~/termux-tools left as it was", os.listdir(os.path.join(HOME, "termux-tools")) == ["mine"])
    check("install: no half-made folder left", not os.path.exists(os.path.join(HOME, "claudelogin.new")))
    for d in (BIN, LBIN):
        p = os.path.join(d, "claudelogin")
        check("install: %s/claudelogin is there and executable" % os.path.basename(d), os.access(p, os.X_OK))
    check("install: the stale command was replaced by a rename, not truncated", os.stat(stale).st_ino != old_inode)
    check("install: no .new left behind", not [f for d in (BIN, LBIN) for f in os.listdir(d) if f.endswith(".new")])
    body = open(stale).read()
    check("install: the command holds no home path (resolved when it runs)", HOME not in body, body)

    def version_via(home, extra=None):
        e = env_for(home)
        e.update(extra or {})
        # through bash: the shebang is Termux's bash, which this machine does not have
        r = sh("bash", os.path.join(BIN, "claudelogin"), "--version", env=e)
        return (r.stdout + r.stderr).strip()

    check("install: the command runs: v1", version_via(HOME) == "claudelogin v1", version_via(HOME))
    # the other spelling of home: the same command, a different $HOME holding the app
    OTHER = os.path.join(T, "other-home")
    shutil.copytree(os.path.join(HOME, "claudelogin"), os.path.join(OTHER, "claudelogin"), symlinks=True)
    ov = os.path.join(OTHER, "claudelogin", "claudelogin", "version.py")
    text = open(ov).read()
    open(ov, "w").write(re.sub(r"(?m)^APP_VERSION = \d+", "APP_VERSION = 99", text))
    check("install: from another home it runs THAT home's copy", version_via(OTHER) == "claudelogin v99", version_via(OTHER))
    EMPTY = os.path.join(T, "empty-home")
    os.makedirs(EMPTY)
    out = version_via(EMPTY)
    if os.path.exists("/root/claudelogin/claudelogin/claudelogin.py") or os.path.exists("/data/data/com.termux/files/home/claudelogin/claudelogin/claudelogin.py"):
        print("  (skipped: this machine has a real install, so 'not found' cannot be shown)")
    else:
        check("install: where it is not, it names the places it tried", "not found in" in out and "/root/claudelogin/claudelogin" in out, out)

    # a ~/claudelogin that is somebody's own folder: refused, not nested into
    THEIRS = os.path.join(T, "theirs-home")
    os.makedirs(os.path.join(THEIRS, "claudelogin"))
    r = sh("bash", os.path.join(WORK, "claudelogin", "install.sh"), env=env_for(THEIRS))
    check("install: a ~/claudelogin that is not a checkout is refused", r.returncode == 1 and "is not a checkout" in r.stdout and os.listdir(os.path.join(THEIRS, "claudelogin")) == [], r.stdout[-300:])

    # 2. the installer again, unchanged: changes nothing
    before = open(stale).read()
    r = sh("bash", os.path.join(WORK, "claudelogin", "install.sh"), env=env_for())
    check("again: exits 0 and the command is the same", r.returncode == 0 and open(stale).read() == before)

    # 3. origin moves to v2; the installer run again takes it
    set_version(2)
    r = sh("bash", os.path.join(WORK, "claudelogin", "install.sh"), env=env_for())
    check("installer over v1: now v2", r.returncode == 0 and version_via(HOME) == "claudelogin v2", version_via(HOME))

    # 4. the u key: a running v2 sees v3 on origin, y, and restarts as v3 on the same port
    set_version(3)
    s = Session.__new__(Session)
    # a Session that runs the INSTALLED program, through the installed command
    import pty
    s.dir, s.home, s.out, s.status, s.tty = T, HOME, "", None, True
    e = env_for(HOME)
    e["CLAUDELOGIN_CLIP_EVERY"] = "0.2"
    s.pid, s.fd = pty.fork()
    if s.pid == 0:
        os.chdir(HOME)
        os.execve("/bin/bash", ["bash", os.path.join(BIN, "claudelogin")], e)
    try:
        check("u: running v2", bool(s.wait_for(r"claudelogin v2")))
        s.wait_for("watching the clipboard")
        port = s.port()
        http(port, "/api/state")          # a connection, so the port has a TIME_WAIT to survive
        s.send("u")
        check("u: shows installed -> available", bool(s.wait_for(r"installed v2 -> available v3", 30)), s.text()[-400:])
        s.send("y")
        # the BANNER, not the installer's "done  claudelogin v3 on the PATH", which comes first:
        # matching that one raced the restart and probed the port mid-exec
        check("u: restarted as v3", bool(s.wait_for(r"restarting\.\.\.\s+claudelogin v3 +Claude Code sign-in", 30)), s.text()[-400:])
        check("u: the same pid (restarted in place)", not s.exited())
        m = s.wait_for(r"restarting\.\.\.\s+claudelogin v3 .*\s+page +http://127\.0\.0\.1:(\d+)")
        check("u: the same port after the restart", m and int(m.group(1)) == port, (m and m.group(1), port))
        check("u: the command on the PATH is v3 too", version_via(HOME) == "claudelogin v3")
        st, r2 = http(port, "/api/state")
        check("u: the page answers, as v3", st == 200 and r2.get("version") == 3, r2)
        s.send("u")
        check("u: at the newest, it says so", bool(s.wait_for(r"installed v3 -> available v3(.|\n)*up to date", 30)))
    finally:
        s.close() if hasattr(s, "pid") else None
finally:
    shutil.rmtree(T, ignore_errors=True)

sys.exit(finish("Test 4"))
