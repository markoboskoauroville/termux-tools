# Termux tools

Three programs built on the phone, inside Termux + PRoot.

    mamc      a Midnight Commander for every app on this phone, in folders:
              open one, run an app, stop it with Q, move it with c, uninstall
              it into a restorable trash
    talk      Claude's replies spoken aloud in Beatrice's voice, on a code word
    ccnotify  a notification when Claude stops or needs you, with a button
              that pushes "continue" into the session from the lock screen

The environment they were built in, and everything it does differently, is
`modules/termux-proot-working.md` in MANTRA_MANIFEST. Read that first if
anything here behaves strangely.

## Install

```bash
install -m 755 mamc/mamc.py  "$HOME/.mamc/mamc.py"
install -m 755 mamc/mamc     "$PREFIX/bin/mamc"
install -m 755 talker/*.py   "$HOME/.talker/"
install -m 755 talker/talk   "$PREFIX/bin/talk"
install -m 755 notify/ccnotify.py "$HOME/.ccnotify/ccnotify.py"
install -m 755 notify/ccnotify notify/ccpush "$PREFIX/bin/"
```

The talker and the notifier are hooks in `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 $HOME/.talker/hook_prompt.py"}]},
                       {"hooks": [{"type": "command", "command": "python3 $HOME/.ccnotify/ccnotify.py prompt"}]}],
  "Stop":             [{"hooks": [{"type": "command", "command": "python3 $HOME/.talker/hook_stop.py"}]},
                       {"hooks": [{"type": "command", "command": "python3 $HOME/.ccnotify/ccnotify.py stopped"}]}],
  "Notification":     [{"hooks": [{"type": "command", "command": "python3 $HOME/.ccnotify/ccnotify.py needs"}]}]
}
```

The notifier's Continue button needs the session to be listening on a socket,
which under PRoot it only does when told to: CLAUDE_CODE_TERMUX edition v9
starts `claude --messaging-socket-path "$HOME/.claude/inbox/cc-$$.sock"`.
`ccnotify status` says whether a session is reachable.

Needs `python3`, `mpv` and Termux:API. Nothing else — all three are stdlib only.

---

# The delivery record

**G9 of `delivery-gate.md`: what is being claimed, and what is not.**

Recorded 13.9.2026, for mamc at 2015 lines, talker at 514, ccnotify at 447.
The record of 12.9.2026 is in the history of this file (commit 73d156d);
what it said about the pipe-based runner was already out of date when it was
written, and this record replaces it.

## What was run, and what it found

| | result |
|---|---|
| **Test 1** mechanism, alone | 108 assertions, pass |
| **Test 2** inside the running app | 55 checks over a real pty, against a screen model, pass |
| **Test 3** the ugly cases | 34 checks, pass |
| **Test 4** the upgrade | 25 checks, pass |
| **Test 5** every folder, every row | 7 folders, 22 apps drawn, pass (needs a pty: `script -qec … /dev/null`) |
| **Test 6** terminal handover | pass |
| **Test 7** ccnotify on a real Claude session | 17 checks, pass (after waiting out the session's 45 s startup window) |
| **G6** monkey + soak | 400 random keys, 12 start/stop cycles, pass — after the crash below was fixed |
| **gate sweep** over every app | G2 137 files · G3 28 .py, 20 launchers · G5 104 boundary calls · T2 20 of 21 run |

### mamc: folders

The first screen is folders now. Each app is filed by a keyword rule
(Commute, Transcribe & speech, Read & play, Tools; loose scripts are
Experiments; the rest is Other; orphans are Orphans), Enter opens one, ← comes
back, `/` searches every folder at once, and `c` moves an app to another
folder or a new one. A move is kept in `~/.mamc/categories.json` by the app's
path — the same key as the important flag, for the same reason. Test 4 shows a
phone that never had the file starts without it and that merely looking never
writes it.

### talker: Stop is a finished playback

mpv exits 4 when it is told to quit over its socket, which is what the Stop
button does. That was read as a failure, and the same clip was handed to
Android's player — so Stop made her start the sentence again, out of reach of
the socket. `talker.log` had said `mpv rc=4` nine times. Exit 4 is now
"stopped"; the notification buttons are Pause/Resume, Stop and Off (three is
all Android gives); and the reply is taken from the Stop hook's own
`last_assistant_message`, with the transcript as a fallback.

### ccnotify: a bubble when Claude stops or needs you

Three hooks. A reply finishing posts *Claude stopped* with its first words;
a permission or an idle prompt posts *Claude needs you*; your next prompt
clears both. Each bubble has a Continue button that pushes a message into the
session over Claude Code's cross-session messaging socket (`ccpush`), which
under PRoot exists only because the launcher now names one
(`--messaging-socket-path`, CLAUDE_CODE_TERMUX v9). Test 7 starts a real
session on a pty and proves the chain end to end: the hook fires, the bubble
is posted with the right words and buttons, a pushed message is answered by
the session, and a permission prompt raises the second bubble.

**A pushed message arrives as a message from another session, not as typed
input.** So it says who sent it ("From Marko, via the phone's notification
button: …"). It cannot approve a permission prompt; that still needs the
terminal or the Claude app, and `inputNeededNotifEnabled` /
`agentPushNotifEnabled` are on so the Claude app shows a push at the same
moments.

### The session that wrote this record was killed, and what that taught

The record of 13.9.2026 was being written when Termux killed the session
that was writing it — with the folder work uncommitted, the monkey red, and
Test 7 still running. The next session found it in the working tree. Three
things came out of that:

* **`ccnotify` bounds every Termux:API call and reaps the hung ones.**
  Twelve `termux-notification` calls were found waiting forever
  (`__skb_wait_for_more_packets`, children of pid 1, up to fifteen minutes
  old), all left by these hooks. Android 12+ counts an app's orphaned
  processes against a limit of 32 and kills the excess; a session that has
  run for hours has left far more than that behind it. Every call now runs
  under `timeout -k 5 45`, every hook first calls `reap()` (orphans older
  than two minutes whose argv names a `termux-*` script or `termux-api`;
  never the wake lock), and `ccnotify reap` does it by hand. Test 1 covers
  the selection on made-up rows and on a real orphan.
* **Process age comes from `CLOCK_BOOTTIME`**, because proot-distro binds a
  fake `/proc/uptime` that reads about two minutes forever, and the first
  version computed an age of minus three hours.
* **The Claude Code launcher takes `termux-wake-lock`** on every start
  (CLAUDE_CODE_TERMUX v10), which is the other half of staying alive.

### mamc: the monkey's crash, and the harness that could not see it

**`min(len(rows) - 1, idx + 1)` on an empty list is -1**, and `rows[-1]` of
nothing raises. Type a filter that matches no app, press Down, and the whole
program came down with `IndexError` out of `draw()`. The suite had never
pressed a key on an empty list on purpose; the monkey did, at key 290 in
one run and 381 in another. `UI.clamp()` now keeps `idx` and `top` on a row
that exists and is called from every navigation key and every redraw; Test 2
presses Down, Up, PageDown, PageUp, End, Home, Enter, i, c and d on an empty
list deliberately. And the monkey prints 1500 characters of a traceback, not
200: the first version showed `main()` and `curses.wrapper` and stopped
before the line that mattered.

**Then the new check failed three times with the program right.** curses
repaints only the cells that changed and trims a run's equal leading
characters: the screen said `tpedit`, the pty carried `pedit` (the old row
began with `tts_reader`); the header went from `9 apps match` to `1 apps
match` and the pty carried a lone `1`. Every check in Test 2 that matched a
word in the captured bytes was matching luck — it passed when the previous
frame happened to differ enough. `tests/vt.py` is a 200-line screen model
(a grid, a cursor, a scroll region, the sequences ncurses emits for xterm);
`u.screen()` is now what is on the screen, whole, and `u.expect()` looks in
both the stream and the screen. A resize does not help: `TIOCSWINSZ` on a
harness pty with no controlling process delivers no SIGWINCH at all.

Two smaller things the same afternoon: a CSI Home or End (`ESC [ H`) is
three keys to this program, the first of which is Esc, "back" — a real
terminal in keypad mode sends `ESC O H`; and Test 7's session must start
with `--settings '{"remoteControlAtStartup": false}'`, because Remote
Control coming up mid-prompt clears the conversation and eats the prompt,
and the five red checks that followed blamed the hooks.

### The sweep over every app: what was fixed

Twenty-one apps. The four the previous record left open, and what the gates
found on top:

1. **Commute could not start from its descriptor.** `~/.ma/apps/20-commute.app`
   ran a command called `commute`; the launcher had been renamed `day.commute`.
   Fixed in the descriptor. Commute now starts, serves 8082 and stops.
2. **night.commute daemonises and exits by design.** Not a fault: the runner
   already tracks the process group, and the sweep now judges *serving*
   (alive, or a server left in the group), not *alive*.
3. **maha-transcribe on 8082 "collides" with Commute.** Only if both run, and
   mamc runs one at a time. Not fixed, because there is nothing to fix; the
   run screen shows `● something is listening on <port>` when a port is taken.
4. **`test_server_modified.py` did not compile:** two literal `\n` where
   newlines belonged. Repaired. Both loose Gemini servers also called `log`
   and `logf`, names that did not exist anywhere — a NameError waiting on the
   task and voice paths. Now `logging.info`.
5. **G5: four calls with no deadline, all in openrouter** — two `requests.post`,
   an `edge-tts` subprocess, a `termux-open-url` subprocess. Given 120 s, 120 s,
   180 s, 15 s. The first version of the check reported sixteen; twelve of
   those had a timeout behind a nested paren the pattern could not see. The
   check was fixed and shown to fail on a fixture before its number was
   believed (`checking-the-checks.md` 8a).
6. **G2: keys in source.** `openrouter/app.py` carried an OpenRouter key and
   five Groq keys as literals; `.mafinder/update_pools.py` a Groq key. Moved to
   `keys.txt` and `groq_api.txt` beside them, mode 600, read at startup, same
   values — the programs behave exactly as before. **Not moved:** the same
   five Groq keys in `openrouter/maha_transcribe.html` and a Maps key in
   `.mafinder/spar-dm-finder.html`, because a browser page needs them
   client-side; every browser on the LAN can read them. Marko decides.
7. **ccnotify was listed as an app.** Its launcher names a folder in HOME,
   which is what makes something look like an app. Protected, like `talk`.
8. **mamc: `proc` assigned and never used** in the pump thread. Removed.

Originals of every app file edited are in `~/.mamc/gate-backups/`.

### Faults in the tests, which count as much as faults in the code

* **Test 7 waited for "alpha" and matched the echo of its own typing.** The
  answer must differ from the prompt: it now asks for capitals.
* **Test 7 read a "stopped" line from an earlier run** and killed the session
  before its hooks had fired. It reads the log from this run's offset only.
* **Test 7 typed a whole line in one write** and the fresh input box dropped
  it. One key at a time, after the box is on screen.
* **Test 7's push went to a person's live session.** Two sessions share one
  inbox file; the last hook to fire wins. The test now runs its session with
  a private `CCNOTIFY_DIR`. The same rule of the sweep applies: exclude your
  own sabotage — and never let it reach someone else.
* **The monkey restored two apps from the trash** (`t` then `r`) that had been
  uninstalled on purpose the day before, and could have uninstalled an ordinary
  one (`d` then `y`). The trash is set aside for its run and `y` is out of its
  keys. `magem` and `bicycle-finder` are back on the phone as a result: see
  KNOWN.
* **Test 2 matched a curses title that was only half redrawn** (" m" was
  already on screen). It matches the second line, which is new in full.
* **The G5 pattern stopped at the first closing paren** — above.
* Test 2 failed four checks when run *concurrently* with Test 7 and passed
  all fifty alone. The suite is run sequentially; two pty programs on one
  phone at once is a load test nobody asked for.

## The gates

| gate | verdict |
|---|---|
| G1 provenance | pass — this repository, clean tree at the commit this record is in |
| G2 secrets | pass for the three programs (0 key-shaped strings); the apps: 57 hits, 55 in key files by house convention, 2 in HTML pages listed under NOT claimed |
| G3 analysis | pass. All three compile with SyntaxWarning as error; pyflakes clean. The apps: 28 files compile, 0 undefined names, 27 unused-import-class messages read and left |
| G4 dead code | pass. One unused variable removed; the docstring's duplicate lines removed |
| G5 dead loops | pass. Every UI loop blocks on `getch`; every subprocess carries a timeout; the mpv socket 3 s, the push socket 4 s. The apps: 104 boundary calls, 104 with deadlines |
| G6 stress | pass, with the monkey defanged as above |
| G7 budgets | below |
| G8 upgrade | pass — Test 4, including a phone that never had categories.json |
| G9 the record | this document |

## Budgets

    discover()          3.5 s mean over 22 apps, best 2.9 s (was 1.9 s over 21) — measured with 7.5 of
                        7.6 GB RAM in use and 3.6 GB of swap; whether the code or the phone is slower is not known
    mamc --list, cold   4.0 s (was 2.4 s), same caveat
    source              mamc 2015 lines (was 1511), talker 514, ccnotify 447, tests/vt.py 210
    dependencies        stdlib only, plus Termux:API for the bubbles

## What is NOT claimed

* **No other phone has run this.** One device, one Android, one PRoot.
* **The four tests were run on the three programs here, not on the twenty-one
  apps.** The apps went through the gates a machine can run in minutes — G2,
  G3, G5 and a start-serve-stop — and the fixes above are what those found.
  Each app's own four tests are a session per app and have not been done.
* **`magem` fails the run sweep** and is not fixed: its launcher runs
  `python server.py` in `~/magem`, and there is no `server.py` there. It came
  back out of the trash today (see KNOWN).
* **The keys in the two HTML pages** are reported, not moved.
* **The bubbles themselves were seen posted only by their command line.**
  Test 7 shims `termux-notification` and reads the arguments back; whether
  Android drew them is checked by eye (one real bubble is posted at the end).
* **`ccpush` cannot approve a permission prompt.** By design of Claude Code:
  a peer message is not the user's key press.
* **The uninstall path is tested with fixtures, not with real apps.**
* **No fuzzing, no mutation testing.**

## KNOWN

* `magem` and `bicycle-finder` were restored from the trash by the monkey
  (or by someone using mamc at 08:20 — the trash keeps no log, and both are
  possible). Both were uninstalled on purpose on 12.9.2026. They are left in
  place rather than re-trashed on a guess; `d` in mamc puts either back.
* A session started from inside another Claude session has transcript saving
  off; the talker and the notifier read the reply from the hook input instead,
  so they speak and post in those sessions too.
