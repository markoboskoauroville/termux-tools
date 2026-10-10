#!/usr/bin/env python3
"""Test 3: the ugly cases. Closes "it works when the world behaves".

EMPTY, MALFORMED, HOSTILE, TWICE, OUT OF ORDER, ABSENT, NEVER ANSWERS (four-tests.md), each
against the real program on a pty.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Session, check, finish, http  # noqa: E402

print("Test 3: the ugly cases")


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie is dead too
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(") ")[1][0] != "Z"
    except OSError:
        return False


# -- MALFORMED: a code for another login in the clipboard is not sent ---------
s = Session()
try:
    s.wait_for("watching the clipboard")
    state = s.wait_file("state").strip()
    s.set_clip("Ab3dEf6hIj9kLmN0pQ#someotherloginsstate")
    s.pump(1.5)
    check("a code from another login is not sent", "typed into claude" not in s.text())
    s.set_clip("my password is hunter2")
    s.pump(1.0)
    check("an ordinary clipboard is not sent", "typed into claude" not in s.text())
    s.set_clip(s.code())
    check("the right one still is, afterwards", bool(s.wait_for("signed in")))
finally:
    s.close()

# -- EMPTY / MALFORMED in the terminal, then a WRONG code, then r -------------
s = Session(drop=("termux-clipboard-get",))
try:
    s.wait_for("paste the code here")
    s.send("hello")
    check("a short word is not a code", bool(s.wait_for("that is not a code")))
    s.send("   ")
    s.pump(0.5)
    check("blank Enter does nothing", "typed into claude" not in s.text())
    first = s.wait_file("state").strip()
    s.send("abcdefghij#wrongstate")
    check("a wrong code: claude's own failure is shown", bool(s.wait_for(r"not signed in +Login failed: Request failed with status code 400")), s.text()[-300:])
    check("and our code is not what it reports", "abcdefghij" not in s.text().split("not signed in")[-1])
    check("it offers r", bool(s.wait_for("r tries again")))
    s.send("abcdefghij#" + first)
    check("a paste after failing is refused, not typed into nothing", bool(s.wait_for("no login is waiting for a code")))
    os.remove(os.path.join(s.dir, "state"))
    s.send("r")
    second = s.wait_file("state").strip()
    check("r starts a new login with a new link", second and second != first)
    s.wait_for(r"starting again(.|\n)*opened in Firefox")
    check("and opens the new link", s.wait_file("opened").count("\n") >= 2 and second in s.file("opened").splitlines()[-1])
    s.send(s.code())
    check("the new code signs in", bool(s.wait_for(r"done +signed in")))
    s.send("q")
    check("q after signing in exits 0", s.wait_exit(5) == 0)
finally:
    s.close()

# -- TWICE: the same code from the page twice ---------------------------------
s = Session(drop=("termux-clipboard-get",))
try:
    s.wait_for("paste the code here")
    port = s.port()
    code = s.code()
    st1, r1 = http(port, "/api/code", {"code": code})
    st2, r2 = http(port, "/api/code", {"code": code})
    check("twice: the first is sent", r1.get("ok") is True, r1)
    check("twice: the second is refused with a reason", r2.get("ok") is False and r2.get("error") in ("a code is already being checked", "already signed in"), r2)
    check("twice: signed in once", bool(s.wait_for("signed in")) and s.text().count("typed into claude") == 1)
finally:
    s.close()

# -- HOSTILE: the guard, over HTTP ---------------------------------------------
s = Session(drop=("termux-clipboard-get",))
try:
    s.wait_for("paste the code here")
    port = s.port()
    code = s.code()
    st, _ = http(port, "/api/code", {"code": code}, headers={"Content-Type": "application/json"})
    check("no header: 403", st == 403, st)
    st, _ = http(port, "/api/code", {"code": code}, host="evil.example:%d" % port)
    check("a rebinding Host: 403", st == 403, st)
    st, _ = http(port, "/api/code", {"code": code}, headers={"X-Claudelogin": "1", "Content-Type": "application/json", "Origin": "https://evil.example"})
    check("another site's Origin: 403", st == 403, st)
    st, _ = http(port, "/", headers={}, host="evil.example:%d" % port)
    check("even the page, under a strange Host: 403", st == 403, st)
    st, r = http(port, "/api/code", {"code": "abcdefgh\u0003ijkl#x"})
    check("a Ctrl-C inside a code is refused", r.get("ok") is False and "not a code" in r.get("error", ""), r)
    st, r = http(port, "/api/code", {"code": 12345678})
    check("a number instead of text is refused", r.get("ok") is False, r)
    st, r = http(port, "/api/code", {})
    check("an empty body is refused", r.get("ok") is False, r)
    check("none of it reached claude", "typed into claude" not in s.text())
    check("and the login still waits", http(port, "/api/state")[1].get("state") == "waiting")
finally:
    s.close()

# -- ABSENT: no Firefox, Firefox that does not answer, no opener at all --------
s = Session(packages="com.android.chrome")
try:
    check("no Firefox: the default browser", bool(s.wait_for("opened in the default browser")))
    check("no Firefox: no package asked for", s.wait_file("opened").strip().split()[-1].startswith("https://"))
finally:
    s.close()
s = Session(packages="org.mozilla.fenix")
try:
    check("only Firefox Nightly: that one", bool(s.wait_for("opened in Firefox")) and s.wait_file("opened").strip().endswith("org.mozilla.fenix"))
finally:
    s.close()
s = Session(env={"FAKE_OPEN_FAIL": "1"})
try:
    check("am fails with exit 0: read, and the default browser tried", bool(s.wait_for("opened in the default browser")) and s.file("opened").count("\n") == 2, s.file("opened"))
finally:
    s.close()
s = Session(drop=("termux-open-url",))
try:
    check("no opener at all: said, and the link is on screen", bool(s.wait_for("no browser could be opened")) and "oauth/authorize" in s.text())
    s.wait_for("watching the clipboard")
    s.set_clip(s.code())
    check("and it still signs in", bool(s.wait_for("signed in")))
finally:
    s.close()

# -- ABSENT: no claude, claude that fails before the link ----------------------
s = Session(env={"CLAUDELOGIN_CLAUDE": "no-such-claude"})
try:
    check("no claude: one sentence, exit 1", s.wait_exit(5) == 1 and "no `no-such-claude` on the PATH" in s.text(), s.text())
finally:
    s.close()
s = Session(env={"FAKE_EXIT_EARLY": "1"})
try:
    check("claude fails before the link: its words", bool(s.wait_for(r"not signed in +Error: something went wrong before the link")), s.text()[-300:])
finally:
    s.close()

# -- ABSENT: no Flask ------------------------------------------------------------
s = Session(pyflags=["-S"])   # no site-packages: no Flask, no waitress
try:
    check("no Flask: the page is off and it says how to get it", bool(s.wait_for("page off +no Flask: pip install flask waitress")))
    s.wait_for("watching the clipboard")
    s.set_clip(s.code())
    check("no Flask: it still signs in", bool(s.wait_for("signed in")))
finally:
    s.close()

# -- NEVER ANSWERS: claude that never shows a link; a clipboard that hangs -------
s = Session(env={"FAKE_SILENT": "1", "CLAUDELOGIN_URL_TIMEOUT": "1"})
try:
    check("a silent claude is named after the deadline, with what it said", bool(s.wait_for(r"has shown no link in 1 s.*starting\.\.\.", 6)), s.text()[-300:])
    pid = int(s.wait_file("pid"))
    check("the silent claude is running", alive(pid))
    s.send("q")
    check("q quits with 1", s.wait_exit(5) == 1, s.status)
    time.sleep(0.5)
    check("and claude is stopped, not left behind", not alive(pid))
finally:
    s.close()
s = Session(env={"FAKE_CLIP_HANG": "1"})
try:
    s.wait_for("watching the clipboard")
    t0 = time.time()
    s.send(s.code())
    check("a hung clipboard does not hold the terminal paste", bool(s.wait_for("signed in", 5)) and time.time() - t0 < 5)
finally:
    s.close()

# -- TWICE: two at once, two ports, two registries --------------------------------
a = Session()
b = Session()
try:
    a.wait_for("watching the clipboard")
    b.wait_for("watching the clipboard")
    pa, pb = a.port(), b.port()
    check("two at once: two ports", pa and pb and pa != pb, (pa, pb))
    check("two at once: both answer", http(pa, "/api/state")[0] == 200 and http(pb, "/api/state")[0] == 200)
    if pa == 8500:
        check("two at once: the second says why it moved", "(8500 was taken)" in b.text())
finally:
    a.close()
    b.close()

sys.exit(finish("Test 3"))
