#!/usr/bin/env python3
"""talker - speak Claude's replies aloud, in Beatrice's voice.

    talk on          from now on, replies are spoken
    talk off         silence
    talk status      which it is, and what it would use
    talk say TEXT    speak this now, whatever the switch says
    talk stop        shut up mid-sentence
    talk pause       pause speech
    talk resume      resume paused speech
    talk toggle      pause if playing, resume if paused (the notification button)
    talk test        one line, to hear that it works

WHY IT IS A HOOK AND NOT A HABIT. Claude cannot decide to speak; it writes
text and stops. So the speaking is done by the harness: a Stop hook fires when
a reply is finished, reads the last message out of the transcript, and sends it
here. `talk on` and `talk off` are read by a UserPromptSubmit hook, which is
why they work as things you SAY rather than commands you run -- and why they
work in every chat in this environment, not just the one they were typed in.

THE VOICE. Beatrice, from Speechify (beatrice_32, British, warm) -- the same
voice and the same key file MA Reader uses, so there is one place to change it.
When the network is not there, or the key is spent, it falls back to the
offline Android voice rather than going quiet: a talker that silently stops
talking is indistinguishable from a broken one.

WHAT IS NOT SPOKEN. Code blocks, file paths, URLs and tables are skipped. They
are for reading, and hearing a shell command dictated character by character is
worse than useless. Very long replies are cut at a limit with a spoken note
that there is more on screen.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
TALK_DIR = os.path.join(HOME, ".talker")
STATE_FILE = os.path.join(TALK_DIR, "state.json")
PID_FILE = os.path.join(TALK_DIR, "playing.pid")
IPC_SOCKET = os.path.join(TALK_DIR, "mpv.sock")
# mpv's exit status for "quit because I was asked to" (see mpv(1), EXIT CODES).
MPV_QUIT_RC = 4
# Written only when a clip was handed to one of the Termux players. Stopping
# those means a round trip out to Termux:API, which costs the better part of
# five seconds each -- worth paying to silence something that is actually
# making noise, and pure waste before every single utterance otherwise.
BG_FLAG = os.path.join(TALK_DIR, "background_player")
NOTIF_ID = "talker_speech"
TALK_BIN = "/data/data/com.termux/files/usr/bin/talk"
LOG_FILE = os.path.join(TALK_DIR, "talker.log")
KEY_FILES = [
    os.path.join(HOME, ".maread-web", "speechify_api.txt"),
    os.path.join(HOME, ".talker", "speechify_api.txt"),
    "/storage/emulated/0/Download/api/speechify_api.txt",
]

SP_API = "https://api.sws.speechify.com"
SP_MODEL = "simba-english"
DEFAULTS = {"on": False, "voice": "beatrice_32", "name": "Beatrice",
            "engine": "speechify", "limit": 1200}

KEY_RE = re.compile(r"\bsk_[0-9A-Za-z_-]{16,}\b")

# We run inside a PRoot distro, where $HOME is /root -- but Android's own
# media player runs OUTSIDE it and cannot see that path at all. Same bytes,
# different spelling: anything handed to a Termux tool must be named the way
# Android names it, or it comes back "open failed: ENOENT".
TERMUX_HOME = "/data/data/com.termux/files/home"


def android_path(path):
    real = os.path.realpath(path)
    if real.startswith(HOME) and not real.startswith(TERMUX_HOME):
        return TERMUX_HOME + real[len(HOME):]
    return real


def log(msg):
    try:
        os.makedirs(TALK_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def load_state():
    st = dict(DEFAULTS)
    try:
        with open(STATE_FILE) as fh:
            st.update(json.load(fh))
    except Exception:
        pass
    return st


def save_state(st):
    os.makedirs(TALK_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=2)
    os.replace(tmp, STATE_FILE)


def find_key():
    """The key file is a notebook, not a config file: URLs, labels and keys
    all in one. Take anything shaped like a Speechify key, longest first --
    the long ones are the live ones."""
    for path in KEY_FILES:
        try:
            raw = open(path, errors="replace").read()
        except OSError:
            continue
        keys = sorted(set(KEY_RE.findall(raw)), key=len, reverse=True)
        keys = [k for k in keys if len(k) >= 40]
        if keys:
            return keys[0]
    return None


# ---------------------------------------------------------------------------
#  Turning a written reply into something worth hearing
# ---------------------------------------------------------------------------

def speakable(text, limit=1200):
    if not text:
        return ""
    t = text
    # Fenced code: say that it is there, do not read it out.
    t = re.sub(r"```[\s\S]*?```", " . code on screen . ", t)
    t = re.sub(r"~~~[\s\S]*?~~~", " . code on screen . ", t)
    # Markdown tables read as a wall of pipes.
    t = re.sub(r"^\s*\|.*\|\s*$", " ", t, flags=re.M)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"https?://\S+", " a link ", t)
    # A path spelled out loud is noise; its last part is usually the point.
    # A dotfile's name keeps its dot on screen but must lose it here: read
    # aloud, ".mamc" is a full stop followed by a word, and every rule further
    # down that tidies punctuation would then eat the space before it.
    t = re.sub(r"(?:~|/)[\w./-]{6,}",
               lambda m: (os.path.basename(m.group(0).rstrip("/")).lstrip(".")
                          or " a path "), t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", t)
    t = re.sub(r"^\s*[-*+]\s+", " ", t, flags=re.M)
    t = re.sub(r"[─-╿•●✓✗]", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\s*\n\s*", ". ", t)
    t = re.sub(r"\.\s*\.\s*(\.\s*)+", ". ", t)
    # Markdown punctuation turns into stutters when it is read: a colon that
    # introduced a code block becomes ":.", a bullet ending a line becomes "..".
    t = re.sub(r"[:;,]\s*\.", ".", t)
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"\s+([.,!?])(?=\s|$)", r"\1", t)
    t = re.sub(r"\.\s*\.", ".", t)
    t = t.strip()
    # Punctuation alone is not speech. A reply of only whitespace, or only
    # markdown that all got stripped, collapses to "." -- which is truthy, so
    # say() would happily spend a Speechify call synthesising a full stop.
    if not any(ch.isalnum() for ch in t):
        return ""
    if len(t) > limit:
        cut = t[:limit]
        dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if dot > limit * 0.6:
            cut = cut[: dot + 1]
        t = cut + " . The rest is on screen."
    return t


# ---------------------------------------------------------------------------
#  Making sound
# ---------------------------------------------------------------------------

def stop_playing():
    """Silence whatever is making noise -- and nothing more.

    Each of the Termux players costs seconds to stop, so they are only asked
    if one of them was actually given something to play. mpv is free to ask.
    """
    if os.path.exists(IPC_SOCKET):
        mpv_command("quit")
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 15)
    except Exception:
        pass
    if os.path.exists(BG_FLAG):
        subprocess.run(["termux-media-player", "stop"],
                       capture_output=True, timeout=10)
        subprocess.run(["termux-tts-speak", ""], capture_output=True, timeout=5)
        try:
            os.remove(BG_FLAG)
        except OSError:
            pass
    hide_controls()


def mpv_command(*args):
    """Send one command to the running mpv over its IPC socket.

    SIGSTOP was the obvious way to pause and it does not work here: PRoot
    drives everything under ptrace, and a traced process absorbs the stop
    rather than stopping. mpv's own IPC socket has no such problem, it is
    what MA Player already drives the same mpv with, and it pauses the audio
    properly rather than just freezing the process that feeds it.
    """
    import socket as _socket
    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(IPC_SOCKET)
        sock.sendall((json.dumps({"command": list(args)}) + "\n").encode())
        try:
            reply = sock.recv(4096).decode("utf-8", "replace")
        except Exception:
            reply = ""
        sock.close()
        return True, reply
    except Exception as e:
        return False, str(e)


def show_controls(text):
    """A pinned notification with the three buttons, while she is speaking.

    The buttons run `talk pause|resume|stop` -- and they run them OUTSIDE
    this PRoot, in Termux's own shell. That works only because /root here and
    Termux's home are the same directory, so the same `talk` command and the
    same pid file are reachable from both sides. The pid itself needs no
    translation: PRoot does not namespace processes, so a signal sent from
    Termux lands on the mpv we started in here.
    """
    try:
        # Popen, not run: this round trip out to Termux:API and back takes the
        # better part of ten seconds on this phone, and waiting for it meant
        # ten seconds of silence before Beatrice said anything. The
        # notification can catch up with her.
        subprocess.Popen([
            "termux-notification",
            "--id", NOTIF_ID,
            "--title", "Beatrice is speaking",
            "--content", (text or "")[:70],
            "--priority", "high",
            "--ongoing",
            "--alert-once",
            # Three buttons is all Android gives a notification. Pause and
            # Resume were two of them, so there was no room for Off -- and
            # Off is the one you want when she starts reading a long reply
            # you have already seen. One toggle does both halves.
            "--button1", "Pause/Resume", "--button1-action", TALK_BIN + " toggle",
            "--button2", "Stop", "--button2-action", TALK_BIN + " stop",
            "--button3", "Off", "--button3-action", TALK_BIN + " off",
            "--on-delete", TALK_BIN + " stop",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"notification failed: {e}")


def hide_controls():
    try:
        subprocess.Popen(["termux-notification-remove", NOTIF_ID],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def play_file(path, text=""):
    """mpv, started detached and then waited on.

    Popen rather than run() for one reason: the pid has to be written down
    while the clip is still playing, or nothing outside this process can
    pause or stop it. Waiting afterwards keeps the old guarantee that the
    temp file is only deleted once playback is really finished.

    A negative return code means a signal ended it -- you pressed Stop. That
    is a completed playback, not a failure, and must NOT fall through to the
    backup player, which would cheerfully start saying the same thing again.
    """
    try:
        os.remove(IPC_SOCKET)
    except OSError:
        pass
    try:
        p = subprocess.Popen(["mpv", "--really-quiet", "--no-video",
                              "--audio-display=no",
                              "--input-ipc-server=" + IPC_SOCKET, path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        with open(PID_FILE, "w") as fh:
            fh.write(str(p.pid))
        show_controls(text)
        rc = p.wait(timeout=900)
        hide_controls()
        if rc == 0:
            return True, True
        if rc < 0:
            log(f"stopped by signal {-rc}")
            return True, True
        # mpv exits 4 when it was told to quit -- which is exactly what the
        # Stop button does, over the IPC socket. That is a finished playback.
        # It was being read as a failure, and the fallback below then handed
        # the same clip to Android's player: press Stop, and she started the
        # sentence again from the top, now beyond the reach of the socket.
        if rc == MPV_QUIT_RC:
            log("stopped by request")
            return True, True
        log(f"mpv rc={rc}")
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"mpv failed: {e}")
    hide_controls()
    try:
        p = subprocess.run(["termux-media-player", "play", android_path(path)],
                           capture_output=True, text=True, timeout=60)
        out = (p.stdout or "") + (p.stderr or "")
        if "Now Playing" in out:
            open(BG_FLAG, "w").close()
            return True, False
        log(f"termux-media-player said: {out.strip()[:140]}")
    except Exception as e:
        log(f"termux-media-player failed: {e}")
    return False, True


def speechify(text, voice):
    key = find_key()
    if not key:
        return None, "no speechify key found"
    try:
        import requests
    except ImportError:
        return None, "requests is not installed"
    try:
        r = requests.post(
            SP_API + "/v1/audio/speech",
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json={"input": text, "voice_id": voice,
                  "audio_format": "mp3", "model": SP_MODEL},
            timeout=120)
    except Exception as e:
        return None, f"network: {e}"
    if r.status_code != 200:
        return None, f"http {r.status_code}: {r.text[:160]}"
    try:
        audio = base64.b64decode(r.json().get("audio_data") or "")
    except Exception as e:
        return None, f"bad body: {e}"
    if not audio:
        return None, "no audio came back"
    fd, path = tempfile.mkstemp(suffix=".mp3", dir=TALK_DIR)
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio)
    return path, None


def android_voice(text):
    """The fallback. Offline, always there, not Beatrice."""
    try:
        open(BG_FLAG, "w").close()
        subprocess.run(["termux-tts-speak", "-e", "com.google.android.tts",
                        text], timeout=600)
        try:
            os.remove(BG_FLAG)
        except OSError:
            pass
        return True
    except Exception as e:
        log(f"android tts failed: {e}")
        return False


def sweep_old_clips(older_than=600):
    """Clips the background player was still holding are cleaned up later,
    not sooner: deleting one early is exactly the bug this works around."""
    now = time.time()
    try:
        for name in os.listdir(TALK_DIR):
            if not name.endswith(".mp3"):
                continue
            f = os.path.join(TALK_DIR, name)
            try:
                if now - os.path.getmtime(f) > older_than:
                    os.remove(f)
            except OSError:
                pass
    except OSError:
        pass


def say(text, st=None, force_engine=None):
    st = st or load_state()
    sweep_old_clips()
    text = speakable(text, st.get("limit", 1200))
    if not text:
        return "nothing to say"
    stop_playing()
    engine = force_engine or st.get("engine", "speechify")
    if engine == "speechify":
        path, err = speechify(text, st.get("voice", "beatrice_32"))
        if path:
            ok, may_delete = play_file(path, text[:80])
            if may_delete:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if ok:
                return None
            log("playback failed, falling back to the android voice")
        else:
            log(f"speechify: {err}")
    android_voice(text)
    return None


# ---------------------------------------------------------------------------
#  The words you say
# ---------------------------------------------------------------------------

ON_RE = re.compile(r"^\s*(?:talk|voice|speak)\s*(?:mode\s*)?on\b[\s.!]*$", re.I)
OFF_RE = re.compile(r"^\s*(?:talk|voice|speak)\s*(?:mode\s*)?off\b[\s.!]*$", re.I)
# "say ..." means speak these words. Anchored at the start, and refusing a
# trailing question mark, so "what does the log say?" is left alone.
SAY_RE = re.compile(r"^\s*say\s+(?!.*\?\s*$)(.+?)[\s.!]*$", re.I | re.S)




# Pause and resume for interactive control
def pause_current():
    ok, why = mpv_command("set_property", "pause", True)
    return "paused" if ok else f"nothing playing ({why})"


def resume_current():
    ok, why = mpv_command("set_property", "pause", False)
    return "resumed" if ok else f"nothing to resume ({why})"


def toggle_pause():
    ok, _ = mpv_command("cycle", "pause")
    return "toggled" if ok else "nothing playing"


def main(argv):
    cmd = (argv[0] if argv else "status").lower()
    st = load_state()

    if cmd in ("on", "start"):
        st["on"] = True
        save_state(st)
        print(f"talk on - replies will be spoken by {st.get('name')}")
        say("Talk mode on. I will read my replies to you.", st)
    elif cmd in ("off", "stop-mode", "quiet"):
        st["on"] = False
        save_state(st)
        stop_playing()
        print("talk off")
    elif cmd == "stop":
        stop_playing()
    elif cmd == "status":
        key = "yes" if find_key() else "NO KEY"
        print(f"talk {'ON' if st['on'] else 'off'}   voice {st.get('name')} "
              f"({st.get('voice')})   engine {st.get('engine')}   "
              f"speechify key: {key}")
    elif cmd == "test":
        say("This is Beatrice. If you can hear me, the talker is working.", st,
            force_engine=argv[1] if len(argv) > 1 else None)
    elif cmd == "say":
        text = " ".join(argv[1:]) or sys.stdin.read()
        err = say(text, st)
        if err:
            print(err)
    elif cmd == "voice":
        if len(argv) > 1:
            st["voice"] = argv[1]
            st["name"] = argv[1].split("_")[0].title()
            save_state(st)
        print(f"voice: {st.get('name')} ({st.get('voice')})")
    elif cmd == "engine":
        if len(argv) > 1 and argv[1] in ("speechify", "android"):
            st["engine"] = argv[1]
            save_state(st)
        print(f"engine: {st.get('engine')}")
    elif cmd == "pause":
        print(pause_current())
    elif cmd == "resume":
        print(resume_current())
    elif cmd in ("toggle", "playpause"):
        print(toggle_pause())
    elif cmd in ("-h", "--help", "help", ""):
        print(__doc__)
    else:
        print(f"unknown: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])

