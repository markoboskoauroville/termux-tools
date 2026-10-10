# HANDOVER: claudelogin (10.10.2026)

Version: `version.py`, one whole number, v1. It lives in `termux-tools/claudelogin/` for now,
because the session that built it could not create a repository (GitHub answered 403). The folder
is self-contained: its own installer, version, tests and documents. **It is meant to become its
own repository**: once Marko creates one (an empty `CLAUDE_LOGIN_TERMUX`, say), move the folder
with `git subtree split --prefix=claudelogin` and change `REPO_URL` in `install.sh`, the
`claudelogin/` part of the install path, and the `gh api` line in README.md.

## What it is

`claudelogin`, one word in Termux: it runs `claude auth login` on its own pty, reads the
sign-in link out of what claude prints, opens it in Firefox and copies it, then watches the
clipboard until the code that belongs to THIS login appears, and types it into claude. When claude
exits 0 it says "signed in", and Enter execs `claude` in the same folder.

## What was decided, and why

* **Firefox, not Chrome.** Marko, 10.10.2026: "It's going to kick always the Firefox browser on
  this phone." It is the only app on the phone that does not follow `termux-app.md` §5 (Chrome),
  and the manifest says so beside that rule. Packages tried: `org.mozilla.firefox`,
  `org.mozilla.firefox_beta`, `org.mozilla.fenix`; then the default browser.
* **`claude auth login`, not the `/login` inside a session.** It is a plain prompt and not the
  interactive screen, so a pty can drive it. A whole line sent in one write is read as one line;
  the warning about dropped keys (`claude-code-termux.md` §11) applies to the interactive input
  box, not to this prompt (measured: the 400 for a wrong code came back, so the line arrived).
* **Measured output, 10.10.2026, Claude Code 2.1.296:**

      Opening browser to sign in…
      If the browser didn't open, visit: ESC]8;;<url>BEL ESC[94m<url> ESC[39m ESC]8;;BEL
      Paste code here if prompted >
      (a wrong code)  Login failed: Request failed with status code 400     exit 1

  The URL is printed whole on an 80-column pty; the pty is opened 1000 columns wide anyway, so a
  later version that wraps cannot cut it.
* **The code is recognised by `#<state>`**: the `state` parameter of the link this run opened.
  This is what makes watching the clipboard safe, and it is the reason a code from an earlier
  attempt is never sent.
* **The page is 127.0.0.1, port 8500** (ports.md: the 8500 block, first free above 8000) with the
  three localguard checks. A login code is a credential.
* **`BROWSER=true`** in claude's environment, so claude's own attempt to open a browser does
  nothing and only one tab opens.
* **Nothing is stored.** No log of claude's output, because the pty echoes the code into it.

## The files

    claudelogin.py        the whole program: the Login (pty), the opener, the clipboard watch,
                          the Flask page, the console, the u key
    claudelogin.html      the page (AGY look), served by the program
    version.py            APP_VERSION
    install.sh            the gh/curl line's target; `link` is the step the u key runs
    tests/                test1..test4, run_all.py, harness.py, fake_bin/ (stand-ins),
                          fixture_auth_login.repr (the bytes claude really printed)

## Test results, 10.10.2026, in the cloud sandbox (not the phone)

    test1_mechanism   45 passed
    test2_real        30 passed      clipboard, page, terminal, no-tty, --no-page, --version
    test3_ugly        42-43 passed   (one check runs only when the first copy holds 8500)
    test4_upgrade     21 passed      installer over a stale command, both homes, v1->v2 by the
                                     installer, v2->v3 by the u key on the same pid and port

Seven mutations were made by hand (link-cut guard, state check, header check, SO_REUSEADDR, am
output reading, port line on exec, rename in the installer); each one turned a test red. The page
was driven in headless Chromium at 390 px: no script errors, no sideways scroll.

## What has NEVER been proven (the Termux runtime is not reachable from the cloud)

* **A real sign-in.** The code from Claude's page, typed into a real `claude auth login`, ending
  in "signed in". The success line is the stand-in's guess (`Login successful.`); the program
  decides by claude's exit status, not by that line, so the wording does not matter.
* **The phone's `claude` launcher** (CLAUDE_CODE_TERMUX, proot-distro) passing `auth login`
  through and keeping a pty. If it treats its first argument as a folder (`claude-code-termux.md`
  §3), the arguments may need a different form. First real run is Test 2.
* **Firefox via `termux-open-url <url> org.mozilla.firefox`**, and that the code page's Copy
  button puts `<code>#<state>` on the Android clipboard as text.
* **`termux-clipboard-get` speed on the phone**: each call is 3-5 s (proot-working §3); the code
  can take that long to be noticed.
* Firefox on the phone showing the local page; the layout was seen only in Chromium.

**Marko's test, one line:** type `claudelogin` in Termux, sign in, tap Copy Code, come back to
Termux. Expected: "signed in", then Enter starts claude.
