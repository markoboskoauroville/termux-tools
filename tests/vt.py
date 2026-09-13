"""A terminal small enough to read: what the screen SAYS after curses drew.

curses repaints only the cells that changed, and trims a run's equal leading
characters. So the byte stream a test captures from a pty can carry "pedit"
when the screen says "tpedit", or a lone "1" when the header changed from
"9 apps match" to "1 apps match". Matching words in that stream is matching
luck: a check passes when the previous screen happened to differ enough, and
fails when it did not (Test 2, 13.9.2026, three such failures in a row).

This models the screen itself: a grid of cells, a cursor, a scroll region,
and the handful of control sequences ncurses emits for xterm. Everything it
does not understand is ignored, which is the right failure for a test
harness -- an unknown sequence draws nothing rather than raising. stdlib only.
"""
import re

CSI = re.compile(rb"\x1b\[([?>]?)([0-9;]*)([a-zA-Z@`])")


class Screen:
    def __init__(self, rows=24, cols=80):
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.y = self.x = 0
        self.top, self.bottom = 0, rows - 1        # scroll region
        self.pending = b""

    # -- text --------------------------------------------------------------
    def line(self, y):
        return "".join(self.grid[y]).rstrip()

    def text(self):
        return "\n".join(self.line(y) for y in range(self.rows))

    # -- primitives --------------------------------------------------------
    def _clamp(self):
        self.y = max(0, min(self.rows - 1, self.y))
        self.x = max(0, min(self.cols - 1, self.x))

    def _scroll_up(self, n=1):
        for _ in range(n):
            del self.grid[self.top]
            self.grid.insert(self.bottom, [" "] * self.cols)

    def _scroll_down(self, n=1):
        for _ in range(n):
            del self.grid[self.bottom]
            self.grid.insert(self.top, [" "] * self.cols)

    def _put(self, ch):
        if self.x >= self.cols:            # wrap
            self.x = 0
            self._lf()
        self.grid[self.y][self.x] = ch
        self.x += 1

    def _lf(self):
        if self.y == self.bottom:
            self._scroll_up()
        elif self.y < self.rows - 1:
            self.y += 1

    def _erase_line(self, mode):
        row = self.grid[self.y]
        if mode == 0:
            for i in range(self.x, self.cols):
                row[i] = " "
        elif mode == 1:
            for i in range(0, min(self.x + 1, self.cols)):
                row[i] = " "
        else:
            row[:] = [" "] * self.cols

    def _erase_display(self, mode):
        if mode == 0:
            self._erase_line(0)
            for y in range(self.y + 1, self.rows):
                self.grid[y] = [" "] * self.cols
        elif mode == 1:
            self._erase_line(1)
            for y in range(0, self.y):
                self.grid[y] = [" "] * self.cols
        else:
            self.grid = [[" "] * self.cols for _ in range(self.rows)]

    # -- the sequences ncurses uses ------------------------------------------
    def _csi(self, private, params, final):
        p = [int(v) if v else 0 for v in params.split(";")] if params else []
        n = p[0] if p and p[0] else 1
        if private:                        # ?1049h, ?1l, ?25l ... : modes
            return
        if final in ("H", "f"):
            self.y = (p[0] - 1) if len(p) > 0 and p[0] else 0
            self.x = (p[1] - 1) if len(p) > 1 and p[1] else 0
        elif final == "A":
            self.y -= n
        elif final == "B":
            self.y += n
        elif final == "C":
            self.x += n
        elif final == "D":
            self.x -= n
        elif final == "G" or final == "`":
            self.x = n - 1
        elif final == "d":
            self.y = n - 1
        elif final == "J":
            self._erase_display(p[0] if p else 0)
        elif final == "K":
            self._erase_line(p[0] if p else 0)
        elif final == "X":                 # erase n chars
            for i in range(self.x, min(self.cols, self.x + n)):
                self.grid[self.y][i] = " "
        elif final == "P":                 # delete n chars
            row = self.grid[self.y]
            del row[self.x:self.x + n]
            row += [" "] * (self.cols - len(row))
        elif final == "@":                 # insert n blanks
            row = self.grid[self.y]
            row[self.x:self.x] = [" "] * n
            del row[self.cols:]
        elif final == "L":                 # insert lines at cursor
            if self.top <= self.y <= self.bottom:
                for _ in range(n):
                    del self.grid[self.bottom]
                    self.grid.insert(self.y, [" "] * self.cols)
        elif final == "M":                 # delete lines at cursor
            if self.top <= self.y <= self.bottom:
                for _ in range(n):
                    del self.grid[self.y]
                    self.grid.insert(self.bottom, [" "] * self.cols)
        elif final == "S":
            self._scroll_up(n)
        elif final == "T":
            self._scroll_down(n)
        elif final == "r":
            t = (p[0] - 1) if len(p) > 0 and p[0] else 0
            b = (p[1] - 1) if len(p) > 1 and p[1] else self.rows - 1
            if 0 <= t < b < self.rows:
                self.top, self.bottom = t, b
                self.y = self.x = 0
        # m (colours), h/l (modes), t (window ops), c, n: nothing to draw
        self._clamp()

    def feed(self, data):
        data = self.pending + data
        self.pending = b""
        i, n = 0, len(data)
        while i < n:
            b = data[i]
            if b == 0x1b:
                if i + 1 >= n:
                    self.pending = data[i:]
                    return
                nxt = data[i + 1]
                if nxt == 0x5b:                                  # CSI
                    m = CSI.match(data, i)
                    if not m:
                        if n - i < 24:
                            self.pending = data[i:]
                            return
                        i += 2
                        continue
                    self._csi(m.group(1), m.group(2).decode(), m.group(3).decode())
                    i = m.end()
                    continue
                if nxt == 0x5d:                                  # OSC ... BEL
                    j = data.find(b"\x07", i)
                    if j < 0:
                        self.pending = data[i:]
                        return
                    i = j + 1
                    continue
                if nxt in (0x28, 0x29):                          # charset
                    i += 3
                    continue
                if nxt == 0x4d:                                  # RI
                    if self.y == self.top:
                        self._scroll_down()
                    else:
                        self.y -= 1
                elif nxt == 0x44:                                # IND
                    self._lf()
                elif nxt == 0x45:                                # NEL
                    self.x = 0
                    self._lf()
                # =, >, 7, 8, c, H and the rest: nothing to draw
                i += 2
                continue
            if b == 0x0d:
                self.x = 0
            elif b == 0x0a:
                self._lf()
            elif b == 0x08:
                self.x = max(0, self.x - 1)
            elif b == 0x09:
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif b < 0x20 or b == 0x7f:
                pass
            elif b < 0x80:
                self._put(chr(b))
            else:                                                # utf-8
                ln = 2 if b < 0xe0 else 3 if b < 0xf0 else 4
                if i + ln > n:
                    self.pending = data[i:]
                    return
                self._put(data[i:i + ln].decode("utf-8", "replace"))
                i += ln
                continue
            i += 1
