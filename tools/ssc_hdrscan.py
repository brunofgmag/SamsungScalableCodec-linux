#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""See how the signal-dependent header bits (byte 2 bits 2-3, byte 3 bits 5-7) react to the input.

    ssc_hdrscan.py LIB.so [--bits 16] [--fs 44100] [--rate 229000]
"""
import argparse
import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402


def frame_pcm(kind, amp, n0, fs, shift, frame=864):
    rng = random.Random(n0)
    out = bytearray()
    for i in range(frame):
        t = (n0 + i) / fs
        if kind == "sine":
            v = amp * math.sin(2 * math.pi * 440 * t)
        elif kind == "noise":
            v = rng.uniform(-amp, amp)
        elif kind == "loud-lf":
            v = amp * math.sin(2 * math.pi * 60 * t)
        else:  # "hf": strong 15 kHz
            v = amp * math.sin(2 * math.pi * 15000 * t)
        s = int(v) << shift
        out += struct.pack("<ii", s, s)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--bits", type=int, default=16)
    ap.add_argument("--fs", type=int, default=44100)
    ap.add_argument("--rate", type=int, default=229000)
    a = ap.parse_args()
    shift = 0 if a.bits == 16 else 8
    top = 32767
    print("bits=%d fs=%d rate=%d   (hdr byte2 = seq<<4 | flags, byte3)" % (a.bits, a.fs, a.rate))
    for kind in ("sine", "noise", "loud-lf", "hf"):
        row = []
        for amp in (0, 1, 8, 64, 512, 2048, 6000, 12000, 20000, 32000):
            o = Oracle(a.lib)
            err = o.alloc(size=4)
            st = o.call("ssc_encoder_create", a.fs, 2, a.bits, err)
            out = o.alloc(size=16384)
            seen = set()
            for f in range(4):
                o.call("ssc_encode", st, o.alloc(frame_pcm(kind, min(amp, top), f * 864, a.fs, shift)), 864, out, a.rate)
                h = o.read(out, 4)
                seen.add((h[2] & 0x0f, h[3]))
            row.append("%5d:%s" % (amp, ",".join("%x/%02x" % s for s in sorted(seen))))
        print("%-8s %s" % (kind, "  ".join(row)))


if __name__ == "__main__":
    main()
