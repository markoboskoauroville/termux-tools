"""
version.py  --  the single number everything else reads (modules/versioning.md).

One whole number: v1, v2, v3, never a dot. Every change, however small, is a new number, bumped
by hand on every commit that touches these programs. mamc reads it with a regular expression,
from this checkout and from origin/main, so the U key never guesses at a version.

The three programs share one number because they ship together, from one repository.

v1: 13.9.2026. Everything before it is the unnumbered delivery of the same day (the record in
    README.md). mamc reads each app's live port from ~/.mantra/ports, has the U key and
    --version, and is installed by the curl line.
v2: 12.10.2026. claudelogin: logs out, then logs in through a private browser tab so the
    browser cannot silently reuse the previous account. A full-screen app with mamc's key
    bar: o opens the login page in Chrome (mamc's open_page), p a private tab, c copies the
    link, v pastes the code from the clipboard into the login, s status, r again, q quit.
v3: 12.10.2026. The orange terminal: theme.py is the one colour scheme (orange on black, 1980s),
    read by mamc and claudelogin. claudelogin's o signs the browser out of claude.ai, waits, then
    opens the login: Chrome did not honour a private tab from another app, so it logged straight
    back into the old account.
"""

APP_VERSION = 3
