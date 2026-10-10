# HANDOVER: termux-tools (13.9.2026)

Version: `version.py`, one whole number (v1 is the first numbered one). Repository:
`markoboskoauroville/termux-tools`, private until Marko runs
`gh repo edit markoboskoauroville/termux-tools --visibility public --accept-visibility-change-consequences`
(the history was audited clean on 13.9.2026); the curl line below works only after that. No installer file: the artefact is the checkout, the
curl line in README.md installs it, and mamc's `u` key updates it. The reasons for what is here are
in the delivery records at the bottom of README.md, newest first.

## What it is

Three programs built on the phone, inside Termux + PRoot, in one repository because they ship
together: **mamc**, a Midnight Commander for every app on this phone; **talk**, Claude's replies
spoken aloud; **ccnotify**, a notification when Claude stops or needs you. The environment they
run in and everything it does differently is `modules/termux-proot-working.md` in MANTRA_MANIFEST.

**claudelogin/** (10.10.2026) is a fourth, separate app parked in this repository until it has
its own (the session could not create one). It is self-contained: `claudelogin/HANDOVER.md`.
Nothing above installs, updates or tests it, and its tests are `claudelogin/tests/run_all.py`.

## Where it runs

    mamc                        ~/termux-tools/mamc/mamc.py, through the wrappers $PREFIX/bin/mamc and
                                ~/.local/bin/mamc that install-termux.sh writes with the checkout's
                                absolute path. A git pull IS the update.
    ~/.mamc/                    mamc's state: runs.json, important.json, categories.json, logs/, trash/.
                                No program lives there any more (the copy the first installs left is
                                removed by the link step).
    ~/.talker, ~/.ccnotify      COPIES of talker/*.py and notify/ccnotify.py, refreshed by the link
                                step, because the hooks in ~/.claude/settings.json name those paths.
    ~/.mantra/ports/<command>   the live port registry every port-picking app writes (ports.md §3);
                                mamc reads it and believes a line only when the port answers.

## The model

An app is a folder in $HOME (or under a launcher's path, even below a skipped directory like
storage/), joined to its launcher in `$PREFIX/bin` or `~/.local/bin` by the paths the launcher
names, in any of four spellings of home, and to its `~/.ma/apps` descriptor by command. Nothing is
deleted: uninstall moves every piece to `~/.mamc/trash` with a manifest. The important flag and the
folder choice are stored against the app's path. One app runs at a time, owned by the Runner on a
pty and stopped from outside by its process group.

## The screens

    folders          the first screen; Enter opens one, ← back, / filters every folder at once
    the list         ↑↓, Enter run, o open the page in Chrome, i info, l log, d uninstall, c move,
                     ! flag, t trash, r rescan, u update, q quit
    the run screen   IS the app's terminal: every key reaches the app; Ctrl-C or Esc Esc asks
                     "stop it and come back? [y/N]". A curses app is handed the real terminal
                     instead (its own q is the way back).
    the update       u: fetch, "installed v1 → available v2", y, git pull --ff-only, the link
                     step, restart on the same pid. Also `mamc --update` without the screen.

## The dangerous parts, as they are now

* `d` on an important app asks twice, the second time for the typed word `remove`.
* The run screen forwards keys to a real app: a monkey must run `mamc --no-run`.
* `install-termux.sh link` rewrites the four wrappers on the PATH and removes `~/.mamc/mamc.py`;
  the tests point it elsewhere with `TT_BIN`, `TT_LOCAL_BIN`, `TT_HOME`.
* The `u` key pulls from origin/main; a private repository or a stale token gives a sentence, never
  a password prompt (`GIT_TERMINAL_PROMPT=0`).

## The files

    version.py            APP_VERSION, read by pattern from the checkout and from origin/main
    install-termux.sh     the curl line's target; `link` is the local step the u key runs
    mamc/mamc.py          the whole program, stdlib only
    mamc/mamc             the checkout-relative wrapper (the installer writes the absolute ones)
    talker/, notify/      talk and ccnotify, with their hook scripts
    tests/                test1..test7, g6_stress, the sweep, vt.py (a screen model), fixture_repo.py
                          (a GitHub on disk, one commit ahead, for the u key)
    README.md             how to install, the hooks, the delivery records

## What has never been proven

* The curl line on a phone that never had these programs: this phone had them before the line
  existed. The installer's link step is tested; the clone step is the next real install.
* Another phone, another Android, another PRoot.
* The talker and the notifier through the link step's copies on a live session: Test 7 covers the
  hooks, not the installer refreshing them under a running Claude.
* Chrome on a phone without Chrome: `has_package` falls back to the default browser, tested with a
  package that does not exist, not with Chrome removed.
