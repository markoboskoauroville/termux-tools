# claudelogin

```sh
curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/termux-tools/main/claudelogin/install.sh | bash
```

That is the whole install, in Termux. It fetches **claudelogin alone** into `~/claudelogin` (not
mamc, talk or ccnotify, and an existing `~/termux-tools` is left as it is), installs Flask, and
puts `claudelogin` on the PATH. Then type `claudelogin`.

**One word signs Claude Code in.** The sign-in link opens in Firefox by itself, and the code is
pasted into Claude for you as soon as you tap **Copy Code**.

The curl line works once this repository is public and this folder is on `main`. Until then,
through the `gh` login already on the phone, from the branch:

```sh
gh api 'repos/markoboskoauroville/termux-tools/contents/claudelogin/install.sh?ref=ccr-00a8eb51-cly4sv' -H 'Accept: application/vnd.github.raw' | CL_BRANCH=ccr-00a8eb51-cly4sv bash
```

    README.md       this: install, use, the keys
    HANDOVER.md     for the next session: what was decided, what is NOT tested
    tests/          the four tests, run_all.py

## How to use it

```
claudelogin
```

    1  starting claude auth login...
    2  the sign-in link:  (opened in Firefox, and copied, in case)
    3  sign in, then tap Copy Code on the page that shows it.
    4  code from the clipboard typed into claude, checking it...
    done  signed in.  Login successful.

       Enter starts claude in <this folder>,  q quits

You sign in on Claude's page in Firefox and tap **Copy Code**. claudelogin is watching the
clipboard, recognises the code and types it into Claude. Come back to Termux and press Enter to
start Claude Code.

**If the clipboard is not watched** (no Termux:API app), paste the code into Termux and press
Enter, or press `o` and Enter to open the page, which has a box for it.

| type, then Enter | does |
|---|---|
| `q` | quit |
| `o` | open claudelogin's own page (paste box, status) in Firefox |
| `l` | open the sign-in link again |
| `r` | start again with a new link |
| `c` | copy the link again |
| `u` | check GitHub for a newer version, and update on `y` |
| anything long | it is a code: typed into Claude |

## Why it is safe to watch the clipboard

Claude's page shows the code as `<code>#<state>`, and `<state>` is a random string that is
also inside the link this login opened. Only a clipboard text that ends in `#` plus **that**
state is sent. The link itself, a password, a code from an earlier attempt: none of them match,
and none of them are ever typed into Claude.

The page answers on 127.0.0.1 only, at port 8500 (or the next free one), and refuses any request
whose Host is not 127.0.0.1/localhost, whose Origin is another site, or that lacks the page's
own header. Nothing is written to disk except the port line in `~/.mantra/ports/claudelogin`
while it runs. The code is never logged.

## Firefox, not Chrome

Every other app on this phone opens Chrome (MANTRA_MANIFEST `termux-app.md` §5). This one
opens **Firefox**, by Marko's word for it (10.10.2026): the sign-in happens in the browser where
he is signed in to Claude. It tries `org.mozilla.firefox`, then Firefox Beta, then Nightly, and
the default browser when none of them is installed.

## Tests

```
python3 ~/termux-tools/claudelogin/tests/run_all.py
```

Four tests, each able to fail alone (MANTRA_MANIFEST `four-tests.md`): the mechanism, the real
program on a pty, the ugly cases, the upgrade. They use stand-ins in `tests/fake_bin/` for
`claude`, `termux-open-url`, the clipboard and `pm`. The stand-in `claude` prints exactly what
Claude Code 2.1.296 printed on 10.10.2026 (`tests/fixture_auth_login.repr`). What that cannot
prove is in `HANDOVER.md`.
