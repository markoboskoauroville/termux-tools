#!/usr/bin/env python3
"""Test 1: the mechanism, alone. Closes "the logic is wrong".

The link finder is fed the bytes Claude Code 2.1.296 really printed on a pty (10.10.2026,
fixture_auth_login.repr), not a string written to agree with the parser.
"""
import ast
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claudelogin as cl  # noqa: E402
from harness import check, finish  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "fixture_auth_login.repr")) as f:
    RAW = ast.literal_eval(f.read()).decode("utf-8")

print("Test 1: the mechanism")

# -- the link ---------------------------------------------------------------
url = cl.find_login_url(RAW)
check("the link is found in the measured output", bool(url) and url.startswith("https://claude.com/cai/oauth/authorize?"), repr(url))
check("it is the plain copy, no escape left in it", url and "\x1b" not in url and "\x07" not in url)
check("it ends at the state, not in the colour code", url and url.endswith("state=ghXhvO8C8UNM3DxrSYJO1U1XnkreVFPDljfXdI57dwI"), url)
check("state read from it", cl.url_state(url) == "ghXhvO8C8UNM3DxrSYJO1U1XnkreVFPDljfXdI57dwI")
# a pty read can stop inside the link: no link until something follows it
cut = RAW[:RAW.index("&state=") + 12]
check("a link cut mid-read is not taken", cl.find_login_url(cut) is None, repr(cl.find_login_url(cut)))
plain_cut = cl.strip_ansi(RAW).split("\n")[1][:300]
check("a plain link at the very end of the text is not taken", cl.find_login_url(plain_cut) is None)
check("a link that is not a sign-in link is ignored",
      cl.find_login_url("see https://claude.com/docs now\n") is None)
check("nothing in, nothing out", cl.find_login_url("") is None and cl.url_state("") == "")
check("strip_ansi removes OSC 8 and CSI", cl.strip_ansi("\x1b]8;;http://x\x07\x1b[94mA\x1b[39m\x1b]8;;\x07") == "A")

# -- the code ---------------------------------------------------------------
st = "ghXhvO8C8UNM3DxrSYJO1U1XnkreVFPDljfXdI57dwI"
good = "Xy7_aB3-cD9eF0gH1iJ2#" + st
check("the code for this login is recognised", cl.looks_like_code(good, st))
check("with the spaces and newline a paste brings", cl.looks_like_code("  " + good + "\n", st))
check("a code for ANOTHER login is refused", not cl.looks_like_code("Xy7_aB3-cD9eF0gH1iJ2#otherstate", st))
check("the link itself is refused", not cl.looks_like_code(url, st))
check("the state alone is refused", not cl.looks_like_code("#" + st, st))
check("a short head is refused (7 chars)", not cl.looks_like_code("abcdefg#" + st, st))
check("8 chars is enough", cl.looks_like_code("abcdefgh#" + st, st))
check("no state known, nothing is recognised", not cl.looks_like_code(good, ""))
check("a password in the clipboard is refused", not cl.looks_like_code("hunter2!", st))
check("an escape inside refuses the whole paste", cl.clean_code("abc\x1b[Adef#x") == "")
check("a Ctrl-C inside refuses the whole paste", cl.clean_code("abcd\x03efgh") == "")
check("empty and enormous are refused", cl.clean_code("") == "" and cl.clean_code("a" * 5000) == "")
check("not a string is refused", cl.clean_code(None) == "" and cl.clean_code(12345678) == "")
check("internal line breaks are taken out", cl.clean_code("abcd\nefgh") == "abcdefgh")

# -- what claude said afterwards --------------------------------------------
after = RAW + good + "\r\nLogin failed: Request failed with status code 400\r\n\x1b[?25h"
check("the failure line is read", cl.outcome_line(after, good) == "Login failed: Request failed with status code 400", cl.outcome_line(after, good))
check("our own echoed code is never the message", cl.outcome_line(RAW + good + "\r\n", good) == "")
check("the success line is read", cl.outcome_line(RAW + good + "\r\nLogin successful.\r\n", good) == "Login successful.")

# -- the guard --------------------------------------------------------------
P = 8500
check("guard: the page from 127.0.0.1", cl.request_allowed("127.0.0.1:8500", None, None, "/", P))
check("guard: localhost too", cl.request_allowed("localhost:8500", "http://localhost:8500", "1", "/api/state", P))
check("guard: a rebinding Host is refused", not cl.request_allowed("evil.example:8500", None, "1", "/api/state", P))
check("guard: 0.0.0.0 is not a loopback name", not cl.request_allowed("0.0.0.0:8500", None, "1", "/", P))
check("guard: the wrong port is refused", not cl.request_allowed("127.0.0.1:8501", None, "1", "/", P))
check("guard: another site's Origin is refused", not cl.request_allowed("127.0.0.1:8500", "https://evil.example", "1", "/api/code", P))
check("guard: the API without the header is refused", not cl.request_allowed("127.0.0.1:8500", None, None, "/api/code", P))
check("guard: the page needs no header", cl.request_allowed("127.0.0.1:8500", None, None, "/favicon.svg", P))

# -- the opener's reading of am ---------------------------------------------
check("am: a clean start is success", not cl.opener_failed(0, "Starting: Intent { act=android.intent.action.VIEW }"))
check("am: an error printed with exit 0 is failure", cl.opener_failed(0, "Error: Activity not started, unable to resolve Intent"))
check("am: brought to the front is success", not cl.opener_failed(0, "Warning: Activity not started, its current task has been brought to the front"))
check("am: a non-zero exit is failure", cl.opener_failed(1, ""))

# -- the version ------------------------------------------------------------
check("version: a whole number is read", cl.read_version("APP_VERSION = 12  # x\n") == 12)
check("version: a dotted number is not a version", cl.read_version("APP_VERSION = 1.2\n") == 0)
check("version: this checkout has one", cl.APP_VERSION >= 1)

# -- the port ---------------------------------------------------------------
# a block of our own, chosen at random each run: a fixed one can hold a TIME_WAIT left by an
# earlier run whose server lacked SO_REUSEADDR, which really does block a bind (ports.md §4)
import random  # noqa: E402
base = random.randrange(40000, 60000, 100)
s1, p1 = cl.bind_port(base, 16)
check("port: the preferred one when free", p1 == base, p1)
s2, p2 = cl.bind_port(base, 16)
check("port: the next one when the preferred is taken", p2 == base + 1, p2)
s1.close(); s2.close()
# TIME_WAIT: serve, connect, server closes first, then try again
srv, p3 = cl.bind_port(base, 16)
c = socket.create_connection(("127.0.0.1", p3))
a, _ = srv.accept()
a.close(); c.close(); srv.close()
s4, p4 = cl.bind_port(base, 16)
check("port: a port in TIME_WAIT is free", p4 == base, p4)
s4.close()
held = [cl.bind_port(base, 16)[0] for _ in range(16)]
s5, p5 = cl.bind_port(base, 16)
check("port: sixteen taken, the system's number", not (base <= p5 < base + 16) and p5 > 0, p5)
for h in held + [s5]:
    h.close()

sys.exit(finish("Test 1"))
