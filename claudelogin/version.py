"""
version.py  --  the single number everything else reads (modules/versioning.md).

One whole number: v1, v2, v3, never a dot. Every change, however small, is a new number, bumped
by hand on every commit that touches claudelogin. The U key reads it with a regular expression,
from this checkout and from origin/main, so it never guesses at a version.

v1: 10.10.2026. claudelogin: `claude auth login` on a pty, the link opened in Firefox and copied,
    the code caught from the clipboard by its shape (it ends in #<the link's state>), or pasted
    into the page or the terminal; Enter afterwards starts claude.
v2: 10.10.2026. The installer fetches claudelogin ALONE into ~/claudelogin (a sparse checkout:
    the rest of termux-tools is never downloaded, ~/termux-tools is never touched); the curl line
    is the first thing in README.md.
"""

APP_VERSION = 2
