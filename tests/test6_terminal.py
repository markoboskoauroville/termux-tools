#!/usr/bin/env python3
"""Apps that need a terminal, which on this phone is nearly all of them.

Fixture one exits the instant it finds no tty -- the way ahkedit, mawhisper
and maha.commute behave. It must stay alive under the Runner.
Fixture two is a curses program -- the way tts_reader behaves. It must be
detected as tui, and mamc must hand it the terminal and get it back.
"""
import importlib.util, os, shutil, subprocess, sys, time
import pty, fcntl, termios, struct, select, re
spec = importlib.util.spec_from_file_location("mamc", "/root/.mamc/mamc.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
HOME = m.HOME; fails = []
def check(label, ok, d=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  {d}" if d and not ok else ""))
    if not ok: fails.append(label)

print("an app that quits without a terminal")
d1 = os.path.join(HOME, ".zz_needs_tty"); os.makedirs(d1, exist_ok=True)
open(os.path.join(d1, "app.py"), "w").write(
    "import sys, time, http.server\nPORT=9093\n"
    "if not sys.stdin.isatty():\n    print('no tty, stopping'); sys.exit(0)\n"
    "print('serving on http://127.0.0.1:9093  [Q] quit', flush=True)\n"
    "while True: time.sleep(1)\n")
a = next(x for x in m.discover() if x.path == d1)
check("not mistaken for a full-screen app", not a.tui)
r = m.Runner(); err = r.start(a); time.sleep(2.5)
check("started", err is None, str(err))
check("STAYS ALIVE now that it has a terminal", r.alive,
      f"rc={r.proc.returncode} last={list(r.lines)[-1:]}" if not r.alive else "")
check("its output reaches the run screen", any("serving" in l for l in r.lines))
check("the url was picked out of it", r.url == "http://127.0.0.1:9093", str(r.url))
check("no escape codes leak into the lines", not any("\x1b" in l for l in r.lines))
r.stop(); time.sleep(0.5)
check("Q's path still kills it", not r.alive)
shutil.rmtree(d1, ignore_errors=True)

print("a launcher that daemonises its server and exits")
d3 = os.path.join(HOME, ".zz_daemon"); os.makedirs(d3, exist_ok=True)
open(os.path.join(d3, "srv.py"), "w").write(
    "import time, http.server\nPORT=9091\nwhile True: time.sleep(1)\n")
open(os.path.join(d3, "app.py"), "w").write(
    "import subprocess, sys, os, http.server\nPORT=9091\n"
    "subprocess.Popen(['nohup', sys.executable, 'srv.py'],\n"
    "    cwd=os.path.dirname(os.path.abspath(__file__)),\n"
    "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "print('daemon started, launcher exiting'); sys.exit(0)\n")
a3 = next(x for x in m.discover() if x.path == d3)
r3 = m.Runner(); r3.start(a3); time.sleep(2.5)
check("the launcher itself has exited", not r3.alive)
check("but mamc knows its server is still serving", r3.serving)
r3.stop(); time.sleep(0.6)
left = subprocess.run(["pgrep", "-f", "zz_daemon/srv.py"], capture_output=True, text=True).stdout.strip()
check("Q's path stops the daemon the launcher left behind", left == "", f"still running: {left}")
check("nothing left in the group", not r3.group_members())
shutil.rmtree(d3, ignore_errors=True)

print("a curses app is detected, handed the terminal, and comes back")
d2 = os.path.join(HOME, ".zz_fullscreen"); os.makedirs(d2, exist_ok=True)
open(os.path.join(d2, "app.py"), "w").write(
    "import curses, http.server\nPORT=9092\n"
    "def run(scr):\n    scr.addstr(2, 2, 'FULLSCREEN FIXTURE  press q'); scr.refresh()\n"
    "    while scr.getch() != ord('q'): pass\n"
    "curses.wrapper(run)\n")
a2 = next(x for x in m.discover() if x.path == d2)
check("detected as tui", a2.tui)

mst, slv = pty.openpty()
fcntl.ioctl(slv, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
p = subprocess.Popen(["bash", "/data/data/com.termux/files/usr/bin/mamc"],
                     stdin=slv, stdout=slv, stderr=slv, close_fds=True,
                     env=dict(os.environ, TERM="xterm-256color"))
os.close(slv); buf = b""
def pump(t):
    global buf
    end = time.time() + t
    while time.time() < end:
        rr, _, _ = select.select([mst], [], [], 0.15)
        if rr:
            try: buf += os.read(mst, 200000)
            except OSError: break
def expect(marker, t=15):
    end = time.time() + t
    while time.time() < end:
        if marker.encode() in buf: return True
        pump(0.3)
    return False
expect("most-run first", 25); pump(0.8)
os.write(mst, b"/"); time.sleep(0.3); os.write(mst, b"zz_fullscreen\n"); pump(1.0)
os.write(mst, b"\n")
check("mamc announces the handover", expect("handing you the terminal"))
check("the curses app really drew on the terminal", expect("FULLSCREEN FIXTURE"))
os.write(mst, b"q")
check("quitting it returns to the list", expect("most-run first", 15))
check("mamc reports the exit", expect("exited (code 0)", 10))
os.write(mst, b"q"); time.sleep(1.5)
check("mamc itself still quits cleanly", p.poll() == 0, f"rc={p.poll()}")
if p.poll() is None: p.terminate()
try: os.close(mst)
except OSError: pass
shutil.rmtree(d2, ignore_errors=True)
runs = m.load_runs(); runs.pop(d1, None); runs.pop(d2, None); runs.pop(d3, None); m.save_runs(runs)
print(f"\n{len(fails)} failures"); sys.exit(1 if fails else 0)
