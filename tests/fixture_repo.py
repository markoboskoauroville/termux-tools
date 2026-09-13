"""A checkout of these programs with a GitHub of its own, on disk, for the tests.

Three tests need the same thing: a checkout that is one version BEHIND an origin it can reach
without a network, so the U key can be pressed for real and the pull, the link step and the
restart all happen -- against a folder of their own, never against the commands on the PATH.

    bare/        the "GitHub": commit A (no version.py, the unnumbered delivery) then
                 commit B (version.py = 1)
    checkout/    cloned from bare, reset to A: installed unnumbered, origin one ahead

env_for() is the environment that points mamc and the installer at the fixture:
MAMC_REPO at the checkout, TT_BIN / TT_LOCAL_BIN / TT_HOME inside the temp folder.
"""
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True,
                          timeout=30, env=dict(os.environ, GIT_TERMINAL_PROMPT="0",
                                               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def make(latest=1):
    """Returns (tmp, bare, checkout). The caller removes tmp."""
    tmp = tempfile.mkdtemp(prefix="tt-fixture-")
    bare = os.path.join(tmp, "bare")
    work = os.path.join(tmp, "work")
    checkout = os.path.join(tmp, "checkout")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True, timeout=30)
    subprocess.run(["git", "clone", "-q", bare, work], check=True, timeout=30,
                   stderr=subprocess.DEVNULL)
    _git(work, "checkout", "-q", "-b", "main")
    os.makedirs(os.path.join(work, "mamc"))
    for rel in ("mamc/mamc.py", "mamc/mamc", "install-termux.sh"):
        shutil.copy2(os.path.join(SRC, rel), os.path.join(work, rel))
    for d in ("talker", "notify"):
        shutil.copytree(os.path.join(SRC, d), os.path.join(work, d),
                        ignore=shutil.ignore_patterns("__pycache__"))
    _git(work, "add", "-A")
    r = _git(work, "commit", "-q", "-m", "A: the unnumbered delivery")
    assert r.returncode == 0, r.stderr
    with open(os.path.join(work, "version.py"), "w") as f:
        f.write('"""fixture"""\n\nAPP_VERSION = %d\n' % latest)
    _git(work, "add", "-A")
    r = _git(work, "commit", "-q", "-m", "B: version.py")
    assert r.returncode == 0, r.stderr
    r = _git(work, "push", "-q", "origin", "main")
    assert r.returncode == 0, r.stderr
    subprocess.run(["git", "clone", "-q", bare, checkout], check=True, timeout=30,
                   stderr=subprocess.DEVNULL)
    r = _git(checkout, "reset", "-q", "--hard", "HEAD~1")
    assert r.returncode == 0, r.stderr
    return tmp, bare, checkout


def env_for(tmp, checkout):
    e = dict(os.environ)
    e["MAMC_REPO"] = checkout
    e["TT_BIN"] = os.path.join(tmp, "bin")
    e["TT_LOCAL_BIN"] = os.path.join(tmp, "localbin")
    e["TT_HOME"] = os.path.join(tmp, "home")
    return e


def apply_env(tmp, checkout):
    """For a test that calls mamc's functions in-process: the installer the update runs
    reads TT_* from the environment of THIS process."""
    for k, v in env_for(tmp, checkout).items():
        os.environ[k] = v
