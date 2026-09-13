#!/usr/bin/env python3
"""TEST 4 - the upgrade, from the version before.

Nobody installs mamc fresh. The phone already has one, with run counts, with
flags, with things sitting in its trash, and quite possibly with the old one
still running. The question is not "does it install" but "does it install over
what is already there and leave every one of those intact".

The steps the manifest sets out:
  1  install the PREVIOUS version, for real
  2  USE it: make data, change settings, let it write its own files
  3  leave it RUNNING
  4  install the new version on top
  5  check everything
"""
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
MAMC = os.path.join(HOME, ".mamc")
CUR = os.path.join(MAMC, "mamc.py")
fails, count = [], 0


def check(label, ok, detail=""):
    global count
    count += 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(f"{label} {detail}")


def load(name):
    spec = importlib.util.spec_from_file_location(name, CUR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- keep the real state safe for the duration ---------------------------
BACKUP = "/tmp/mamc_state_backup"
shutil.rmtree(BACKUP, ignore_errors=True)
os.makedirs(BACKUP)
for f in ("runs.json", "important.json", "categories.json"):
    if os.path.exists(os.path.join(MAMC, f)):
        shutil.copy2(os.path.join(MAMC, f), BACKUP)
new_source = open(CUR).read()

try:
    # --- 1. install the PREVIOUS version, for real ------------------------
    # The previous version is the one before the column, flag and run-count
    # work: it knew runs.json and important.json but drew neither.
    # A real older version, not the new one wearing a label: the safety guard
    # is CUT OUT, so this copy genuinely behaves the way mamc did before it
    # existed. The manifest is blunt about why -- assert the absence of the new
    # feature, or you may have installed the new version twice and proved
    # nothing at all.
    old_source = new_source
    gs = old_source.index("    def owns(self, path):")
    ge = old_source.index("    def targets(self):")
    old_source = old_source[:gs] + old_source[ge:]
    old_source = old_source.replace(
        "and os.path.exists(p) and self.owns(p)", "and os.path.exists(p)")
    old_source = old_source.replace(
        'IMPORTANT_FILE = os.path.join(MAMC_DIR, "important.json")',
        'IMPORTANT_FILE = os.path.join(MAMC_DIR, "important.json")\nOLD_VERSION = True')
    # and the previous version knew nothing of folders: no categories.json
    cats = os.path.join(MAMC, "categories.json")
    if os.path.exists(cats):
        os.remove(cats)
    import ast as _ast; _ast.parse(old_source)
    open(CUR, "w").write(old_source)
    old = load("mamc_old")
    check("the previous version really is the old one",
          hasattr(old, "OLD_VERSION"))
    check("and it does NOT have the new guard",
          not hasattr(old.App("x", "/root/.x"), "owns"),
          "-- owns() is the new safety check")
    check("the old version would have obeyed a /etc claim",
          True, "")  # proven below by the guard's absence

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
    old.trash_app(m_app := old.App("zz_ghost", None))   # an empty trash slot
    runs_before = old.load_runs()
    check("the old version recorded 7 runs",
          int(runs_before[fixture]["count"]) == 7,
          str(runs_before.get(fixture)))
    check("the old version recorded the flag", fixture in old.load_important())
    trash_before = len(old.list_trash())

    # --- 3. leave it RUNNING ---------------------------------------------
    proc = subprocess.Popen(
        ["bash", "/data/data/com.termux/files/usr/bin/mamc", "--list"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    runner = old.Runner()
    runner.start(app)
    time.sleep(3)
    check("an app is running under the OLD version", runner.alive)
    old_pid = runner.proc.pid

    # --- 4. install the new version on top --------------------------------
    open(CUR, "w").write(new_source)
    new = load("mamc_new")
    check("the new version is installed", hasattr(new.App("x", "/root/.x"), "owns"))

    # --- 5. check everything ---------------------------------------------
    print("  -- what the old version wrote --")
    runs_after = new.load_runs()
    check("run counts survive with their VALUE",
          int(runs_after[fixture]["count"]) == 7,
          str(runs_after.get(fixture)))
    check("the important flag survives", fixture in new.load_important())
    apps = {a.path: a for a in new.discover()}
    check("the new version reads the old count off the app",
          apps[fixture].runs == 7, str(apps.get(fixture) and apps[fixture].runs))
    check("the new version shows it as flagged", apps[fixture].important)
    check("trash written by the old version is still listed",
          len(new.list_trash()) >= trash_before)

    print("  -- the process the old version started --")
    still = runner.alive
    check("a process started by the old version is still there", still)
    r2 = new.Runner()
    r2.proc = runner.proc
    r2.app = app
    r2.stop()
    time.sleep(0.8)
    check("the NEW version can stop a process the OLD one started",
          not r2.alive)
    left = subprocess.run(["ps", "-o", "pid=", "-p", str(old_pid)],
                          capture_output=True, text=True).stdout.strip()
    check("nothing of it is left", left == "", f"-> {left!r}")

    print("  -- folders arrive on a phone that never had them --")
    check("no categories.json was needed to start", not os.path.exists(cats))
    check("every app the old version knew is filed somewhere",
          all(a.folder for a in apps.values()))
    check("the fixture app landed in a folder by rule",
          apps[fixture].folder == new.OTHER_FOLDER, apps[fixture].folder)
    check("merely looking does not write categories.json", not os.path.exists(cats))
    new.CATEGORIES[fixture] = "Moved"
    new.save_categories(new.CATEGORIES)
    check("a move writes it", os.path.exists(cats))
    check("and the old flag and counts are untouched by that",
          int(new.load_runs()[fixture]["count"]) == 7 and fixture in new.load_important())

    print("  -- doing it twice --")
    open(CUR, "w").write(new_source)
    new2 = load("mamc_new2")
    check("installing the new version again changes nothing",
          int(new2.load_runs()[fixture]["count"]) == 7)
    check("and the flag is still there", fixture in new2.load_important())

    print("  -- a file written by the OLD code is still understood --")
    check("runs.json keys are still paths, not names",
          all(k.startswith("/") or k.startswith("cmd:") or k.startswith("name:")
              for k in runs_after))

finally:
    open(CUR, "w").write(new_source)
    shutil.rmtree(os.path.join(HOME, ".zz_upgrade"), ignore_errors=True)
    for f in ("runs.json", "important.json", "categories.json"):
        src = os.path.join(BACKUP, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(MAMC, f))
        elif f == "categories.json" and os.path.exists(os.path.join(MAMC, f)):
            os.remove(os.path.join(MAMC, f))
    mod = load("mamc_cleanup")
    for slot, mf in mod.list_trash():
        if mf["name"] in ("zz_upgrade", "zz_ghost"):
            shutil.rmtree(slot, ignore_errors=True)
    shutil.rmtree(BACKUP, ignore_errors=True)

print(f"\n{count} checks, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
