"""theme.py - the one colour scheme every termux-tools screen uses: an orange terminal, 1980s.

Orange characters on a black screen, selection and key caps in reverse. mamc and claudelogin read
it; a new screen takes its colours from here and never picks its own (Marko, 12.10.2026).

colors() gives role -> (foreground, background) for the terminal curses is running on: the real
orange (256-colour 214) where the terminal has 256 colours, the 8-colour yellow, which is amber
on most screens, where it has not.
"""
import curses

ROLES = ("norm", "sel", "head", "dim", "ok", "warn", "bar", "key", "imp", "impsel")


def colors():
    black = curses.COLOR_BLACK
    if getattr(curses, "COLORS", 8) >= 256:
        orange, deep, dim, gold, red = 214, 208, 172, 220, 196
    else:
        orange = deep = dim = gold = curses.COLOR_YELLOW
        red = curses.COLOR_RED
    return {
        "norm": (orange, black),
        "sel": (black, orange),        # the selected row: a lit bar
        "head": (black, deep),
        "dim": (dim, black),
        "ok": (gold, black),
        "warn": (red, black),
        "bar": (orange, black),
        "key": (black, orange),        # key caps
        "imp": (deep, black),
        "impsel": (black, deep),
    }
