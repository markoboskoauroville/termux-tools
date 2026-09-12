# Termux tools

Two programs built on the phone, inside Termux + PRoot.

    mamc     a Midnight Commander for every app on this phone: run one, stop it
             with Q, uninstall it into a restorable trash
    talk     Claude's replies spoken aloud in Beatrice's voice, on a code word

The environment they were built in, and everything it does differently, is
`modules/termux-proot-working.md` in MANTRA_MANIFEST. Read that first if
anything here behaves strangely.

## Install

```bash
install -m 755 mamc/mamc.py  "$HOME/.mamc/mamc.py"
install -m 755 mamc/mamc     "$PREFIX/bin/mamc"
install -m 755 talker/*.py   "$HOME/.talker/"
install -m 755 talker/talk   "$PREFIX/bin/talk"
```

The talker also needs two hooks in `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 $HOME/.talker/hook_prompt.py"}]}],
  "Stop":             [{"hooks": [{"type": "command", "command": "python3 $HOME/.talker/hook_stop.py"}]}]
}
```

Needs `python3` and `mpv`. Nothing else — both programs are stdlib only.

---

# The delivery record

**G9 of `delivery-gate.md`: what is being claimed, and what is not.**

Recorded 12.9.2026, for mamc at 1511 lines and talker at 505 lines.

## What was run, and what it found

| | result |
|---|---|
| **Test 1** mechanism, alone | 55 assertions, pass |
| **Test 2** inside the running app | 33 checks over a real pty, pass |
| **Test 3** the ugly cases | 27 checks, pass |
| **Test 4** the upgrade | 19 checks, pass |
| **G6** monkey + soak | 400 random keys, 12 start/stop cycles, pass |
| **run-and-quit sweep** | every app on the phone started and stopped |

**Nine real faults, found and fixed:**

1. **A descriptor claiming `paths=/etc` was obeyed.** Uninstalling such an app would have
   moved `/etc` and `/usr` into the trash. Found by Test 3 (HOSTILE). `App.owns()` now
   refuses any claim outside `$HOME` or a bin directory, and refuses those directories
   themselves.
2. **`for kind, w in cells` rebound the terminal width** for the rest of `draw()`. The key
   bar then measured itself against a seven-column screen and drew nothing; the detail pane
   was sliced to a negative width. Nothing raised. Found by Test 2 asking whether the footer
   was on screen.
3. **The runs column was formatted past the pane edge** and truncated away on every screen
   size — so the number the whole list is sorted by was the one number never displayed. The
   row is now built to fit, dropping columns inward as the screen narrows.
4. **Symlinked launchers lost their app** — `realpath` returns the Termux spelling of home
   and the `/root` test rejected it. `MAHA_TRANSCRIBE_TERMUX_TERMINAL` was listed with no
   command and "ran" by the wrong entry point.
5. **The talker was listed as an app** and could have been uninstalled by its own tooling.
   Now protected, with `claude`, `ma`, `mamc` and `cc`.
6. **Whitespace-only text was spoken.** It collapsed to `"."`, which is truthy, so a
   Speechify call was spent synthesising a full stop.
7. **Selecting the fourth row crashed the whole program.** An app can have no folder of its
   own — MA Player lives entirely in its bin script — and `bin_only` was added to the model
   without being added to the detail pane, so `a.path.replace(...)` hit `None`. Found by
   Marko, on the phone, not by the suite. See *What the suite missed* below.
8. **mamc listed its own source repository as an app.** `~/termux-tools` holds mamc's
   source and tests, those files mention Flask, so discovery found it and offered to
   uninstall it. A checkout with nothing installed from it is source, not an app — but the
   test cannot be "does it have `.git`", because `MAHA_TRANSCRIBE_TERMUX_TERMINAL` is a clone
   too and a real installed app. The test is whether anything was installed *from* it: a
   launcher on PATH, or an ma descriptor.
9. **A dead file and two dead symbols** — `control_daemon.py` (written for a notification
   mechanism that never worked and referenced by nothing), an unused `signal` import, an
   f-string with no placeholders. G3/G4.

**Four "failures" that were the test's fault, not the program's**, each one worth as much as
a real bug because a test that lies is worse than no test:

* arrow keys "did nothing" — the test sent `ESC [ B`; `keypad(True)` means a real terminal
  sends `ESC O B`
* six apps "failed to start" — the harness gave them no tty; with a pty, three run and quit
  on their own key
* checks passed and failed on alternate runs — fixed sleeps, now waits for the marker
* the monkey "crashed" the program — it had pressed `q`

## What the suite missed, and why

**A 33-check UI suite passed while the fourth row of the list crashed on sight.**

It pressed Down twice. MA Player is fourth. That is `checking-the-checks.md`'s central
failure wearing a new hat: walking two rows of a list and declaring the list drawn is a
check that runs nothing, and from outside it is indistinguishable from a check that found
nothing.

`tests/test5_every_row.py` now draws the detail pane for **every** app and builds every
`targets()` list, under a real pty. The rule it encodes: when a program renders a
collection, the test renders all of it. Not the first, not a sample — all of it.

**The monkey missed it too, and for a different reason worth writing down.** Replaying its
exact seed shows which rows it selected: 0, 1, 2, then 5, 6, 7 … It never landed on 4 at
all. Its key list includes PageUp and PageDown, and those jumps hopped straight over the one
row that crashes. Nine more navigation keys were swallowed while it was in filter mode.

So: **a random monkey is not coverage, and must not be reported as if it were.** 400
keypresses that happen to skip a row prove nothing about that row. The monkey is for finding
states nobody imagined; enumerating every item of a collection is a separate job, and needs a
separate test that walks the list deliberately.

## The gates

| gate | verdict |
|---|---|
| G1 provenance | **now passes** — this repository is it. It did not exist until today |
| G2 secrets | pass. No key-shaped token in either source; the talker reads keys from a file it never logs |
| G3 analysis | pass. Compiles with SyntaxWarning as error; pyflakes clean |
| G4 dead code | pass, after removing what §7 above lists |
| G5 dead loops | pass. Every UI loop blocks on `getch`; every `subprocess.run` carries a timeout; the mpv socket has a 3 s deadline |
| G6 stress | pass |
| G7 budgets | recorded below. No previous release to compare against |
| G8 upgrade | pass — Test 4 |
| G9 the record | this document |

## Budgets, recorded so the next release can be compared

    discover()          1.77 s best, 1.90 s mean, over 20 apps
    mamc --list, cold   2.37 s
    peak memory         20.1 MB
    source              55.8 KB, 1511 lines
    state on disk       56.4 KB
    dependencies        stdlib only

## What is NOT claimed

* **No other phone has run this.** Every number above is from one device, one Android
  version, one PRoot distro. `claude-code-termux.md` §7 is right that this is more of a
  limitation than it first looks.
* **The uninstall path is tested with fixtures, not with your apps.** Test 3 removes and
  restores a throwaway app. Nothing in the suite uninstalls something real, on purpose.
* **G7 has no baseline.** The budgets are a first measurement, not a comparison.
* **The run-and-quit sweep does not prove an app WORKS**, only that it starts, holds its
  port, and stops cleanly. `Commute`'s descriptor execs a command that does not exist and
  `maha-transcribe` collides with it on port 8082 — both are environment faults the sweep
  surfaced, and neither is fixed here.
* **The talker's Speechify path needs the network and a live key.** The offline fallback is
  exercised; a key that has run out of credit is not.
* **No fuzzing, no mutation testing, no formal verification** — `delivery-gate.md` §15 says
  why, and nothing here changes that argument.
