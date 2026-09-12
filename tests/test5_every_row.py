#!/usr/bin/env python3
"""Draw the detail pane for EVERY app, not just the first one.

This exists because mamc crashed on the fourth row of the list and a 33-check
UI suite said it was fine. The suite pressed Down twice. MA Player is fourth,
it has no folder of its own, and a.path was None.

The lesson is not "handle None". It is that walking two rows of a list and
declaring the list drawn is a check that runs nothing, in the sense
checking-the-checks.md means. Every row, every time.
"""
import curses
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("mamc", "/root/.mamc/mamc.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def run(scr):
    ui = m.UI(scr)
    ui.reload()
    bad = []
    for i in range(len(ui.filtered)):
        ui.idx = i
        a = ui.filtered[i]
        try:
            ui.draw()
        except Exception as e:
            bad.append((a.name, f"{type(e).__name__}: {e}"))
        # and the pane the info key opens, built from the same object
        try:
            lines = [a.name]
            if a.path:
                lines.append(a.path)
            [str(t) for t in a.targets()]
        except Exception as e:
            bad.append((a.name + " (info)", f"{type(e).__name__}: {e}"))
    return bad, len(ui.filtered), [a.name for a in ui.filtered if a.path is None]


bad, n, pathless = curses.wrapper(run)
print(f"drew every row: {n} apps")
print(f"of which have no folder: {pathless or 'none'}")
for name, err in bad:
    print(f"  FAIL {name}: {err}")
print("all rows clean" if not bad else f"{len(bad)} rows crashed")
sys.exit(1 if bad else 0)
