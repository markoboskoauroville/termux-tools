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

spec = importlib.util.spec_from_file_location("mamc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mamc", "mamc.py"))
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

print("a checkout is source, not an app")
import subprocess as _sp
_repo = os.path.join(m.HOME, ".zz_checkout")
os.makedirs(os.path.join(_repo, ".git"), exist_ok=True)
open(os.path.join(_repo, "server.py"), "w").write("from flask import Flask\n")
_names = [x.name for x in m.discover()]
check("a git checkout with nothing installed is not listed",
      "zz_checkout" in _names, False)
# but a checkout that DID install something is still an app
_sp.run(["cp", "/data/data/com.termux/files/usr/bin/mamc",
         os.path.join(m.HOME, ".local", "bin", "zzcheckoutcmd")], check=False)
with open(os.path.join(m.HOME, ".local", "bin", "zzcheckoutcmd"), "w") as fh:
    fh.write('#!/bin/bash\nAPPDIR="$HOME/.zz_checkout"\npython "$APPDIR/server.py"\n')
os.chmod(os.path.join(m.HOME, ".local", "bin", "zzcheckoutcmd"), 0o755)
_names = [x.name for x in m.discover()]
check("a checkout WITH an installed launcher is still an app",
      "zz_checkout" in _names, True)
os.remove(os.path.join(m.HOME, ".local", "bin", "zzcheckoutcmd"))
_sh2 = __import__("shutil"); _sh2.rmtree(_repo, ignore_errors=True)

print("speakable - the talker's cleaner, same discipline")
sys.path.insert(0, "/root/.talker")
import talker as tk
check("dotfile keeps its space", "deleted bicycle-finder" in
      tk.speakable("I deleted `/root/.bicycle-finder` now"), True)
check("code fence not read out", "rm -rf" in tk.speakable("a\n```\nrm -rf /\n```\nb"), False)
check("empty in, empty out", tk.speakable(""), "")
check("whitespace only", tk.speakable("   \n\n  "), "")
check("a question mark survives", tk.speakable("Is it on?").endswith("?"), True)
check("hush alone is the pause word", bool(tk.HUSH_RE.match("hush")), True)
check("hush with a full stop", bool(tk.HUSH_RE.match(" Hush. ")), True)
check("hush inside a sentence is left alone", bool(tk.HUSH_RE.match("why did you hush")), False)
import json

print("play_file - a Stop is a finished playback, not a failure to fall back from")
# mpv exits 4 when told to quit over its socket. A fake mpv on PATH that
# does exactly that must be read as "stopped", and must NOT hand the clip to
# the Termux player -- that was the bug where Stop made her start over.
import subprocess as _sp2
_fake = tempfile.mkdtemp(dir=m.HOME, prefix=".zz_fakebin_")
for _name, _rc in (("mpv", 4), ("termux-media-player", 0)):
    with open(os.path.join(_fake, _name), "w") as fh:
        fh.write("#!/bin/sh\n" + ("echo Now Playing\n" if _name != "mpv" else "") + f"exit {_rc}\n")
    os.chmod(os.path.join(_fake, _name), 0o755)
_oldpath = os.environ["PATH"]
os.environ["PATH"] = _fake + ":" + _oldpath
_clip = os.path.join(_fake, "clip.mp3"); open(_clip, "wb").write(b"x")
try:
    if os.path.exists(tk.BG_FLAG): os.remove(tk.BG_FLAG)
    _ok, _may_delete = tk.play_file(_clip, "t")
    check("mpv exit 4 is reported as a completed playback", (_ok, _may_delete), (True, True))
    check("and the background player was NOT given the clip", os.path.exists(tk.BG_FLAG), False)
    with open(os.path.join(_fake, "mpv"), "w") as fh:
        fh.write("#!/bin/sh\nexit 2\n")
    _ok, _may_delete = tk.play_file(_clip, "t")
    check("a real mpv failure (exit 2) still falls back", (_ok, _may_delete), (True, False))
    check("and the fallback is remembered so it can be stopped", os.path.exists(tk.BG_FLAG), True)
finally:
    os.environ["PATH"] = _oldpath
    if os.path.exists(tk.BG_FLAG): os.remove(tk.BG_FLAG)
    _sh.rmtree(_fake, ignore_errors=True)

print("ccnotify - the pieces that do not need a phone")
sys.path.insert(0, "/root/.ccnotify")
import ccnotify as cn
check("both spellings of a /root path", cn.spellings("/root/.claude/inbox/x.sock"),
      ["/root/.claude/inbox/x.sock", "/data/data/com.termux/files/home/.claude/inbox/x.sock"])
check("both spellings of a Termux path", cn.spellings("/data/data/com.termux/files/home/a")[1], "/root/a")
check("a path outside home has one spelling", cn.spellings("/tmp/x"), ["/tmp/x"])
check("headline flattens and trims", cn.headline("**Done.**\n\nThe `x` file " + "y" * 200).endswith("…"), True)
check("headline strips markdown", cn.headline("**Done.** ok"), "Done. ok")
_tp = os.path.join(m.HOME, ".zz_t1_transcript.jsonl")
with open(_tp, "w") as fh:
    fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "first"}]}}) + "\n")
    fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "rm -rf /"}}]}}) + "\n")
check("last text block, skipping a tool-only turn", cn.last_assistant_text(_tp), "first")
check("tool input is never read as a headline", "rm -rf" in cn.last_assistant_text(_tp), False)
os.remove(_tp)
check("push with nothing to send is refused", cn.push("")[0], False)
_saved = cn.INBOX
cn.INBOX = os.path.join(m.HOME, ".zz_no_inbox.json")
check("push with no announced session is refused, not hung", cn.push("x")[0], False)
cn.INBOX = _saved
# reaping: a Termux:API call left waiting for an app that will never answer
check("a termux-notification call is a Termux:API call",
      cn.is_termux_api_call(["/data/data/com.termux/files/usr/bin/bash",
                             "/data/data/com.termux/files/usr/bin/termux-notification", "--id", "x"]), True)
check("the libexec helper is one too", cn.is_termux_api_call(["/x/libexec/termux-api", "Notification"]), True)
check("wrapped in timeout it still is", cn.is_termux_api_call(["timeout", "-k", "5", "45", "termux-toast", "hi"]), True)
check("the wake lock is NOT reaped", cn.is_termux_api_call(["termux-wake-lock"]), False)
check("a shell is not", cn.is_termux_api_call(["bash", "-l"]), False)
check("a bounded call carries its deadline", cn.bounded(["termux-toast", "x"])[:1], ["timeout"])
_rows = [(1, 500.0, ["termux-api", "Notification"]),      # old and hung
         (2, 5.0, ["termux-api", "Notification"]),        # young: still working
         (3, 500.0, ["python3", "server.py"])]            # old, not ours
_killed = []
cn.os.kill, _real_kill = (lambda pid, sig: _killed.append(pid)), cn.os.kill
check("reap takes the old Termux:API call and nothing else",
      cn.reap(stale=120, procs=_rows), [1])
cn.os.kill = _real_kill
# and for real: an orphan called termux-api that will never finish
_sp2.run(["bash", "-c", "(exec -a termux-api python3 -c 'import time; time.sleep(300)' &)"], timeout=10)
time.sleep(0.5)
_mine = [pid for pid, age, argv in cn.orphans() if argv[:1] == ["termux-api"] and "-c" in argv]
check("the orphan is seen with its parent as pid 1", len(_mine) >= 1, True)
check("younger than the limit, it is left alone", [q for q in cn.reap(stale=60) if q in _mine], [])
_got = cn.reap(stale=0)
check("older than the limit, it is reaped", all(p in _got for p in _mine), True)
time.sleep(0.5)
check("and it is gone", any(pid in [q for q, _, _ in cn.orphans()] for pid in _mine), False)

print("folders - the rule, its order, the override, and the name")
def _fake(name, cmd=None, path="/root/.x", loose=False, orphan=False):
    a = m.App(name, path)
    a.cmds = [(cmd, "/bin")] if cmd else []
    a.loose, a.orphan = loose, orphan
    return a
check("commute by command", m.default_folder(_fake("nightcommute", "night.commute", "/root/.nightcommute")), "Commute")
check("commute wins when a later rule also matches",
      m.default_folder(_fake("commute-tts", "commute-tts")), "Commute")
check("transcribe by command", m.default_folder(_fake("MAHA_TRANSCRIBE_TERMUX_TERMINAL", "transcribe")), "Transcribe & speech")
check("tts before reader: first rule wins", m.default_folder(_fake("tts_reader", "tts", "/root/.tts_reader")), "Transcribe & speech")
check("reader by folder name", m.default_folder(_fake("MA Reader", "mareadweb", "/root/.maread-web")), "Read & play")
check("player with no folder", m.default_folder(_fake("MA Player", "maplayerweb", None)), "Read & play")
check("an editor is a tool", m.default_folder(_fake("tpedit", "tpedit", "/root/.tpedit")), "Tools")
check("shops & pools is a tool", m.default_folder(_fake("Shops & Pools", "mafinder", "/root/.mafinder")), "Tools")
check("a loose .py is an experiment", m.default_folder(_fake("test_server.py", None, "/root/test_server.py", loose=True)), m.LOOSE_FOLDER)
check("nothing matches -> Other", m.default_folder(_fake("zzz", "zzz")), m.OTHER_FOLDER)
check("an orphan is always Orphans, whatever its name", m.default_folder(_fake("nightcommute", None, None, orphan=True)), m.ORPHAN_FOLDER)
_a = _fake("tpedit", "tpedit", "/root/.tpedit")
check("an override wins over the rule", m.folder_of(_a, {"/root/.tpedit": "Mine"}), "Mine")
check("an override for another app is ignored", m.folder_of(_a, {"/root/.other": "Mine"}), "Tools")
check("an empty override table falls back to the rule", m.folder_of(_a, {}), "Tools")
check("clean: whitespace collapsed", m.clean_folder_name("  a \n\t b  "), "a b")
check("clean: bounded", len(m.clean_folder_name("x" * 200)), m.FOLDER_NAME_MAX)
check("clean: not a string", m.clean_folder_name(None), "")
check("clean: empty", m.clean_folder_name("   "), "")
_x, _y, _z = _fake("a", "a", "/root/.a"), _fake("b", "b", "/root/.b"), _fake("c", None, None, orphan=True)
_x.runs, _y.runs, _z.runs = 2, 9, 50
_x.folder, _y.folder, _z.folder = "Tools", "Commute", m.ORPHAN_FOLDER
_fs = m.group_folders([_x, _y, _z])
check("most-run folder first", [f.name for f in _fs][:2], ["Commute", "Tools"])
check("orphans last even with more runs", _fs[-1].name, m.ORPHAN_FOLDER)
check("a folder's runs are its apps' runs added up", _fs[0].runs, 9)
check("counts add up to the apps", sum(len(f.apps) for f in _fs), 3)
_saved = m.CATEGORIES_FILE
m.CATEGORIES_FILE = os.path.join(m.HOME, ".zz_t1_categories.json")
for junk, want in (('{"/root/.a": "Mine"}', {"/root/.a": "Mine"}),
                   ('{"/root/.a": ""}', {}), ('{"/root/.a": 5}', {}),
                   ('[]', {}), ('not json', {}), ('', {}),
                   ('{"/root/.a": "' + "y" * 100 + '"}', {"/root/.a": "y" * 32})):
    open(m.CATEGORIES_FILE, "w").write(junk)
    check(f"load_categories on {junk[:22]!r}", m.load_categories(), want)
os.remove(m.CATEGORIES_FILE)
m.CATEGORIES_FILE = _saved


print("the live port - ~/.mantra/ports/<command>, trusted only when it answers")
import socket as _socket
import shutil as _shutil
_saved_dir = m.PORTS_DIR
_reg = tempfile.mkdtemp(prefix="mamc-ports-")
m.PORTS_DIR = _reg
try:
    check("no file -> None", m.registry_port("nothing"), None)
    open(os.path.join(_reg, "good"), "w").write("8850\n")
    check("a number is read", m.registry_port("good"), 8850)
    open(os.path.join(_reg, "zero"), "w").write("0\n")
    check("0 is not a port", m.registry_port("zero"), None)
    open(os.path.join(_reg, "big"), "w").write("70000\n")
    check("70000 is not a port", m.registry_port("big"), None)
    open(os.path.join(_reg, "words"), "w").write("eight thousand\n")
    check("words are not a port", m.registry_port("words"), None)
    check("a command with a slash is refused", m.registry_port("../good"), None)
    check("an empty command is refused", m.registry_port(""), None)
    check("None is refused", m.registry_port(None), None)
    srv = _socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    lp = srv.getsockname()[1]
    open(os.path.join(_reg, "live"), "w").write(f"{lp}\n")
    a = m.App("live", None); a.cmds = [("live", "/x")]
    check("a line whose port answers is the live port", m.live_port(a), lp)
    a2 = m.App("also", None); a2.cmds = [("also-update", "/x"), ("live", "/x")]
    check("every command name of the app is tried", m.live_port(a2), lp)
    srv.close()
    time.sleep(0.2)
    check("the same line once nothing answers is ignored", m.live_port(a), None)
    a3 = m.App("none", None)
    check("an app with no command has no live port", m.live_port(a3), None)
finally:
    m.PORTS_DIR = _saved_dir
    _shutil.rmtree(_reg, ignore_errors=True)

print("the version - one whole number, read by pattern, here and off origin")
check("APP_VERSION = 12", m.read_version("APP_VERSION = 12\n"), 12)
check("with a comment after it", m.read_version('"""doc"""\n\nAPP_VERSION = 7  # x\n'), 7)
check("a dotted number is not one", m.read_version("APP_VERSION = 1.2\n"), None)
check("no line -> None", m.read_version("VERSION = 3\n"), None)
check("None -> None", m.read_version(None), None)
_vd = tempfile.mkdtemp(prefix="mamc-ver-")
check("a folder without version.py is unnumbered", m.installed_version(_vd), None)
open(os.path.join(_vd, "version.py"), "w").write("APP_VERSION = 3\n")
check("and numbered once it has one", m.installed_version(_vd), 3)
check("a folder is not a checkout without .git and mamc/mamc.py", m.is_checkout(_vd), False)
_shutil.rmtree(_vd, ignore_errors=True)
check("this checkout has a version", isinstance(m.installed_version(), int), True)

print("the update - against a GitHub on disk, one commit ahead")
import fixture_repo
tmp, bare, co = fixture_repo.make(latest=1)
_env_before = dict(os.environ)
try:
    fixture_repo.apply_env(tmp, co)
    check("the fixture checkout is a checkout", m.is_checkout(co), True)
    check("it is unnumbered", m.installed_version(co), None)
    info = m.check_update(co)
    check("check: installed unnumbered", info["installed"], None)
    check("check: latest is 1", info["latest"], 1)
    check("check: one commit behind", info["behind"], 1)
    check("check: not up to date", info["up_to_date"], False)
    check("checking moved nothing", m.installed_version(co), None)
    msg = m.perform_update(co)
    check("update says the new number", "v1" in msg, True)
    check("after the pull the checkout is v1", m.installed_version(co), 1)
    info = m.check_update(co)
    check("and a second check is up to date", info["up_to_date"], True)
    check("the link step wrote the fixture's wrapper, not the real one",
          os.path.exists(os.path.join(tmp, "bin", "mamc")), True)
    check("the fixture wrapper runs the fixture checkout",
          co in open(os.path.join(tmp, "bin", "mamc")).read(), True)
    real = "/data/data/com.termux/files/usr/bin/mamc"
    check("the real wrapper still runs the real checkout",
          "/root/termux-tools/mamc/mamc.py" in open(real).read(), True)
    try:
        m.check_update(tmp)
        check("a non-checkout raises a sentence", False, True)
    except m.UpdateError as e:
        check("a non-checkout raises a sentence", "not a git checkout" in str(e), True)
finally:
    os.environ.clear(); os.environ.update(_env_before)
    _shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{count} assertions, {len(fails)} failures")
for f in fails:
    print("  ", f)
sys.exit(1 if fails else 0)
