#!/usr/bin/env python3
"""Log the arguments of every ssc_comb_filter call made by the emulated encoder (pitch pre-filter): periods, gains, tapsets.

    ssc_combtrace.py LIB.so [--frames 14] [--f0 190] [--rate 229000]
"""
import argparse
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_SP, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4,  # noqa: E402
                                 UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--fs", type=int, default=44100)
    ap.add_argument("--frames", type=int, default=14)
    ap.add_argument("--f0", type=float, default=190.0)
    ap.add_argument("--rate", type=int, default=229000)
    a = ap.parse_args()
    o = Oracle(a.lib)
    calls = []

    def hook(uc, addr, size, _):
        r = lambda reg: uc.reg_read(reg)  # noqa: E731
        sp = r(UC_ARM64_REG_SP)
        stack = struct.unpack("<4Q", bytes(uc.mem_read(sp, 32)))
        s32 = lambda v: v - (1 << 32) if v & 0x80000000 else v  # noqa: E731
        calls.append(dict(T0=s32(r(UC_ARM64_REG_X2) & 0xFFFFFFFF), T1=s32(r(UC_ARM64_REG_X3) & 0xFFFFFFFF),
                          N=r(UC_ARM64_REG_X4) & 0xFFFFFFFF, g0=r(UC_ARM64_REG_X5) & 0xFFFFFFFF,
                          g1=r(UC_ARM64_REG_X6) & 0xFFFFFFFF, tapset0=r(UC_ARM64_REG_X7) & 0xFFFFFFFF,
                          stack=[x & 0xFFFFFFFF for x in stack]))

    o.uc.hook_add(UC_HOOK_CODE, hook, begin=o.syms["ssc_comb_filter"], end=o.syms["ssc_comb_filter"])
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", a.fs, 2, 16, err)
    out = o.alloc(size=16384)
    n = 0
    for f in range(a.frames):
        pcm = bytearray()
        for _ in range(864):
            t = n / a.fs
            v = sum(8000.0 / k * math.sin(2 * math.pi * a.f0 * k * t) for k in range(1, 12))
            for c in range(2):
                pcm += struct.pack("<i", int(v))
            n += 1
        calls.clear()
        o.call("ssc_encode", st, o.alloc(bytes(pcm)), 864, out, a.rate)
        print("frame %2d: %d comb_filter call(s)" % (f, len(calls)))
        for c in calls:
            print("     T0=%d T1=%d N=%d g0=0x%x g1=0x%x tapset0=%d stack=%s" % (
                c["T0"], c["T1"], c["N"], c["g0"], c["g1"], c["tapset0"], c["stack"]))


if __name__ == "__main__":
    main()
