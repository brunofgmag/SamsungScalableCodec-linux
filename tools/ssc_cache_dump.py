#!/usr/bin/env python3
"""Identify the arrays referenced by the SSC mode struct (eBands, allocVectors, logN, window, pulse cache ...)
by dumping the struct and the head of everything it points to.

    ssc_cache_dump.py LIB.so [OUT.json]
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle, BASE  # noqa: E402


def main():
    o = Oracle(sys.argv[1])
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", 44100, 2, 16, err)
    mode = struct.unpack("<Q", o.read(st, 8))[0]
    n = 0xa0
    raw = o.read(mode, n)
    print("mode @0x%x" % mode)
    for off in range(0, n, 8):
        v = struct.unpack("<Q", raw[off:off + 8])[0]
        lo, hi = struct.unpack("<II", raw[off:off + 8])
        tag = ""
        if BASE <= v < BASE + 0x100000 or 0x20000000 <= v < 0x24000000:
            tag = " <- pointer"
        print("  +0x%02x: %016x  (%d, %d)%s" % (off, v, lo, hi, tag))


if __name__ == "__main__":
    main()
