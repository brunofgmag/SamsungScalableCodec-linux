#!/usr/bin/env python3
"""Differential test of the SSC range coder against Opus' entenc.c.

Generates random symbol sequences, runs them through the emulated ssc_ec_* functions, writes the sequence
and the resulting bytes so that work/ref/build/rc_replay (Opus' coder) can replay and compare them.

    rc_compare.py LIB.so OUTDIR [--cases 50] [--seed 1]
"""
import argparse
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402

SPREAD_ICDF = bytes([25, 23, 2, 0])
TRIM_ICDF = bytes([126, 124, 119, 109, 87, 41, 19, 9, 4, 2, 0])


# (fs, decay) pairs as seen in the encoder's own laplace calls (random pairs can violate fl+fs <= 32768)
LAP_PAIRS = [(2816, 11392), (8064, 7296), (9472, 5248), (10752, 5312), (11776, 5248), (13184, 3968),
             (12288, 4608), (12288, 4288), (12928, 4672), (13696, 4608), (14464, 3520), (15104, 3328),
             (16000, 3328), (17280, 3136), (17536, 2496), (20096, 2048), (18560, 1856), (23552, 832)]


KINDS = ["logp", "logp", "bin", "enc", "uint", "bits", "icdf", "lap", "lap"]


def gen_ops(rng, n, kinds=None):
    ops = []
    for _ in range(n):
        k = rng.choice(kinds or KINDS)
        if k == "logp":
            ops.append(("logp", rng.randint(0, 1), rng.randint(1, 15)))
        elif k == "bin":
            fl = rng.randint(0, 32766)
            ops.append(("bin", fl, fl + rng.randint(1, 32768 - fl), 15))
        elif k == "enc":
            ft = rng.randint(2, 4000)
            fl = rng.randint(0, ft - 1)
            ops.append(("enc", fl, fl + rng.randint(1, ft - fl), ft))
        elif k == "uint":
            ft = rng.choice([2, 5, 22, 100, 300, 5000, 70000, 1 << 20])
            ops.append(("uint", rng.randint(0, ft - 1), ft))
        elif k == "bits":
            nb = rng.randint(1, 16)
            ops.append(("bits", rng.randint(0, (1 << nb) - 1), nb))
        elif k == "icdf":
            if rng.random() < 0.5:
                ops.append(("icdf", rng.randint(0, 3), 5, 0))
            else:
                ops.append(("icdf", rng.randint(0, 10), 7, 1))
        else:
            fs, decay = rng.choice(LAP_PAIRS)
            ops.append(("lap", rng.choice([0, 0, 1, -1, 2, -2, 3, -4, 7, -29]), fs, decay))
    return ops


def run_oracle(o, ops, size, tabs):
    enc = o.alloc(size=256)
    buf = o.alloc(bytes(size), size)
    for i in range(256):
        pass
    o.uc.mem_write(enc, bytes(256))
    o.call("ssc_ec_enc_init", enc, buf, size)
    for op in ops:
        k = op[0]
        if k == "logp":
            o.call("ssc_ec_enc_bit_logp", enc, op[1], op[2])
        elif k == "bin":
            o.call("ssc_ec_encode_bin", enc, op[1], op[2], op[3])
        elif k == "enc":
            o.call("ssc_ec_encode", enc, op[1], op[2], op[3])
        elif k == "uint":
            o.call("ssc_ec_enc_uint", enc, op[1], op[2])
        elif k == "bits":
            o.call("ssc_ec_enc_bits", enc, op[1], op[2])
        elif k == "icdf":
            o.call("ssc_ec_enc_icdf", enc, op[1], tabs[op[3]], op[2])
        elif k == "lap":
            v = o.alloc(struct.pack("<i", op[1]), 4)
            o.call("ssc_ec_laplace_encode", enc, v, op[2], op[3])
    o.call("ssc_ec_enc_done", enc)
    return o.read(buf, size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("outdir")
    ap.add_argument("--cases", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--kinds", default="", help="comma list restricting the op types, e.g. logp or bits,enc")
    ap.add_argument("--maxops", type=int, default=40)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    rng = random.Random(a.seed)
    for c in range(a.cases):
        o = Oracle(a.lib)
        tabs = [o.alloc(SPREAD_ICDF, 4), o.alloc(TRIM_ICDF, 11)]
        ops = gen_ops(rng, rng.randint(1, a.maxops), a.kinds.split(',') if a.kinds else None)
        size = 400
        out = run_oracle(o, ops, size, tabs)
        with open(os.path.join(a.outdir, "case%03d.txt" % c), "w") as f:
            f.write("%d\n" % size)
            for op in ops:
                f.write(" ".join(str(x) for x in op) + "\n")
            f.write("END\n%s\n" % out.hex())
    print("wrote %d cases to %s" % (a.cases, a.outdir))


if __name__ == "__main__":
    main()
