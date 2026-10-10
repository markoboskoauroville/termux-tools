#!/usr/bin/env python3
"""Test 2: the real program, through its entry point, on a pty. Closes "the logic is right but
nothing calls it".

Driven the way a person does it: the clipboard is written as Claude's Copy button would, the page
is posted to as the page's own script does, the code is typed into the terminal. The stand-in
claude is the measured output (fake_bin/claude); the real Android, Firefox and Anthropic are not
here, and HANDOVER.md says so.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Session, check, finish, http  # noqa: E402

print("Test 2: the real program")

# -- A: the clipboard path, then Enter starts claude --------------------------
work = os.path.join(os.path.dirname(os.path.abspath(__file__)))
s = Session(cwd=work)
try:
    check("A banner shows the version", bool(s.wait_for(r"claudelogin v\d+")))
    check("A the page is announced on 127.0.0.1", bool(s.wait_for(r"page +http://127\.0\.0\.1:\d+")))
    port = s.port()
    check("A claude auth login was started", bool(s.wait_for("the sign-in link")) and s.file("argv").strip() == "auth login", s.file("argv"))
    opened = s.wait_file("opened")
    check("A the link was opened in Firefox", "oauth/authorize" in opened and opened.strip().endswith("org.mozilla.firefox"), opened)
    check("A the console says Firefox", bool(s.wait_for("opened in Firefox")))
    check("A the link was copied to the clipboard", bool(s.wait_for("copied")) and s.file("clip").startswith("https://claude.com/cai/oauth/authorize"))
    check("A the opened link is the one in the clipboard", opened.split()[0] == s.file("clip").strip())
    reg = os.path.join(s.home, ".mantra", "ports", "claudelogin")
    check("A the live port is in ~/.mantra/ports", os.path.exists(reg) and open(reg).read().strip() == str(port))
    st, page = http(port, "/", headers={})
    check("A the page answers 200 with the version", st == 200 and "claudelogin" in page and "{{VERSION}}" not in page, st)
    st, fav = http(port, "/favicon.svg", headers={})
    check("A the favicon answers", st == 200 and "<svg" in fav)
    st, state = http(port, "/api/state")
    check("A the page sees the login waiting", st == 200 and state.get("state") == "waiting" and state.get("browser") == "Firefox", state)
    check("A the console is waiting for the code", bool(s.wait_for("watching the clipboard")))
    s.set_clip(s.code())   # what tapping Copy Code on Claude's page does
    check("A the code was caught from the clipboard", bool(s.wait_for(r"code from the clipboard typed into claude", 10)))
    check("A signed in", bool(s.wait_for(r"done +signed in\. +Login successful\.", 10)), s.text()[-400:])
    st, state = http(port, "/api/state")
    check("A the page sees signed in", state.get("state") == "signed_in", state)
    check("A it offers to start claude", bool(s.wait_for("Enter starts claude in")))
    s.send("")
    check("A Enter started claude, in the same folder", s.wait_file("started") == work, s.file("started"))
    check("A it exits 0", s.wait_exit(5) == 0, s.status)
    check("A the port line is gone", not os.path.exists(reg))
finally:
    s.close()

# -- B: the page path ---------------------------------------------------------
s = Session()
try:
    s.wait_for("watching the clipboard")
    port = s.port()
    st, r = http(port, "/api/code", {"code": "  " + s.code() + "\n"})
    check("B the page's code is accepted", st == 200 and r.get("ok") is True, r)
    check("B signed in from the page", bool(s.wait_for(r"code from the page typed into claude")) and bool(s.wait_for("signed in")))
    s.send("q")
    check("B q quits with 0 once signed in", s.wait_exit(5) == 0, s.status)
finally:
    s.close()

# -- B2: the page's clipboard button --------------------------------------------
s = Session(env={"CLAUDELOGIN_CLIP_EVERY": "3600"})   # the watcher reads once, then sleeps
try:
    s.wait_for("watching the clipboard")
    port = s.port()
    s.set_clip(s.code())
    st, r = http(port, "/api/clipboard", {})
    check("B2 the clipboard button sends it", r.get("ok") is True and bool(s.wait_for("signed in")), r)
finally:
    s.close()

# -- C: the terminal path -------------------------------------------------------
s = Session(drop=("termux-clipboard-get",))
try:
    check("C without Termux:API it says to paste by hand", bool(s.wait_for("paste the code here and press Enter")))
    s.send(s.code())
    check("C the pasted code goes in", bool(s.wait_for(r"code from the terminal typed into claude")))
    check("C signed in", bool(s.wait_for(r"done +signed in")))
finally:
    s.close()

# -- D: no terminal (started from a script): it exits by itself ---------------
s = Session(tty=False)
try:
    s.wait_for("watching the clipboard")
    s.set_clip(s.code())
    check("D with no terminal it signs in and exits 0", s.wait_exit(15) == 0 and "signed in" in s.text(), (s.status, s.text()[-300:]))
finally:
    s.close()

# -- E: --no-page and --version -----------------------------------------------
s = Session(args=["--no-page"])
try:
    s.wait_for("watching the clipboard")
    check("E --no-page serves nothing", s.port() is None and "page " not in s.text().split("1  starting")[0].replace("claudelogin", ""))
    s.set_clip(s.code())
    check("E and still signs in", bool(s.wait_for("signed in")))
finally:
    s.close()
s = Session(args=["--version"])
try:
    check("E --version", s.wait_exit(5) == 0 and bool(s.wait_for(r"(?m)^claudelogin v\d+\s*$")), s.text())
finally:
    s.close()

sys.exit(finish("Test 2"))
