#!/usr/bin/env python3
"""Differential test of the PVQ enumeration: Samsung's ssc_encode_pulses vs Opus' encode_pulses.

    pvq_compare.py LIB.so OUT.txt [--cases 80]
Each line of OUT.txt: N K y0..y(N-1) <hex of the range coder bytes written by the Samsung function>
"""
import argparse
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402


def rand_pulses(rng, n, k):
    y = [0] * n
    for _ in range(k):
        y[rng.randrange(n)] += rng.choice([-1, 1])
    # |y|_1 must equal k: opposite signs on the same position would cancel, so redo with a sign per position
    y = [0] * n
    sign = [rng.choice([-1, 1]) for _ in range(n)]
    for _ in range(k):
        y[rng.randrange(n)] += 1
    return [sign[i] * y[i] for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("out")
    ap.add_argument("--cases", type=int, default=80)
    a = ap.parse_args()
    rng = random.Random(5)
    lines = []
    for _ in range(a.cases):
        n, k = rng.choice([2, 3, 4, 6, 8, 12, 16, 24, 32]), rng.randint(1, 9)
        y = rand_pulses(rng, n, k)
        o = Oracle(a.lib)
        enc = o.alloc(size=256)
        o.uc.mem_write(enc, bytes(256))
        buf = o.alloc(bytes(64), 64)
        o.call("ssc_ec_enc_init", enc, buf, 64)
        yp = o.alloc(struct.pack("<%di" % n, *y), 4 * n)
        o.call("ssc_encode_pulses", yp, n, k, enc)
        o.call("ssc_ec_enc_done", enc)
        lines.append("%d %d %s %s" % (n, k, " ".join(map(str, y)), o.read(buf, 64).hex()))
    open(a.out, "w").write("\n".join(lines) + "\n")
    print("wrote %d cases" % len(lines))


if __name__ == "__main__":
    main()
