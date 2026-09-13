#!/usr/bin/env python3
"""The gate sweep, over every app mamc lists.

Not the four tests of each app -- that is a session per app, and it says so
in the record. This is the part of the nine gates a machine can run over
twenty programs in a few minutes, with a COUNT printed for every check so a
zero can be told from a check that ran nothing:

  G2  SECRETS     key-shaped strings in the app's own files
  G3  ANALYSIS    every .py compiles; pyflakes undefined names (real bugs)
                  and its other messages (read, not necessarily fixed);
                  every shell launcher parses under bash -n
  G5  DEAD LOOPS  calls that cross a process boundary with no deadline:
                  requests.* without timeout=, urlopen without timeout,
                  subprocess.run/check_output/call without timeout
  T2  RUNS        started through mamc's own Runner, the way Enter starts
                  it; alive OR serving after a settle; stopped by Q's path;
                  nothing left in its process group

    python3 tests/sweep_apps.py            everything
    python3 tests/sweep_apps.py --static   G2, G3, G5 only (seconds)
    python3 tests/sweep_apps.py --run      T2 only
"""
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("mamc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mamc", "mamc.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SECRET_RE = re.compile(rb"(gsk_|sk-ant-|sk_|ghp_|github_pat_|AIza|xox[baprs]-)[A-Za-z0-9_-]{20,}")
TEXT_EXT = {".py", ".sh", ".txt", ".json", ".md", ".html", ".js", ".cfg", ".conf",
            ".env", ".ini", ".yaml", ".yml", ".toml", ""}
# The call's opening paren; the arguments are then walked to the MATCHING
# close, counting depth. The first version used [^)]* and stopped at the
# first ")" inside str(x) or Request(u) -- and reported sixteen calls with
# no deadline of which twelve had one. A check that cries wolf is deleted or
# fixed in the session it cried, and this is the fix.
BOUNDARY_CALL = re.compile(
    r"\b(requests\.(?:get|post|put|delete|head|patch|request)"
    r"|urlopen|subprocess\.(?:run|check_output|check_call|call))\s*\(")


def call_args(src, start):
    """The text between the paren at `start` and its match, or None."""
    depth, i, n = 0, start, len(src)
    quote = None
    while i < n:
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[start + 1:i]
        i += 1
    return None
SETTLE = 6.0


def py_files(app):
    out = []
    if not app.path:
        return out
    if os.path.isfile(app.path):
        return [app.path] if app.path.endswith(".py") else []
    for root, dirs, files in os.walk(app.path):
        dirs[:] = [d for d in dirs if d not in m.SKIP_WALK and d != "backup"]
        if root.count(os.sep) - app.path.count(os.sep) > 2:
            dirs[:] = []
        out += [os.path.join(root, f) for f in files if f.endswith(".py")]
    return out


def text_files(app):
    out = []
    if not app.path:
        return out
    if os.path.isfile(app.path):
        return [app.path]
    for root, dirs, files in os.walk(app.path):
        dirs[:] = [d for d in dirs if d not in m.SKIP_WALK]
        for f in files:
            if os.path.splitext(f)[1].lower() in TEXT_EXT:
                p = os.path.join(root, f)
                try:
                    if os.path.getsize(p) < 2_000_000:
                        out.append(p)
                except OSError:
                    pass
    return out


def g2_secrets(app):
    files = text_files(app)
    hits = []
    for p in files:
        try:
            blob = open(p, "rb").read()
        except OSError:
            continue
        for mo in SECRET_RE.finditer(blob):
            hits.append((os.path.relpath(p, app.path) if os.path.isdir(app.path or "") else os.path.basename(p),
                         mo.group(0)[:6].decode() + "…"))
    return len(files), hits


def g3_analysis(app):
    files = py_files(app)
    compile_bad, undefined, other = [], [], 0
    for p in files:
        r = subprocess.run([sys.executable, "-m", "py_compile", p],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            compile_bad.append((os.path.basename(p), (r.stderr.strip().splitlines() or ["?"])[-1][:100]))
    if files:
        r = subprocess.run([sys.executable, "-m", "pyflakes"] + files,
                           capture_output=True, text=True, timeout=120)
        for line in r.stdout.splitlines():
            if "undefined name" in line:
                undefined.append(line.replace(app.path or "", "").strip()[:140])
            else:
                other += 1
    sh_bad = []
    for c, d in app.cmds:
        p = os.path.join(d, c)
        try:
            head = open(p, "rb").read(64)
        except OSError:
            continue
        if b"bash" in head or b"/sh" in head:
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                sh_bad.append((c, r.stderr.strip()[:100]))
    return len(files), compile_bad, undefined, other, len(app.cmds), sh_bad


def g5_deadlines(app):
    files = py_files(app)
    calls, missing = 0, []
    for p in files:
        try:
            src = open(p, errors="replace").read()
        except OSError:
            continue
        for mo in BOUNDARY_CALL.finditer(src):
            args = call_args(src, mo.end() - 1)
            if args is None:
                continue
            calls += 1
            if not re.search(r"\btimeout\s*=", args):
                line = src.count("\n", 0, mo.start()) + 1
                missing.append(f"{os.path.basename(p)}:{line} {mo.group(1).split('.')[0]}")
    return calls, missing


def port_open(port):
    if not port:
        return None
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        s.close()


def t2_run(app):
    r = m.Runner()
    row = {"pre_bound": port_open(app.port)}
    err = r.start(app)
    if err:
        row.update(start=False, note=err, ok=False)
        return row
    row["start"] = True
    time.sleep(SETTLE)
    row["alive"] = r.alive
    row["serving"] = r.serving
    row["port_after"] = port_open(app.port)
    row["last_line"] = (list(r.lines)[-1][:80] if r.lines else "")
    row["rc_if_dead"] = None if r.alive else r.proc.returncode
    t0 = time.time()
    r.stop()
    row["stop_secs"] = round(time.time() - t0, 2)
    time.sleep(0.6)
    row["group_left"] = len(r.group_members())
    row["ok"] = bool(row["serving"] and not r.alive and row["group_left"] == 0)
    return row


def main():
    static = "--run" not in sys.argv
    run = "--static" not in sys.argv
    apps = [a for a in m.discover() if not a.orphan]
    print(f"sweeping {len(apps)} apps\n")
    report = []
    for a in apps:
        rec = {"name": a.name, "path": a.path, "cmd": a.cmd, "port": a.port}
        line = f"{a.name[:30]:30}"
        if static:
            nf, hits = g2_secrets(a)
            rec["g2"] = {"files": nf, "hits": hits}
            npy, cbad, undef, other, ncmd, shbad = g3_analysis(a)
            rec["g3"] = {"py": npy, "compile_bad": cbad, "undefined": undef,
                         "other": other, "launchers": ncmd, "sh_bad": shbad}
            calls, missing = g5_deadlines(a)
            rec["g5"] = {"calls": calls, "missing": missing}
            line += (f" G2 {nf:3}f {len(hits)}hit | G3 {npy:3}py {len(cbad)}bad {len(undef)}undef "
                     f"{other:3}other {ncmd}sh {len(shbad)}bad | G5 {calls:3}calls {len(missing):3}nodeadline")
        if run:
            row = t2_run(a)
            rec["t2"] = row
            line += (f" | T2 {'ok  ' if row.get('ok') else 'FAIL'} alive={row.get('alive')} "
                     f"serving={row.get('serving')} port={row.get('port_after')} left={row.get('group_left')}"
                     + (f" {row.get('note')}" if row.get("note") else ""))
        print(line, flush=True)
        report.append(rec)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sweep_apps.json")
    json.dump(report, open(out, "w"), indent=1, default=str)
    print(f"\nwritten {out}")
    if static:
        print(f"G2  {sum(r['g2']['files'] for r in report)} files, "
              f"{sum(len(r['g2']['hits']) for r in report)} key-shaped strings")
        print(f"G3  {sum(r['g3']['py'] for r in report)} .py, "
              f"{sum(len(r['g3']['compile_bad']) for r in report)} do not compile, "
              f"{sum(len(r['g3']['undefined']) for r in report)} undefined names, "
              f"{sum(r['g3']['other'] for r in report)} other pyflakes messages; "
              f"{sum(r['g3']['launchers'] for r in report)} launchers, "
              f"{sum(len(r['g3']['sh_bad']) for r in report)} do not parse")
        print(f"G5  {sum(r['g5']['calls'] for r in report)} boundary calls, "
              f"{sum(len(r['g5']['missing']) for r in report)} without a deadline")
    if run:
        ok = sum(1 for r in report if r["t2"].get("ok"))
        print(f"T2  {ok}/{len(report)} started, served and stopped cleanly")


if __name__ == "__main__":
    main()
