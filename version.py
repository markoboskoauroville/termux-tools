"""
version.py  --  the single number everything else reads (modules/versioning.md).

One whole number: v1, v2, v3, never a dot. Every change, however small, is a new number, bumped
by hand on every commit that touches these programs. mamc reads it with a regular expression,
from this checkout and from origin/main, so the U key never guesses at a version.

The three programs share one number because they ship together, from one repository.

v1: 13.9.2026. Everything before it is the unnumbered delivery of the same day (the record in
    README.md). mamc reads each app's live port from ~/.mantra/ports, has the U key and
    --version, and is installed by the curl line.
"""

APP_VERSION = 1
