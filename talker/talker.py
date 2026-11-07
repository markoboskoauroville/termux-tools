#!/usr/bin/env python3
"""talker - speak Claude's replies aloud, in Beatrice's voice.

    talk on          from now on, replies are spoken
    talk off         silence
    talk status      which it is, and what it would use
    talk say TEXT    speak this now, whatever the switch says
    talk say --hr T  the same, read as Croatian (Edge); --en as English (Speechify)
    talk stop        shut up mid-sentence
    talk pause       pause speech
    talk resume      resume paused speech
    talk toggle      pause if playing, resume if paused (the notification's
                     Pause / Play button, which turns into the other one)
    hush             the same toggle; also a code word typed alone into Claude
    talk test        one line, to hear that it works

    talk sub on|off          the subtitle (Voice Engine Android) on or off
    talk sub test [TEXT]     show a subtitle without a voice
    talk sub hide|stop       the subtitle's HIDE, its STOP
    talk sub status          the app, its port, its settings
    talk sub set k=v ...     size colour band grey position bold word wordbg
                             linger offset ypos (the Mac's subtitle.json keys)

WHY IT IS A HOOK AND NOT A HABIT. Claude cannot decide to speak; it writes
text and stops. So the speaking is done by the harness: a Stop hook fires when
a reply is finished, reads the last message out of the transcript, and sends it
here. `talk on` and `talk off` are read by a UserPromptSubmit hook, which is
why they work as things you SAY rather than commands you run -- and why they
work in every chat in this environment, not just the one they were typed in.

THE ENGINE IS MA READER (31.10.2026). Every clip is asked of the MA Reader
server's /api/say, so one app with one fallback chain is the voice of this
phone: Croatian is read by Edge, English by Speechify, and each falls back
inside the reader (Edge -> Google -> Speechify, Speechify -> Edge -> Google).
Only when the reader is not running does the talker call Speechify itself.

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
# What is being said, kept while she says it, so the notification can be put
# up again with Play instead of Pause by `talk toggle` -- which runs as a new
# process, from the notification's button, and knows nothing else.
NOW_FILE = os.path.join(TALK_DIR, "speaking.txt")
# How long one clip may hold the talker, paused time included (2.11.2026: a
# pause longer than the old 15 minutes counted as a failed playback, and the
# fallback player then read the whole clip again from the top).
PLAY_LIMIT_S = 3 * 3600
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
            "engine": "maread", "limit": 1200}
MAREAD_PORT_FILE = "/data/data/com.termux/files/home/.maread-web/port.txt"

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


def show_controls(text, paused=False, wait=False):
    """A pinned notification with the three buttons, while she is speaking.

    Marko, 2.11.2026: "pause is toggle pause, pause and play, and stop just
    stops it." So the first button says what it will do: Pause while she
    speaks, Play while she is paused, and `talk toggle` swaps the label.

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
        try:
            with open(NOW_FILE, "w") as fh:
                fh.write(text or "")
        except OSError:
            pass
        cmd = [
            "termux-notification",
            "--id", NOTIF_ID,
            "--title", "Paused" if paused else "Beatrice is speaking",
            "--content", (text or "")[:70],
            "--priority", "high",
            "--ongoing",
            "--alert-once",
            # Three buttons is all Android gives a notification. Pause and
            # Resume were two of them, so there was no room for Off -- and
            # Off is the one you want when she starts reading a long reply
            # you have already seen. One toggle does both halves.
            "--button1", "Play" if paused else "Pause", "--button1-action", TALK_BIN + " toggle",
            "--button2", "Stop", "--button2-action", TALK_BIN + " stop",
            "--button3", "Off", "--button3-action", TALK_BIN + " off",
            "--on-delete", TALK_BIN + " stop",
        ]
        if wait:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        else:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"notification failed: {e}")


def hide_controls():
    try:
        subprocess.Popen(["termux-notification-remove", NOTIF_ID],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    for f in (PID_FILE, NOW_FILE):
        try:
            os.remove(f)
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
        feed = None
        if SUB_WORDS.get("text"):
            import threading
            feed = threading.Thread(target=subtitle_feed, daemon=True,
                                    args=(p, SUB_WORDS["text"], list(SUB_WORDS.get("words") or [])))
            feed.start()
        try:
            rc = p.wait(timeout=PLAY_LIMIT_S)
            if feed:
                feed.join(1.5)
        except subprocess.TimeoutExpired:
            # Left paused for hours: give up on this clip quietly. NOT a
            # failure -- a failure would hand it to the backup player.
            p.kill()
            p.wait()
            hide_controls()
            log("clip given up after the play limit")
            return True, True
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
        body = r.json()
        audio = base64.b64decode(body.get("audio_data") or "")
    except Exception as e:
        return None, f"bad body: {e}"
    if not audio:
        return None, "no audio came back"
    try:
        SUB_WORDS.update(text=text, words=sp_words(text, body.get("speech_marks")))
    except Exception as e:
        log(f"speech marks: {e}")
    fd, path = tempfile.mkstemp(suffix=".mp3", dir=TALK_DIR)
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio)
    return path, None


def maread(text, lang="auto"):
    """One clip from the MA Reader server. Its port is in port.txt, written
    by the server itself; 8081 when the file is missing."""
    try:
        port = int(open(MAREAD_PORT_FILE).read().strip() or 8081)
    except (OSError, ValueError):
        port = 8081
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/say" % port,
        data=json.dumps({"text": text, "lang": lang, "words": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            audio = r.read()
            engine = r.headers.get("X-Engine", "?")
            try:
                if r.headers.get("X-Words"):
                    SUB_WORDS.update(
                        text=base64.b64decode(r.headers.get("X-Text") or "").decode("utf-8") or text,
                        words=json.loads(base64.b64decode(r.headers["X-Words"]).decode("utf-8")))
            except Exception as e:
                log(f"ma reader words: {e}")
    except Exception as e:
        return None, f"ma reader: {e}"
    if not audio:
        return None, "ma reader returned no audio"
    fd, path = tempfile.mkstemp(suffix=".mp3", dir=TALK_DIR)
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio)
    log(f"ma reader spoke with {engine}")
    return path, None


def android_voice(text, lang="auto"):
    """The fallback. Offline, always there, not Beatrice."""
    try:
        open(BG_FLAG, "w").close()
        cmd = ["termux-tts-speak", "-e", "com.google.android.tts"]
        if lang == "hr":
            cmd += ["-l", "hr"]
        subprocess.run(cmd + [text], timeout=600)
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


def say(text, st=None, force_engine=None, lang="auto"):
    st = st or load_state()
    sweep_old_clips()
    text = speakable(text, st.get("limit", 1200))
    if not text:
        return "nothing to say"
    stop_playing()
    SUB_WORDS.update(text=text, words=[])
    engine = force_engine or st.get("engine", "maread")
    if engine == "maread":
        path, err = maread(text, lang)
        if path:
            ok, may_delete = play_file(path, text[:80])
            if may_delete:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if ok:
                return None
            log("playback failed")
        else:
            log(err)
        # The reader is not running. Speechify's English model would mangle
        # Croatian, so only English goes to it from here.
        engine = "android" if lang == "hr" else "speechify"
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
    android_voice(text, lang)
    return None



# ---------------------------------------------------------------------------
#  The subtitle: Voice Engine (Mantra) for Android, VOICE_ENGINE_ANDROID
# ---------------------------------------------------------------------------
# The same subtitle as on the Mac (VOICE_ENGINE): the words being spoken over
# every app, the word being said lit. The Mac's app asks its voice server where
# the voice is; here the voice is mpv, so the talker tells the app instead:
# POST /start with the text and the word timings, then POST /pos five times a
# second, and each answer says whether the subtitle's STOP or the
# notification's PAUSE was pressed. No app, no subtitle, and nothing else
# changes: every call here is allowed to fail.

SUB_PORT_FILE = os.path.join(TALK_DIR, "subtitle_port")
SUB_PORTS = list(range(8720, 8736))
SUB_WORDS = {"text": "", "words": []}


def sp_words(text, marks):
    """Speechify's speech marks -> [{s, e, t, d}], as MA Reader's sp_tokens:
    character offsets into the text it was given, seconds into the clip."""
    flat = []

    def walk(node):
        if isinstance(node, list):
            for c in node:
                walk(c)
            return
        if not isinstance(node, dict):
            return
        kids = node.get("chunks") or node.get("nestedChunks") or []
        if kids:
            walk(kids)
        elif "start_time" in node or "startTime" in node:
            flat.append(node)

    walk(marks or {})
    n, out = len(text), []
    for m in flat:
        try:
            s = int(m.get("start", m.get("startOffset", 0)))
            e = int(m.get("end", m.get("endOffset", s)))
            t = float(m.get("start_time", m.get("startTime", 0))) / 1000.0
            d = float(m.get("end_time", m.get("endTime", 0))) / 1000.0
        except (TypeError, ValueError):
            continue
        s, e = max(0, min(n, s)), max(0, min(n, e))
        if e > s:
            out.append({"s": s, "e": e, "t": round(t, 3), "d": round(max(d, t), 3)})
    out.sort(key=lambda w: (w["t"], w["s"]))
    return out


def sub_http(port, path, body=None, timeout=0.6):
    import urllib.request
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def sub_port():
    """The app's port: the one it answered on last time, else 8720..8735."""
    first = []
    try:
        first = [int(open(SUB_PORT_FILE).read().strip())]
    except (OSError, ValueError):
        pass
    for p in first + [p for p in SUB_PORTS if p not in first]:
        try:
            if sub_http(p, "/status", timeout=0.4).get("app") == "voiceengine":
                if p not in first:
                    try:
                        with open(SUB_PORT_FILE, "w") as fh:
                            fh.write(str(p))
                    except OSError:
                        pass
                return p
        except Exception:
            continue
    return None


def mpv_props(*props):
    """Several properties in one visit to mpv's socket. A property mpv cannot
    give yet (duration before the file is open) comes back as None. mpv also
    writes events on the same socket, so replies are matched by request_id."""
    import socket as _socket
    out = {p: None for p in props}
    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(IPC_SOCKET)
        for i, p in enumerate(props, 1):
            sock.sendall((json.dumps({"command": ["get_property", p],
                                      "request_id": i}) + "\n").encode())
        buf, want = b"", set(range(1, len(props) + 1))
        while want:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                rid = msg.get("request_id")
                if rid in want:
                    want.discard(rid)
                    if msg.get("error") == "success":
                        out[props[rid - 1]] = msg.get("data")
        sock.close()
    except Exception:
        pass
    return out


def subtitle_feed(proc, text, words):
    port = sub_port()
    if not port:
        return
    rid = "%x" % int(time.time() * 1000)
    try:
        sub_http(port, "/start", {"id": rid, "text": text, "words": words}, timeout=1.5)
    except Exception as e:
        log(f"subtitle start: {e}")
        return
    log(f"subtitle on :{port}, {len(words)} timed words")
    misses = 0
    while proc.poll() is None:
        time.sleep(0.2)
        pr = mpv_props("time-pos", "pause", "duration")
        if pr["time-pos"] is None and proc.poll() is not None:
            break
        try:
            reply = sub_http(port, "/pos", {
                "id": rid, "pos": pr["time-pos"] if pr["time-pos"] is not None else -1,
                "paused": bool(pr["pause"]), "dur": pr["duration"] or 0})
            misses = 0
        except Exception:
            misses += 1
            if misses > 10:
                return
            continue
        cmd = reply.get("cmd") or ""
        if cmd == "stop":
            log("stopped from the subtitle")
            stop_playing()
            break
        if cmd == "toggle":
            import threading
            threading.Thread(target=toggle_pause, daemon=True).start()
    try:
        sub_http(port, "/end", {"id": rid})
    except Exception:
        pass


def sub_command(args):
    """talk sub ...: Voice Engine Android, from Claude Code or any shell."""
    port = sub_port()
    if not port:
        return ("Voice Engine (Mantra) is not answering on 127.0.0.1:8720-8735. "
                "Open the app once, or install it from github.com/markoboskoauroville/VOICE_ENGINE_ANDROID.")
    what = (args[0] if args else "status").lower()
    try:
        if what in ("on", "off"):
            sub_http(port, "/subtitles", {"on": what == "on"})
            return f"subtitles {what}"
        if what == "test":
            r = sub_http(port, "/test", {"text": " ".join(args[1:]), "seconds": 8})
            return "shown for 8 s" if r.get("overlay") else "the app may not draw over other apps yet: open it and press ALLOW"
        if what in ("hide", "stop", "toggle"):
            sub_http(port, "/" + what, {})
            return what
        if what == "set":
            body = {}
            for kv in args[1:]:
                if "=" not in kv:
                    continue
                k, v = kv.split("=", 1)
                if v.lower() in ("true", "on", "yes"):
                    body[k] = True
                elif v.lower() in ("false", "off", "no"):
                    body[k] = False
                else:
                    try:
                        body[k] = float(v)
                    except ValueError:
                        body[k] = v
            return json.dumps(sub_http(port, "/settings", body), indent=1)
        st = sub_http(port, "/status")
        st["settings"] = sub_http(port, "/settings")
        return json.dumps(st, indent=1)
    except Exception as e:
        return f"voice engine: {e}"

# ---------------------------------------------------------------------------
#  The words you say
# ---------------------------------------------------------------------------

ON_RE = re.compile(r"^\s*(?:talk|voice|speak)\s*(?:mode\s*)?on\b[\s.!]*$", re.I)
OFF_RE = re.compile(r"^\s*(?:talk|voice|speak)\s*(?:mode\s*)?off\b[\s.!]*$", re.I)
# "say ..." means speak these words. Anchored at the start, and refusing a
# trailing question mark, so "what does the log say?" is left alone.
SAY_RE = re.compile(r"^\s*say\s+(?!.*\?\s*$)(.+?)[\s.!]*$", re.I | re.S)
# "hush" alone is the pause button: the |hush key on the Termux bar types it and
# presses Enter. Pause if she is speaking, resume if paused. A new reply still
# starts over from its own beginning -- say() stops whatever is paused first.
HUSH_RE = re.compile(r"^\s*hush[\s.!]*$", re.I)




# Pause and resume for interactive control
def relabel():
    """Put the notification up again with the button the state now needs:
    Play while paused, Pause while speaking. Asked of mpv, not guessed."""
    ok, reply = mpv_command("get_property", "pause")
    if not ok:
        return
    try:
        paused = bool(json.loads(reply.splitlines()[0]).get("data"))
    except Exception:
        return
    try:
        text = open(NOW_FILE).read()
    except OSError:
        return  # she has finished: nothing to relabel
    show_controls(text, paused=paused, wait=True)
    # The clip may have ended during the second that call takes; then the
    # notification just put up would stay forever. Take it down again.
    if not os.path.exists(PID_FILE):
        hide_controls()


def pause_current():
    ok, why = mpv_command("set_property", "pause", True)
    if ok:
        relabel()
    return "paused" if ok else f"nothing playing ({why})"


def resume_current():
    ok, why = mpv_command("set_property", "pause", False)
    if ok:
        relabel()
    return "resumed" if ok else f"nothing to resume ({why})"


def toggle_pause():
    ok, _ = mpv_command("cycle", "pause")
    if ok:
        relabel()
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
        rest, lang = argv[1:], "auto"
        if rest and rest[0] in ("--hr", "--en"):
            lang, rest = rest[0][2:], rest[1:]
        text = " ".join(rest) or sys.stdin.read()
        err = say(text, st, lang=lang)
        if err:
            print(err)
    elif cmd == "voice":
        if len(argv) > 1:
            st["voice"] = argv[1]
            st["name"] = argv[1].split("_")[0].title()
            save_state(st)
        print(f"voice: {st.get('name')} ({st.get('voice')})")
    elif cmd == "engine":
        if len(argv) > 1 and argv[1] in ("maread", "speechify", "android"):
            st["engine"] = argv[1]
            save_state(st)
        print(f"engine: {st.get('engine')}")
    elif cmd in ("sub", "subtitle", "subtitles"):
        print(sub_command(argv[1:]))
    elif cmd == "pause":
        print(pause_current())
    elif cmd == "resume":
        print(resume_current())
    elif cmd in ("toggle", "playpause", "hush"):
        print(toggle_pause())
    elif cmd in ("-h", "--help", "help", ""):
        print(__doc__)
    else:
        print(f"unknown: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])

