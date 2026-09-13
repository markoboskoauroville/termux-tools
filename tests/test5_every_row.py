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
import os
import sys

spec = importlib.util.spec_from_file_location("mamc", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mamc", "mamc.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def run(scr):
    ui = m.UI(scr)
    ui.reload()
    bad = []
    # the first screen: every folder row
    folders = list(ui.filtered)
    for i, f in enumerate(folders):
        ui.idx = i
        try:
            ui.draw()
        except Exception as e:
            bad.append((f"[{f.name}]", f"{type(e).__name__}: {e}"))
    # then inside every folder, every app row
    apps_drawn, pathless = 0, []
    for f in folders:
        ui.open_folder(f)
        for i in range(len(ui.filtered)):
            ui.idx = i
            a = ui.filtered[i]
            apps_drawn += 1
            if a.path is None:
                pathless.append(a.name)
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
        ui.close_folder()
    if apps_drawn != len(ui.apps):
        bad.append(("(count)", f"drew {apps_drawn} app rows but {len(ui.apps)} apps exist"))
    return bad, len(folders), apps_drawn, pathless


bad, nf, n, pathless = curses.wrapper(run)
print(f"drew every row: {nf} folders, {n} apps")
print(f"of which have no folder: {pathless or 'none'}")
for name, err in bad:
    print(f"  FAIL {name}: {err}")
print("all rows clean" if not bad else f"{len(bad)} rows crashed")
sys.exit(1 if bad else 0)
