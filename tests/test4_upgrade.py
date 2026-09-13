#!/usr/bin/env python3
"""TEST 4 - the upgrade, from the version before.

Nobody installs mamc fresh. The phone already has one, with run counts, with
flags, with things sitting in its trash, and quite possibly with the old one
still running. The question is not "does it install" but "does it install over
what is already there and leave every one of those intact".

The version before (the delivery of 13.9.2026) was a COPY of mamc.py in
~/.mamc with a wrapper on the PATH naming that copy; it knew no version, no
registry and no U key. The new one runs from the checkout, and the upgrade is
the installer's link step. Both are done for real here, against a bin folder
and a home of the test's own (TT_BIN, TT_LOCAL_BIN, TT_HOME) so the commands
on the real PATH are never rewritten by a test.

The steps the manifest sets out:
  1  install the PREVIOUS version, for real
  2  USE it: make data, change settings, let it write its own files
  3  leave it RUNNING
  4  install the new version on top
  5  check everything
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
NEW_PY = os.path.join(SRC, "mamc", "mamc.py")
MAMC = os.path.join(HOME, ".mamc")
sys.path.insert(0, HERE)
fails, count = [], 0


def check(label, ok, detail=""):
    global count
    count += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(f"{label} {detail}")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- keep the real state safe for the duration ---------------------------
BACKUP = tempfile.mkdtemp(prefix="mamc-t4-backup-")
for f in ("runs.json", "important.json", "categories.json"):
    if os.path.exists(os.path.join(MAMC, f)):
        shutil.copy2(os.path.join(MAMC, f), BACKUP)
new_source = open(NEW_PY).read()
T = tempfile.mkdtemp(prefix="mamc-t4-")
TBIN, TLBIN, THOME = os.path.join(T, "bin"), os.path.join(T, "localbin"), os.path.join(T, "home")
OLD_DIR = os.path.join(THOME, ".mamc")
OLD_PY = os.path.join(OLD_DIR, "mamc.py")
env = dict(os.environ, TT_BIN=TBIN, TT_LOCAL_BIN=TLBIN, TT_HOME=THOME)
runner = None

try:
    # --- 1. install the PREVIOUS version, for real ------------------------
    # A real older version, not the new one wearing a label: the registry
    # and the update are CUT OUT, so this copy genuinely behaves the way
    # mamc did before they existed. The manifest is blunt about why --
    # assert the absence of the new feature, or you may have installed the
    # new version twice and proved nothing at all.
    old_source = new_source
    a = old_source.index("def registry_port(cmd):")
    b = old_source.index("def lan_ip():")
    old_source = old_source[:a] + old_source[b:]
    a = old_source.index("class UpdateError(Exception):")
    b = old_source.index("# ---------------------------------------------------------------------------\n#  Important apps")
    old_source = old_source[:a] + old_source[b:]
    old_source = old_source.replace(
        'CATEGORIES_FILE = os.path.join(MAMC_DIR, "categories.json")',
        'CATEGORIES_FILE = os.path.join(MAMC_DIR, "categories.json")\nOLD_VERSION = True')
    import ast as _ast; _ast.parse(old_source)
    os.makedirs(OLD_DIR); os.makedirs(TBIN); os.makedirs(TLBIN)
    open(OLD_PY, "w").write(old_source)
    for d in (TBIN, TLBIN):
        open(os.path.join(d, "mamc"), "w").write(
            '#!/data/data/com.termux/files/usr/bin/bash\n'
            '# mamc - browse, run and uninstall the apps on this phone.\n'
            'exec python3 "%s" "$@"\n' % OLD_PY)
        os.chmod(os.path.join(d, "mamc"), 0o755)
    old = load("mamc_old", OLD_PY)
    check("the previous version really is the old one", hasattr(old, "OLD_VERSION"))
    check("and it has NO registry", not hasattr(old, "live_port"))
    check("and NO update", not hasattr(old, "check_update"))
    check("its wrapper names the copy in ~/.mamc", OLD_PY in open(os.path.join(TBIN, "mamc")).read())
    r = subprocess.run(["bash", os.path.join(TBIN, "mamc"), "--list"], capture_output=True, text=True, timeout=120)
    check("the old install runs", r.returncode == 0 and "apps in" in r.stdout, r.stderr[-300:])

    # --- 2. USE it: let it write its own files ----------------------------
    fixture = os.path.join(HOME, ".zz_upgrade")
    os.makedirs(fixture, exist_ok=True)
    open(os.path.join(fixture, "app.py"), "w").write(
        "from flask import Flask\nimport time\nPORT=9095\ntime.sleep(600)\n")
    app = next((a for a in old.discover() if a.path == fixture), None)
    check("the old version sees the fixture app", app is not None)
    flags = old.load_important()
    flags[fixture] = "zz_upgrade"
    old.save_important(flags)
    for _ in range(7):
        old.note_run(app)
    old.trash_app(old.App("zz_ghost", None))          # an empty trash slot
    check("the old version recorded 7 runs", int(old.load_runs()[fixture]["count"]) == 7)
    check("the old version recorded the flag", fixture in old.load_important())
    trash_before = len(old.list_trash())
    cats = os.path.join(MAMC, "categories.json")
    had_cats = os.path.exists(cats)

    # --- 3. leave it RUNNING ---------------------------------------------
    runner = old.Runner()
    runner.start(app)
    time.sleep(3)
    check("an app is running under the OLD version", runner.alive)
    old_pid = runner.proc.pid

    # --- 4. install the new version on top --------------------------------
    r = subprocess.run(["bash", os.path.join(SRC, "install-termux.sh"), "link"],
                       capture_output=True, text=True, timeout=120, env=env)
    check("the link step ran", r.returncode == 0, (r.stdout + r.stderr)[-400:])
    wrapper = open(os.path.join(TBIN, "mamc")).read()
    check("the wrapper now names the checkout", NEW_PY in wrapper, wrapper)
    check("and so does the one in ~/.local/bin", NEW_PY in open(os.path.join(TLBIN, "mamc")).read())
    check("the old copy in ~/.mamc is gone", not os.path.exists(OLD_PY))
    check("the wrapper was renamed into place, not truncated", not os.path.exists(os.path.join(TBIN, "mamc.new")))
    check("the talker's copies were refreshed", os.path.exists(os.path.join(THOME, ".talker", "talker.py")))
    check("and the notifier's", os.path.exists(os.path.join(THOME, ".ccnotify", "ccnotify.py")))
    r = subprocess.run(["bash", os.path.join(TBIN, "mamc"), "--version"], capture_output=True, text=True, timeout=60)
    check("the upgraded command says its version", r.stdout.startswith("mamc v"), r.stdout + r.stderr)
    new = load("mamc_new", NEW_PY)
    check("the new version is installed", hasattr(new, "live_port") and hasattr(new, "check_update"))

    # --- 5. check everything ---------------------------------------------
    print("  -- what the old version wrote --")
    check("run counts survive with their VALUE", int(new.load_runs()[fixture]["count"]) == 7)
    check("the important flag survives", fixture in new.load_important())
    apps = {a.path: a for a in new.discover()}
    check("the new version reads the old count off the app", apps[fixture].runs == 7)
    check("the new version shows it as flagged", apps[fixture].important)
    check("trash written by the old version is still listed", len(new.list_trash()) >= trash_before)
    check("categories.json was not written by looking", os.path.exists(cats) == had_cats)

    print("  -- the process the old version started --")
    check("a process started by the old version is still there", runner.alive)
    r2 = new.Runner()
    r2.proc, r2.app, r2.pgid, r2.master = runner.proc, app, runner.pgid, runner.master
    r2.stop()
    time.sleep(0.8)
    check("the NEW version can stop a process the OLD one started", not r2.alive)
    left = subprocess.run(["ps", "-o", "pid=", "-p", str(old_pid)], capture_output=True, text=True).stdout.strip()
    check("nothing of it is left", left == "", f"-> {left!r}")
    runner = None

    print("  -- a phone that never had the registry --")
    saved = new.PORTS_DIR
    new.PORTS_DIR = os.path.join(T, "no-such-dir")
    check("no ~/.mantra/ports: the fixture has no live port and nothing raises",
          new.live_port(apps[fixture]) is None)
    check("its port from the source is still known", apps[fixture].port == 9095)
    new.PORTS_DIR = saved
    check("this checkout is numbered", isinstance(new.installed_version(), int))

    print("  -- doing it twice --")
    r = subprocess.run(["bash", os.path.join(SRC, "install-termux.sh"), "link"],
                       capture_output=True, text=True, timeout=120, env=env)
    check("the link step again changes nothing", r.returncode == 0 and NEW_PY in open(os.path.join(TBIN, "mamc")).read())
    check("and the flag and counts are untouched",
          int(new.load_runs()[fixture]["count"]) == 7 and fixture in new.load_important())

    print("  -- a file written by the OLD code is still understood --")
    check("runs.json keys are still paths, not names",
          all(k.startswith("/") or k.startswith("cmd:") or k.startswith("name:") for k in new.load_runs()))

finally:
    if runner is not None and runner.alive:
        runner.stop()
    shutil.rmtree(os.path.join(HOME, ".zz_upgrade"), ignore_errors=True)
    for f in ("runs.json", "important.json", "categories.json"):
        src = os.path.join(BACKUP, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(MAMC, f))
        elif f == "categories.json" and os.path.exists(os.path.join(MAMC, f)):
            os.remove(os.path.join(MAMC, f))
    mod = load("mamc_cleanup", NEW_PY)
    for slot, mf in mod.list_trash():
        if mf["name"] in ("zz_upgrade", "zz_ghost"):
            shutil.rmtree(slot, ignore_errors=True)
    shutil.rmtree(BACKUP, ignore_errors=True)
    shutil.rmtree(T, ignore_errors=True)

print(f"\n{count} checks, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
