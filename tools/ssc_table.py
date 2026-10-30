#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Tabulate what the emulated SSC encoder produces for every (bits, fs, bitrate): packet length and the 4 header bytes.

    ssc_table.py LIB.so [--frames 3]
"""
import argparse
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402


def run(lib, fs, bits, rate, frames, frame=864, ch=2):
    o = Oracle(lib)
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", fs, ch, bits, err)
    e = struct.unpack("<i", o.read(err, 4))[0]
    if st == 0:
        return "create failed (err=%d)" % e
    out = o.alloc(size=16384)
    shift = 0 if bits == 16 else 8
    n, res = 0, []
    for _ in range(frames):
        pcm = bytearray()
        for _ in range(frame):
            v = int((6000 * math.sin(2 * math.pi * 440 * n / fs) + 2500 * math.sin(2 * math.pi * 3100 * n / fs)) * (1 << shift))
            pcm += struct.pack("<ii", v, v)
            n += 1
        r = o.call("ssc_encode", st, o.alloc(bytes(pcm)), frame, out, rate) & 0xFFFFFFFF
        r = r - (1 << 32) if r & 0x80000000 else r
        res.append((r, o.read(out, 4).hex(" ") if r > 0 else "-"))
    return " | ".join("len=%d hdr=%s" % x for x in res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--bits", default="16,24,34")
    ap.add_argument("--fs", default="44100,48000,96000")
    ap.add_argument("--rates", default="64000,87000,96000,128000,160000,192000,229000,256000,320000")
    a = ap.parse_args()
    for bits in map(int, a.bits.split(",")):
        for fs in map(int, a.fs.split(",")):
            for rate in map(int, a.rates.split(",")):
                try:
                    r = run(a.lib, fs, bits, rate, a.frames)
                except Exception as ex:  # noqa: BLE001
                    r = "FAULT %s" % type(ex).__name__
                print("bits=%2d fs=%5d rate=%6d: %s" % (bits, fs, rate, r), flush=True)


if __name__ == "__main__":
    main()
