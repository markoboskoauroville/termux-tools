#!/usr/bin/env python3
"""ccnotify - the phone tells you when Claude stops or needs you.

    ccnotify needs        Notification hook: Claude is waiting on you
    ccnotify stopped      Stop hook: Claude finished a reply
    ccnotify prompt       UserPromptSubmit hook: you are back, clear the bubbles
    ccnotify push TEXT    send TEXT into the running session, as if typed
    ccnotify status       which session is reachable, if any
    ccnotify reap [S]     kill Termux:API calls left hanging longer than S s

WHY. Claude Code runs in a terminal that is usually not on screen. When it
finishes, or stops to ask for a permission, nothing on the phone says so, and
a session that is waiting looks exactly like one that is working. So three
hooks post an Android notification at those moments, and each notification
carries a button that pushes a message straight back into the session -- so
"continue" is one tap from the lock screen, without opening Termux at all.

HOW THE BUTTON REACHES THE SESSION. Claude Code listens on a Unix socket for
messages from other sessions (its cross-session messaging). The launcher passes
--messaging-socket-path so that socket exists on this phone at all -- the
default location fails under PRoot, which has no uid map. Every hook writes the
socket path and its token to inbox.json; `ccnotify push` reads them back and
speaks the one-line protocol: an auth line, then a user message.

The button actions run OUTSIDE the PRoot, in Termux's own shell -- that is why
every path here is absolute, why the socket path is translated between the two
spellings of home, and why this file must run on Termux's python as well as the
distro's (stdlib only).
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
TERMUX_HOME = "/data/data/com.termux/files/home"
PREFIX = "/data/data/com.termux/files/usr"
# CCNOTIFY_DIR: a test sets it so a session it starts announces itself
# somewhere private. Without it, two sessions share one inbox.json and a
# test's push lands in whichever session's hook fired last -- which, once,
# was a person's live session rather than the one under test.
DIR = os.environ.get("CCNOTIFY_DIR") or os.path.join(HOME, ".ccnotify")
INBOX = os.path.join(DIR, "inbox.json")
LOG_FILE = os.path.join(DIR, "ccnotify.log")
CCPUSH = PREFIX + "/bin/ccpush"
# Bring Termux to the front. `am` runs in Termux's shell, where it exists.
OPEN_TERMUX = "am start -n com.termux/.app.TermuxActivity"
ID_NEEDS = "claude_needs_you"
ID_STOPPED = "claude_stopped"
ID_PUSH = "claude_push"


def log(msg):
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def spellings(path):
    """Both names of one file: the PRoot one and the Termux one."""
    out = [path]
    if path.startswith("/root/"):
        out.append(TERMUX_HOME + path[len("/root"):])
    elif path.startswith(TERMUX_HOME + "/"):
        out.append("/root" + path[len(TERMUX_HOME):])
    return out


# ---------------------------------------------------------------------------
#  Where the session can be reached
# ---------------------------------------------------------------------------

def record_inbox():
    """Called from every hook: the hook inherits the session's environment,
    which is the only place the socket path and token are ever announced."""
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    if not sock:
        return False
    rec = {"socket": sock,
           "token": os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN") or "",
           "session": os.environ.get("CLAUDE_CODE_SESSION_ID") or "",
           "at": time.time()}
    os.makedirs(DIR, exist_ok=True)
    tmp = INBOX + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rec, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, INBOX)
    return True


def load_inbox():
    try:
        with open(INBOX) as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) and rec.get("socket") else None
    except Exception:
        return None


def push(text, timeout=4.0):
    """Send one user message into the session. Returns (ok, detail)."""
    text = (text or "").strip()
    if not text:
        return False, "nothing to send"
    if not text.startswith(PUSH_PREFIX):
        text = PUSH_PREFIX + text
    rec = load_inbox()
    if not rec:
        return False, "no session has announced a socket yet"
    last_err = "socket not found"
    for path in spellings(rec["socket"]):
        if not os.path.exists(path):
            continue
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(path)
            lines = []
            if rec.get("token"):
                lines.append(json.dumps({"type": "auth", "token": rec["token"]}))
            lines.append(json.dumps({"type": "user",
                                     "message": {"role": "user", "content": text}}))
            s.sendall(("\n".join(lines) + "\n").encode())
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            reply = b""
            try:
                while len(reply) < 4096:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    reply += chunk
            except (socket.timeout, OSError):
                pass
            reply = reply.decode("utf-8", "replace").strip()
            if "error" in reply.lower() and "ok" not in reply.lower():
                return False, reply[:200]
            return True, reply[:200] or "sent"
        except (OSError, socket.timeout) as e:
            last_err = f"{path}: {e}"
        finally:
            s.close()
    return False, last_err


# ---------------------------------------------------------------------------
#  The bubbles
# ---------------------------------------------------------------------------

# A Termux:API call is a shell script that starts `termux-api`, which sends
# the request to the Termux:API app over a socket and WAITS for the reply.
# When the app has been swapped out or restarted, that reply never comes and
# the wait never ends: on 13.9.2026 twelve of them sat at
# __skb_wait_for_more_packets for a quarter of an hour, all children of pid 1,
# all left by these hooks. Android 12+ counts every such process against the
# app's phantom-process limit (32) and kills processes -- Claude Code's
# among them -- when it is passed. So every call is bounded by `timeout`, and
# every hook first reaps what an earlier call left behind.
BOUND = 45          # seconds one Termux:API call may take before it is killed
STALE = 120         # an orphaned termux-* process older than this is reaped
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def bounded(argv):
    """The same call, with a deadline. `timeout` becomes the group leader,
    and on expiry signals the whole group: the script AND its termux-api."""
    if shutil.which("timeout"):
        return ["timeout", "-k", "5", str(BOUND)] + list(argv)
    return list(argv)


def spawn(argv):
    """Popen, never run: Termux:API takes seconds per call and a hook that
    waits for it holds up the prompt you wanted to type into."""
    subprocess.Popen(bounded(argv), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def is_termux_api_call(argv):
    """A termux-* script, the termux-api helper it runs, or a `timeout`
    wrapped around either. Never the wake lock: it is quick, and it is the
    one Termux call that keeps this session alive."""
    for part in argv:
        base = os.path.basename(part)
        if base == "termux-api" or (base.startswith("termux-")
                                     and base not in ("termux-wake-lock",
                                                      "termux-wake-unlock")):
            return True
    return False


def now_since_boot():
    """The clock /proc/<pid>/stat's starttime is on. NOT /proc/uptime:
    proot-distro binds a fake one that reads about two minutes forever, and
    an age computed from it came out as minus three hours (13.9.2026)."""
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])


def orphans():
    """Every visible process whose parent is pid 1: (pid, age_s, argv)."""
    try:
        uptime = now_since_boot()
    except Exception:
        return
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"/proc/{pid}/stat") as fh:
                stat = fh.read()
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = [a.decode("utf-8", "replace")
                        for a in fh.read().split(b"\0") if a]
        except Exception:
            continue
        # the command name sits in parentheses and may contain spaces
        fields = stat[stat.rfind(")") + 2:].split()
        try:
            ppid = int(fields[1])
            started = int(fields[19]) / CLK_TCK
        except (IndexError, ValueError):
            continue
        if ppid != 1:
            continue
        yield pid, uptime - started, argv


def reap(stale=STALE, procs=None):
    """Kill orphaned Termux:API calls older than `stale` seconds. Returns
    the pids it signalled. `procs` lets a test hand in rows of its own."""
    killed = []
    for pid, age, argv in (orphans() if procs is None else procs):
        if age < stale or not is_termux_api_call(argv):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    if killed:
        log(f"reaped {len(killed)} hung Termux:API call(s): {killed}")
    return killed


def notify(nid, title, content, buttons=(), ongoing=False):
    argv = ["termux-notification", "--id", nid, "--title", title,
            "--content", content[:200], "--priority", "high", "--alert-once",
            "--action", OPEN_TERMUX]
    if ongoing:
        argv.append("--ongoing")
    for i, (label, action) in enumerate(buttons[:3], start=1):
        argv += [f"--button{i}", label, f"--button{i}-action", action]
    try:
        spawn(argv)
        return True
    except Exception as e:
        log(f"notify failed: {e}")
        return False


def remove(*ids):
    for nid in ids:
        try:
            spawn(["termux-notification-remove", nid])
        except Exception:
            pass


# A message that arrives over the socket is shown to Claude as coming from
# another session, not from the keyboard -- so it must say who pressed the
# button, or Claude treats a plain "continue" as a stranger's request.
PUSH_PREFIX = "From Marko, via the phone's notification button: "


def push_button(label, text):
    return (label, f"{CCPUSH} {json.dumps(text)}")


def last_assistant_text(path):
    """The last thing Claude said, out of the transcript jsonl. Text blocks
    only -- a turn that was all tool calls has nothing to show."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        parts = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts += [b.get("text") or "" for b in content
                      if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p).strip()
        if text:
            return text
    return ""


def headline(text, limit=110):
    """First sentence or so, flattened, for a one-line bubble."""
    t = " ".join((text or "").split())
    for ch in ("`", "*", "#"):
        t = t.replace(ch, "")
    return (t[:limit] + "…") if len(t) > limit else t


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
#  The three moments
# ---------------------------------------------------------------------------

def cmd_needs():
    data = read_hook_input()
    record_inbox()
    reap()
    kind = data.get("notification_type") or ""
    msg = headline(data.get("message") or data.get("title") or kind or
                   "Claude is waiting for you")
    if kind == "permission_prompt":
        title = "Claude needs a permission"
        hint = "approve in Termux, or in the Claude app"
    elif kind == "idle_prompt":
        title = "Claude is waiting for you"
        hint = "tap Continue to push it on"
    else:
        title = "Claude needs you"
        hint = kind or ""
    content = msg if not hint else f"{msg} — {hint}"
    notify(ID_NEEDS, title, content,
           [("Open", OPEN_TERMUX), push_button("Continue", "continue"),
            push_button("Yes", "yes")],
           ongoing=True)
    log(f"needs: {kind} {msg[:60]}")


def cmd_stopped():
    data = read_hook_input()
    record_inbox()
    reap()
    if data.get("stop_hook_active"):
        return
    # The hook input carries the reply itself (last_assistant_message). The
    # transcript is only the fallback: a session with transcript saving off
    # -- one started from inside another session, for instance -- has no
    # file to read, and the bubble went generic for exactly that reason.
    tp = data.get("transcript_path")
    text = headline(data.get("last_assistant_message") or last_assistant_text(tp))
    if not text:
        log(f"stopped: no text in the hook input and none in {tp!r} "
            f"(exists={bool(tp and os.path.isfile(tp))})")
    remove(ID_NEEDS)
    notify(ID_STOPPED, "Claude stopped",
           text or "finished a turn and is waiting for you",
           [("Open", OPEN_TERMUX), push_button("Continue", "continue"),
            push_button("Carry on", "keep going with the task until it is done")])
    log(f"stopped: {text[:60]}")


def cmd_prompt():
    read_hook_input()
    record_inbox()
    reap()
    remove(ID_NEEDS, ID_STOPPED)


def cmd_push(argv):
    text = " ".join(argv).strip() or sys.stdin.read().strip()
    ok, detail = push(text)
    log(f"push {'ok' if ok else 'FAILED'}: {text[:40]!r} -> {detail}")
    if ok:
        print(f"sent: {text}")
        remove(ID_NEEDS, ID_STOPPED)
        notify(ID_PUSH, "Sent to Claude", text[:120])
    else:
        print(f"could not send: {detail}")
        notify(ID_PUSH, "Could not reach Claude", detail[:120],
               [("Open", OPEN_TERMUX)])
    return 0 if ok else 1


def cmd_status():
    rec = load_inbox()
    if not rec:
        print("no session has announced a socket (start claude through its launcher)")
        return 1
    live = [p for p in spellings(rec["socket"]) if os.path.exists(p)]
    print(f"socket   {rec['socket']}   {'present' if live else 'GONE'}")
    print(f"token    {'yes' if rec.get('token') else 'none'}")
    print(f"session  {rec.get('session') or '?'}   announced {int(time.time() - rec.get('at', 0))}s ago")
    return 0 if live else 1


def main(argv):
    cmd = (argv[0] if argv else "status").lower()
    if cmd == "needs":
        cmd_needs()
    elif cmd == "stopped":
        cmd_stopped()
    elif cmd == "prompt":
        cmd_prompt()
    elif cmd == "push":
        return cmd_push(argv[1:])
    elif cmd == "status":
        return cmd_status()
    elif cmd == "reap":
        killed = reap(stale=float(argv[1]) if len(argv) > 1 else STALE)
        print(f"reaped {len(killed)}: {killed}" if killed else "nothing hung")
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"unknown: {cmd}\n{__doc__}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
