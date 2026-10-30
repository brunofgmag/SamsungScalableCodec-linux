#!/usr/bin/env python3
"""Find the Thumb functions of the Buds3 Pro firmware that load pointers to given strings (literal-pool ldr xrefs).

Memory map recovered from the header (cafecafe image, header size 0x1c): file offset F is executed at 0x0c01ffe4+F
(cached flash alias) and read as data at 0x2c01ffe4+F (non-cached alias).

    bes_xref.py FIRMWARE.bin "substring" ["substring" ...]
"""
import re
import sys

import numpy as np

CODE, DATA = 0x0c01ffe4, 0x2c01ffe4


def main():
    d = open(sys.argv[1], "rb").read()
    n = len(d)
    words = np.frombuffer(d[: n // 4 * 4], dtype="<u4")
    hw = [int(x) for x in np.frombuffer(d[: n // 2 * 2], dtype="<u2")]
    pos_of = {}
    for i, w in enumerate(words.tolist()):
        if DATA <= w < DATA + n:
            pos_of.setdefault(w, []).append(i * 4)
    starts = np.array(sorted(i * 2 for i, h in enumerate(hw) if (h & 0xFF00) == 0xB500 or h == 0xE92D))

    def func_of(p):
        k = int(np.searchsorted(starts, p)) - 1
        return int(starts[k]) if k >= 0 else None

    def ldr_to(L):
        out = []
        for p in range(max(0, L - 0x1200), L, 2):
            h1 = hw[p // 2]
            if (h1 & 0xF800) == 0x4800:
                if ((p + 4) & ~3) + (h1 & 0xFF) * 4 == L:
                    out.append(p)
            elif h1 in (0xF8DF, 0xF85F):
                imm = hw[p // 2 + 1] & 0xFFF
                if ((p + 4) & ~3) + (imm if h1 == 0xF8DF else -imm) == L:
                    out.append(p)
        return out

    for t in sys.argv[2:]:
        m = re.search(re.escape(t.encode()), d)
        if not m:
            print("%-48s string not found" % t)
            continue
        s0 = d.rfind(b"\0", 0, m.start()) + 1
        refs = []
        for w in (DATA + s0, DATA + m.start()):
            for L in pos_of.get(w, []):
                refs += ldr_to(L)
        fn = sorted({func_of(p) for p in refs if func_of(p) is not None})
        print("%-48s str@%#x  ldr@%s  func@%s  (addr %s)" % (
            t[:48], s0, [hex(r) for r in refs[:3]], [hex(f) for f in fn[:3]], [hex(CODE + f) for f in fn[:3]]))


if __name__ == "__main__":
    main()
