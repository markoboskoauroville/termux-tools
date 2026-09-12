#!/usr/bin/env python3
"""TEST 1 - the mechanism, alone.

mamc's pure logic, outside the running UI, on inputs chosen by hand. Every case
here exists to close one answer to: what could be true that would make this
pass and the feature still be broken?

The claims under test:
  * a candidate path is an app only if it is a real app name       (discovery)
  * the primary command is the one that STARTS the app             (App.cmd)
  * the important flag survives a rename                           (App.ident)
  * a relative time is described in the right unit                 (ago)
  * uninstall names every piece and only existing pieces           (targets)
"""
import importlib.util
import os
import sys
import tempfile
import time

spec = importlib.util.spec_from_file_location("mamc", "/root/.mamc/mamc.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []
count = 0


def check(label, got, want):
    global count
    count += 1
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")


print("APPNAME_RE - the case it is for, and the cases it must refuse")
for name in (".mamc", "tpedit", "MAHA_TRANSCRIBE_TERMUX_TERMINAL", ".ma-spotify",
             "a1", ".x2"):
    check(f"accepts {name}", bool(m.APPNAME_RE.match(name)), True)
for name in ("msys|cygwin|MSWin32", "ls.txt\\", "proverc>", "", ".", "..",
             "a", "-leading-dash", "has space", "x" * 45):
    check(f"refuses {name!r}", bool(m.APPNAME_RE.match(name)), False)

print("NOT_APP_EXT - a file that merely sits in HOME is not an app")
for name in ("server.log", "notes.txt", "state.json", "run.sh"):
    check(f"{name} excluded", os.path.splitext(name)[1] in m.NOT_APP_EXT, True)
check("dotdir not excluded", os.path.splitext(".mamc")[1] in m.NOT_APP_EXT, False)

print("App.cmd - picks the starter, not the updater")
a = m.App("MA Reader", "/root/.maread-web")
a.cmds = [("maread-update", "/bin"), ("mareadweb", "/bin")]
check("prefers starter over -update", a.cmd, "mareadweb")
a2 = m.App("x", "/root/.x")
a2.cmds = [("matools", "/bin"), ("day.commute", "/bin")]
a2.desc = {"cmd": "commute", "_id": "20-commute"}
check("no descriptor match -> kin by name", a2.cmd, "day.commute")
a3 = m.App("y", "/root/.y")
a3.cmds = [("zzz", "/bin"), ("yyy", "/bin")]
a3.desc = {"cmd": "yyy", "_id": "i"}
check("descriptor cmd wins", a3.cmd, "yyy")
a4 = m.App("z", "/root/.z")
check("no commands at all", a4.cmd, None)

print("App.ident - the flag key must survive a rename")
b = m.App("Old Name", "/root/.thing")
before = b.ident
b.name = "Totally New Name"
check("rename keeps ident", b.ident, before)
check("ident is the path", b.ident, "/root/.thing")
c = m.App("binonly", None)
c.cmds = [("thecmd", "/bin")]
check("no path -> keyed on command", c.ident, "cmd:thecmd")
d = m.App("nothing", None)
check("no path, no cmd -> keyed on name", d.ident, "name:nothing")

print("ago - the unit, at and across every boundary")
now = time.time()
check("0s", m.ago(now), "just now")
check("89s", m.ago(now - 89), "just now")
check("91s", m.ago(now - 91), "1m ago")
check("59m", m.ago(now - 3540), "59m ago")
check("60m", m.ago(now - 3600), "1h ago")
check("23h", m.ago(now - 82800), "23h ago")
check("24h", m.ago(now - 86400), "1d ago")
check("13d (was once 322h)", m.ago(now - 86400 * 13), "13d ago")
check("59d", m.ago(now - 86400 * 59), "59d ago")
check("never", m.ago(0), "never")
check("None", m.ago(None), "never")

print("targets - names every piece, and only pieces that exist")
# Under HOME, not /tmp: owns() refuses to claim anything outside HOME or the
# bin dirs, so a fixture in /tmp proves nothing about the real case.
tmp = tempfile.mkdtemp(dir=m.HOME, prefix=".zz_t1_")
appdir = os.path.join(tmp, ".fake")
os.makedirs(appdir)
launcher = os.path.join(tmp, "fakecmd")
open(launcher, "w").close()
e = m.App("Fake", appdir)
e.cmds = [("fakecmd", tmp)]
e.desc = {"_file": os.path.join(tmp, "99-fake.app"), "_id": "99-fake",
          "paths": "", "cache": ""}
t = e.targets()
check("includes the app dir", appdir in t, True)
check("includes the launcher", launcher in t, True)
check("omits the descriptor that does not exist",
      os.path.join(tmp, "99-fake.app") in t, False)
check("no duplicates", len(t), len(set(t)))
check("refuses a claim outside HOME", e.owns("/etc"), False)
check("refuses HOME itself", e.owns(m.HOME), False)
check("refuses a bin dir itself", e.owns(m.BIN_DIRS[0]), False)
check("allows a launcher inside a bin dir",
      e.owns(os.path.join(m.BIN_DIRS[0], "anything")), True)
import shutil as _sh; _sh.rmtree(tmp, ignore_errors=True)

print("speakable - the talker's cleaner, same discipline")
sys.path.insert(0, "/root/.talker")
import talker as tk
check("dotfile keeps its space", "deleted bicycle-finder" in
      tk.speakable("I deleted `/root/.bicycle-finder` now"), True)
check("code fence not read out", "rm -rf" in tk.speakable("a\n```\nrm -rf /\n```\nb"), False)
check("empty in, empty out", tk.speakable(""), "")
check("whitespace only", tk.speakable("   \n\n  "), "")
check("a question mark survives", tk.speakable("Is it on?").endswith("?"), True)

print(f"\n{count} assertions, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
