#!/usr/bin/env python3
"""TEST 3 - the ugly cases.

Down the generator list: EMPTY, ENORMOUS, MALFORMED, HOSTILE, TWICE,
OUT OF ORDER, ABSENT, NEVER ANSWERS.

Sabotage is deliberate. Anything broken on purpose here is repaired at the end
and is NOT counted as a fault.
"""
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location("mamc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mamc", "mamc.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HOME = m.HOME

fails, count = [], 0


def check(label, ok, detail=""):
    global count
    count += 1
    if ok:
        print(f"  ok    {label}")
    else:
        fails.append(f"{label} {detail}")
        print(f"  FAIL  {label} {detail}")


print("EMPTY")
d = tempfile.mkdtemp()
empty = os.path.join(HOME, ".zz_empty_app")
os.makedirs(empty, exist_ok=True)
try:
    apps = m.discover()
    check("an empty dir is not called an app",
          not any(a.path == empty for a in apps))
finally:
    os.rmdir(empty)

a = m.App("nothing", None)
check("targets() of an app that owns nothing is empty", a.targets() == [])
r = m.Runner()
check("running an app with no command and no entry is refused",
      r.start(a) is not None, f"-> {r.start(a)!r}")

print("MALFORMED")
bad = os.path.join(m.MA_APPS, "98-broken.app")
open(bad, "w").write("this is not key=value\n\x00\x01garbage\nname=\n")
try:
    apps = m.discover()
    check("a corrupt ma descriptor does not crash discovery", True)
except Exception as e:
    check("a corrupt ma descriptor does not crash discovery", False, str(e))
finally:
    os.remove(bad)

for junk in ('{"count": "not a number"}', "not json at all", "", "[]", "null"):
    open(m.RUNS_FILE + ".bak", "w").close()
    orig = open(m.RUNS_FILE).read() if os.path.exists(m.RUNS_FILE) else None
    open(m.RUNS_FILE, "w").write(junk)
    try:
        m.load_runs()
        m.discover()
        ok = True
    except Exception as e:
        ok = False
    finally:
        if orig is not None:
            open(m.RUNS_FILE, "w").write(orig)
        else:
            os.remove(m.RUNS_FILE)
        os.remove(m.RUNS_FILE + ".bak")
    check(f"corrupt runs.json survived: {junk[:24]!r}", ok)

print("HOSTILE")
evil = m.App("../../etc/passwd", None)
check("a traversal name does not escape when used as a trash slot name",
      "/" not in evil.name.replace("/", "_"))
open(os.path.join(m.MA_APPS, "97-evil.app"), "w").write(
    "name=Evil\ncmd=../../../bin/sh\npaths=/etc /usr\n")
try:
    apps = m.discover()
    ev = next((x for x in apps if x.name == "Evil"), None)
    t = ev.targets() if ev else []
    check("a descriptor claiming /etc and /usr is not silently obeyed",
          "/etc" not in t and "/usr" not in t,
          f"-> {t}")
finally:
    os.remove(os.path.join(m.MA_APPS, "97-evil.app"))

print("TWICE")
runs_before = m.load_runs()
fixture = os.path.join(HOME, ".zz_twice")
os.makedirs(fixture, exist_ok=True)
open(os.path.join(fixture, "app.py"), "w").write(
    "from flask import Flask\nimport time\nPORT=9097\ntime.sleep(30)\n")
app = next((x for x in m.discover() if x.path == fixture), None)
check("the fixture app was discovered", app is not None)
if app:
    r = m.Runner()
    e1 = r.start(app)
    e2 = r.start(app)
    check("a second start while running is refused", e2 is not None, f"-> {e2!r}")
    r.stop()
    r.stop()
    check("stopping twice does not raise", True)
    check("stop is idempotent - still not alive", not r.alive)

print("OUT OF ORDER")
r2 = m.Runner()
try:
    r2.stop()
    check("stop before any start does not raise", True)
except Exception as e:
    check("stop before any start does not raise", False, str(e))

print("ABSENT")
if app:
    slot, moved, failed = m.trash_app(app)
    check("uninstall reports what it moved", len(moved) >= 1)
    gone = not os.path.exists(fixture)
    check("the app dir is gone after uninstall", gone)
    slots = m.list_trash()
    mine = [(s, mf) for s, mf in slots if mf["name"] == "zz_twice"]
    check("it is in the trash with a manifest", len(mine) == 1)
    if mine:
        ok_r, bad_r = m.restore_slot(*mine[0])
        check("restore puts it back", os.path.isdir(fixture), f"bad={bad_r}")
        check("restore reports no failures", not bad_r, str(bad_r))

    # restore when the destination already exists: must refuse, not overwrite
    slot2, moved2, _ = m.trash_app(app)
    os.makedirs(fixture, exist_ok=True)
    open(os.path.join(fixture, "SENTINEL"), "w").write("do not lose me")
    slots = [(s, mf) for s, mf in m.list_trash() if mf["name"] == "zz_twice"]
    ok_r, bad_r = m.restore_slot(*slots[0])
    check("restore refuses to overwrite what is already there",
          os.path.exists(os.path.join(fixture, "SENTINEL")), f"bad={bad_r}")
    shutil.rmtree(fixture, ignore_errors=True)
    for s, mf in m.list_trash():
        if mf["name"] == "zz_twice":
            shutil.rmtree(s, ignore_errors=True)

print("NEVER ANSWERS")
# an app that accepts nothing, prints nothing, and never exits
hang = os.path.join(HOME, ".zz_hang")
os.makedirs(hang, exist_ok=True)
open(os.path.join(hang, "app.py"), "w").write(
    "import signal, time, http.server\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "PORT=9096\n"
    "while True: time.sleep(60)\n")
happ = next((x for x in m.discover() if x.path == hang), None)
if happ:
    r3 = m.Runner()
    r3.start(happ)
    time.sleep(2)
    check("a silent app is still seen as alive", r3.alive)
    pgid = os.getpgid(r3.proc.pid)
    t0 = time.time()
    r3.stop()
    secs = time.time() - t0
    check("an app that IGNORES SIGTERM is still stopped", not r3.alive,
          f"took {secs:.1f}s")
    check("it does not hang forever getting there", secs < 12, f"{secs:.1f}s")
    left = subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)],
                          capture_output=True, text=True).stdout.strip()
    check("nothing left in its process group", left == "", f"-> {left!r}")
shutil.rmtree(hang, ignore_errors=True)

print("FOLDERS - a corrupt, hostile or stale categories.json")
_saved_cf = m.CATEGORIES_FILE
m.CATEGORIES_FILE = os.path.join(HOME, ".zz_t3_categories.json")
try:
    for junk in ("{ broken", "[]", '{"/root/.tpedit": ""}', '{"/root/.tpedit": null}',
                 '{"/root/.tpedit": "' + "z" * 500 + '"}',
                 '{"/root/.gone_forever": "Ghost"}', '{"": "x"}'):
        open(m.CATEGORIES_FILE, "w").write(junk)
        try:
            apps = m.discover()
            fs = m.group_folders(apps)
            ok = all(a.folder for a in apps) and all(len(f.name) <= m.FOLDER_NAME_MAX for f in fs) \
                and not any(f.name == "Ghost" for f in fs) and sum(len(f.apps) for f in fs) == len(apps)
            check(f"categories.json {junk[:26]!r}: every app filed, no ghost folder", ok)
        except Exception as e:
            check(f"categories.json {junk[:26]!r} does not crash discovery", False, str(e))
finally:
    try:
        os.remove(m.CATEGORIES_FILE)
    except OSError:
        pass
    m.CATEGORIES_FILE = _saved_cf

print("SABOTAGE - the flag file is destroyed mid-flight")
orig_imp = open(m.IMPORTANT_FILE).read() if os.path.exists(m.IMPORTANT_FILE) else None
try:
    open(m.IMPORTANT_FILE, "w").write("{ broken json")
    apps = m.discover()
    check("a corrupt important.json degrades to 'nothing is flagged'",
          all(not a.important for a in apps))
finally:
    if orig_imp is not None:
        open(m.IMPORTANT_FILE, "w").write(orig_imp)


print("THE REGISTRY, MISBEHAVING")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_saved_ports = m.PORTS_DIR
_ugly = tempfile.mkdtemp(prefix="mamc-ugly-ports-")
try:
    m.PORTS_DIR = os.path.join(_ugly, "not-a-dir")
    open(m.PORTS_DIR, "w").write("8842\n")
    check("the registry folder is a FILE: no line, no crash", m.registry_port("keyring") is None)
    m.PORTS_DIR = _ugly
    open(os.path.join(_ugly, "huge"), "w").write("8" * 200000)
    check("ENORMOUS: a 200 KB number is not a port", m.registry_port("huge") is None)
    open(os.path.join(_ugly, "neg"), "w").write("-8842\n")
    check("a negative number is not a port", m.registry_port("neg") is None)
    open(os.path.join(_ugly, "empty"), "w").write("")
    check("EMPTY: an empty file is no line", m.registry_port("empty") is None)
    open(os.path.join(_ugly, "bin"), "wb").write(b"\x00\xff\xfe8842")
    check("HOSTILE: binary garbage is no line", m.registry_port("bin") is None)
    open(os.path.join(_ugly, "spaced"), "w").write("  8850  \n\n")
    check("whitespace around the number is fine", m.registry_port("spaced") == 8850)
    os.mkdir(os.path.join(_ugly, "adir"))
    check("a directory where the file should be is no line", m.registry_port("adir") is None)
    check("OUT OF ORDER: a line for a port nothing serves is ignored by live_port",
          (lambda a: (setattr(a, "cmds", [("spaced", "/x")]) or m.live_port(a)))(m.App("s", None)) is None)
finally:
    m.PORTS_DIR = _saved_ports
    shutil.rmtree(_ugly, ignore_errors=True)

print("THE UPDATE, WHEN GITHUB IS NOT THERE")
import fixture_repo
_env_before = dict(os.environ)
tmp, bare, co = fixture_repo.make(latest=1)
try:
    fixture_repo.apply_env(tmp, co)
    # ABSENT: the origin is gone
    shutil.rmtree(bare)
    try:
        m.check_update(co); check("ABSENT origin: raises", False)
    except m.UpdateError as e:
        check("ABSENT origin: a sentence, not a traceback", "could not reach" in str(e), str(e))
    check("and nothing moved", m.installed_version(co) is None)
    # NEVER ANSWERS: a git that hangs
    fake = os.path.join(tmp, "fakebin"); os.makedirs(fake)
    open(os.path.join(fake, "git"), "w").write("#!/bin/sh\nsleep 30\n")
    os.chmod(os.path.join(fake, "git"), 0o755)
    os.environ["PATH"] = fake + os.pathsep + os.environ["PATH"]
    m.GIT_TIMEOUT = 2
    t0 = time.time()
    try:
        m.check_update(co); check("NEVER ANSWERS: raises", False)
    except m.UpdateError as e:
        check("NEVER ANSWERS: gives up with a sentence", "did not answer" in str(e), str(e))
    check("and within the deadline", time.time() - t0 < 6, f"{time.time() - t0:.1f}s")
    m.GIT_TIMEOUT = 25
    os.environ["PATH"] = _env_before["PATH"]
finally:
    os.environ.clear(); os.environ.update(_env_before)
    shutil.rmtree(tmp, ignore_errors=True)

# MALFORMED: origin's version.py has no number; TWICE: diverged so ff is impossible
tmp, bare, co = fixture_repo.make(latest=1)
try:
    fixture_repo.apply_env(tmp, co)
    work = os.path.join(tmp, "work")
    open(os.path.join(work, "version.py"), "w").write("VERSION = 'two'\n")
    subprocess.run(["git", "-C", work, "commit", "-q", "-am", "C: no number"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"), timeout=30)
    subprocess.run(["git", "-C", work, "push", "-q", "origin", "main"], check=True, timeout=30)
    try:
        m.check_update(co); check("MALFORMED version.py on origin: raises", False)
    except m.UpdateError as e:
        check("MALFORMED version.py on origin: a sentence", "no readable version.py" in str(e), str(e))
    open(os.path.join(co, "local.txt"), "w").write("x\n")
    subprocess.run(["git", "-C", co, "add", "local.txt"], check=True, timeout=30)
    subprocess.run(["git", "-C", co, "commit", "-q", "-m", "local"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"), timeout=30)
    try:
        m.perform_update(co); check("TWICE: a diverged checkout cannot fast-forward: raises", False)
    except m.UpdateError as e:
        check("TWICE: a diverged checkout says pull failed", "git pull failed" in str(e), str(e))
    check("and the local commit is still there",
          os.path.exists(os.path.join(co, "local.txt")))
finally:
    os.environ.clear(); os.environ.update(_env_before)
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{count} checks, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
