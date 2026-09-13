#!/usr/bin/env python3
"""ccnotify - the phone tells you when Claude stops or needs you.

    ccnotify needs        Notification hook: Claude is waiting on you
    ccnotify stopped      Stop hook: Claude finished a reply
    ccnotify prompt       UserPromptSubmit hook: you are back, clear the bubbles
    ccnotify push TEXT    send TEXT into the running session, as if typed
    ccnotify status       which session is reachable, if any

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
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
TERMUX_HOME = "/data/data/com.termux/files/home"
PREFIX = "/data/data/com.termux/files/usr"
DIR = os.path.join(HOME, ".ccnotify")
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

def notify(nid, title, content, buttons=(), ongoing=False):
    """Popen, never run: Termux:API takes seconds per call and a hook that
    waits for it holds up the prompt you wanted to type into."""
    argv = ["termux-notification", "--id", nid, "--title", title,
            "--content", content[:200], "--priority", "high", "--alert-once",
            "--action", OPEN_TERMUX]
    if ongoing:
        argv.append("--ongoing")
    for i, (label, action) in enumerate(buttons[:3], start=1):
        argv += [f"--button{i}", label, f"--button{i}-action", action]
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception as e:
        log(f"notify failed: {e}")
        return False


def remove(*ids):
    for nid in ids:
        try:
            subprocess.Popen(["termux-notification-remove", nid],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
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
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"unknown: {cmd}\n{__doc__}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
