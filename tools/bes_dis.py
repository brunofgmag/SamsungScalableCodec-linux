#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Disassemble Thumb-2 code of the Buds3 Pro firmware with radare2, annotating literal-pool string pointers.

    bes_dis.py FIRMWARE.bin START_FILE_OFFSET COUNT_INSTRUCTIONS
"""
import re
import subprocess
import sys

CODE, DATA = 0x0c01ffe4, 0x2c01ffe4


def main():
    fw, start, count = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3])
    d = open(fw, "rb").read()
    addr = CODE + start
    cmd = ["r2", "-q", "-a", "arm", "-b", "16", "-m", hex(CODE), "-e", "scr.color=0", "-e", "asm.bytes=false",
           "-e", "asm.comments=true", "-e", "asm.cmt.right=true", "-e", "asm.var=false", "-e", "asm.lines=false",
           "-c", "pd %d @ %s" % (count, hex(addr)), fw]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    for ln in out.splitlines():
        m = re.search(r"\[(0x[0-9a-f]+)\]", ln)
        note = ""
        if "ldr" in ln and m:
            lit = int(m.group(1), 16) - CODE
            if 0 <= lit + 4 <= len(d):
                w = int.from_bytes(d[lit:lit + 4], "little")
                if DATA <= w < DATA + len(d):
                    s = d[w - DATA:w - DATA + 70].split(b"\0")[0]
                    if len(s) >= 4 and all(32 <= c < 127 for c in s):
                        note = '   ; "%s"' % s.decode()
                else:
                    note = "   ; =%#x" % w
        print(ln.rstrip() + note)


if __name__ == "__main__":
    main()
