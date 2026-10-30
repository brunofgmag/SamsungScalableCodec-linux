#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Trace the range-coder calls made by Samsung's SSC encoder for one frame.

Hooks the exported (S23: ssc_ec_*, Android 12: ec_*) range coder entry points inside the emulated
library and prints every symbol the encoder emits, in order. That is the bitstream *syntax* of a frame.

    ssc_trace.py LIB.so [--fs 48000] [--bits 16] [--rate 128000] [--frame 864] [--frames 1] [--signal sine|noise|mix]
"""
import argparse
import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,  # noqa: E402
                                 UC_ARM64_REG_X2, UC_ARM64_REG_X3)

# (suffix, argument names). `value` for laplace is read through a pointer.
FUNCS = [
    ("ec_enc_init", ("buf", "size")),
    ("ec_encode", ("fl", "fh", "ft")),
    ("ec_encode_bin", ("fl", "fh", "bits")),
    ("ec_enc_bit_logp", ("val", "logp")),
    ("ec_enc_icdf", ("s", "icdf", "ftb")),
    ("ec_enc_uint", ("fl", "ft")),
    ("ec_enc_bits", ("fl", "bits")),
    ("ec_laplace_encode", ("valueptr", "fs", "decay")),
    ("ec_enc_done", ()),
    ("ec_enc_shrink", ("size",)),
    ("ec_enc_patch_initial_bits", ("val", "nbits")),
]


def find(o, suffix):
    for name in (suffix, "ssc_" + suffix):
        if name in o.syms:
            return name, o.syms[name]
    return None, None


def attach(o, events):
    uc = o.uc
    by_addr = {}
    for suffix, args in FUNCS:
        name, addr = find(o, suffix)
        if addr is not None:
            by_addr[addr] = (suffix, args)

    def hook(uc_, address, size, _):
        suffix, args = by_addr[address]
        regs = [uc_.reg_read(r) & 0xFFFFFFFF for r in
                (UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)]
        vals = dict(zip(args, regs))
        if suffix == "ec_laplace_encode":
            ptr = uc_.reg_read(UC_ARM64_REG_X1)
            vals = {"val": struct.unpack("<i", bytes(uc_.mem_read(ptr, 4)))[0],
                    "fs": regs[1], "decay": regs[2]}
        if suffix == "ec_enc_init":
            vals = {"buf": uc_.reg_read(UC_ARM64_REG_X1), "size": regs[1]}
        events.append((suffix, vals))

    for addr in by_addr:
        uc.hook_add(UC_HOOK_CODE, hook, begin=addr, end=addr)
    return [by_addr[a][0] for a in by_addr]


def make_pcm(kind, n, ch, fs, nbytes, rng, bits=16):
    nbytes = 4                       # int32 container (see ssc_preemphasis)
    shift = 0 if bits == 16 else 8
    out = bytearray()
    for i in range(n):
        for c in range(ch):
            if kind == "tones":   # same signal as tools/ssc_vectors.py
                t = i / fs
                v = (6000 * math.sin(2 * math.pi * 330 * (c + 1) * t)
                     + 3000 * math.sin(2 * math.pi * 1800 * t + c) + 1500 * math.sin(2 * math.pi * 5200 * t))
            elif kind == "sine":
                v = 8000 * math.sin(2 * math.pi * 440 * (c + 1) * i / fs)
            elif kind == "noise":
                v = rng.uniform(-6000, 6000)
            else:
                v = (5000 * math.sin(2 * math.pi * 440 * (c + 1) * i / fs)
                     + 2500 * math.sin(2 * math.pi * 3300 * i / fs)
                     + rng.uniform(-1200, 1200))
            out += struct.pack("<i", int(v) << shift)[:nbytes]
    return bytes(out)


def fmt(ev):
    name, v = ev
    short = {"ec_enc_bit_logp": "bit_logp", "ec_enc_icdf": "icdf", "ec_enc_uint": "uint",
             "ec_enc_bits": "bits", "ec_laplace_encode": "laplace", "ec_encode_bin": "enc_bin",
             "ec_encode": "enc", "ec_enc_done": "done", "ec_enc_init": "init",
             "ec_enc_shrink": "shrink", "ec_enc_patch_initial_bits": "patch"}[name]
    return "%s(%s)" % (short, ", ".join("%s=%s" % (k, ("0x%x" % x) if k in ("buf", "icdf", "valueptr") else x)
                                        for k, x in v.items()))


def collapse(events):
    """laplace is always followed by the enc_bin it issues internally: show them as one entry."""
    out, i = [], 0
    while i < len(events):
        n, v = events[i]
        if n == "ec_laplace_encode" and i + 1 < len(events) and events[i + 1][0] == "ec_encode_bin":
            out.append("lap(val=%d, fs=%d, decay=%d)" % (v["val"], v["fs"], v["decay"]))
            i += 2
            continue
        out.append(fmt(events[i]))
        i += 1
    return out


def _skip_pass(events, start, nlaps):
    """Index just after the nlaps laplace calls that follow `start` (and the enc_bin issued by the last one)."""
    n, i = 0, start
    while n < nlaps:
        if events[i][0] == "ec_laplace_encode":
            n += 1
        i += 1
    while events[i][0] == "ec_encode_bin":
        i += 1
    return i


def final_stream(events, nlaps):
    """Drop the discarded energy pass. The encoder tries intra and inter coarse-energy coding on copies of the
    range coder state and keeps one of them; both trials show up in the hook trace."""
    flags = [i for i, e in enumerate(events) if e[0] == "ec_enc_bit_logp" and e[1]["logp"] == 3]
    p1 = flags[1]                       # intra flag written by the first (intra) trial
    p2 = _skip_pass(events, p1 + 1, nlaps)   # inter flag written by the second trial
    end2 = _skip_pass(events, p2 + 1, nlaps)
    return events[:p1] + (events[p1:p2] if a_which == "intra" else events[p2:end2]) + events[end2:]


def canon(lines):
    import re
    out = []
    for ln in lines:
        m = re.match(r"(\w+)\((.*)\)", ln)
        if not m or not m.group(2):
            continue
        name = m.group(1)
        if name not in ("bit_logp", "lap", "icdf", "uint", "bits", "enc", "enc_bin"):
            continue
        args = dict(kv.split("=") for kv in m.group(2).split(", "))
        if name == "bit_logp":
            out.append("logp %s %s" % (args["val"], args["logp"]))
        elif name == "lap":
            out.append("lap %s %s %s" % (args["val"], args["fs"], args["decay"]))
        elif name == "icdf":
            out.append("icdf %s %s" % (args["s"], args["ftb"]))
        elif name == "uint":
            out.append("uint %s %s" % (args["fl"], args["ft"]))
        elif name == "bits":
            out.append("bits %s %s" % (args["fl"], args["bits"]))
        elif name == "enc":
            out.append("enc %s" % args["ft"])
        elif name == "enc_bin":
            out.append("bin %s" % args["bits"])
    return out


a_which = "intra"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--ch", type=int, default=2)
    ap.add_argument("--bits", type=int, default=16)
    ap.add_argument("--rate", type=int, default=128000)
    ap.add_argument("--frame", type=int, default=864)
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--signal", default="mix")
    ap.add_argument("--max", type=int, default=400, help="max events to print per frame")
    ap.add_argument("--skip", type=int, default=0, help="skip the first N printed entries")
    ap.add_argument("--canon", default="", help="write the canonical final-stream trace (trial pass removed) to this file")
    ap.add_argument("--which", default="intra", help="which energy pass ended up in the stream: intra | inter")
    ap.add_argument("--collapse", action="store_true", help="merge laplace+enc_bin and runs of identical uint/bits")
    a = ap.parse_args()

    o = Oracle(a.lib)
    events = []
    hooked = attach(o, events)
    print("hooked:", " ".join(hooked))
    size = o.call("ssc_encoder_get_size", a.ch, a.fs) & 0xFFFFFFFF
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", a.fs, a.ch, a.bits, err)
    out = o.alloc(size=16384)
    rng = random.Random(1)
    nbytes = a.bits >> 3
    for f in range(a.frames):
        events.clear()
        pcm = make_pcm(a.signal, a.frame, a.ch, a.fs, nbytes, rng, a.bits)
        pp = o.alloc(pcm)
        r = o.call("ssc_encode", st, pp, a.frame, out, a.rate) & 0xFFFFFFFF
        data = o.read(out, r)
        print("\n=== frame %d: ret=%d bytes, header %s, %d range-coder calls ===" % (f, r, data[:4].hex(" "), len(events)))
        counts = {}
        for e in events:
            counts[e[0]] = counts.get(e[0], 0) + 1
        print("counts:", ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
        global a_which
        a_which = a.which
        if a.canon:
            fin = final_stream(events, 2 * 22 if a.ch == 2 else 22)
            with open(a.canon, "w") as fh:
                fh.write("\n".join(canon(collapse(fin))) + "\n")
            print("canonical trace written to", a.canon)
        lines = collapse(events) if a.collapse else [fmt(e) for e in events]
        for i, ln in enumerate(lines[a.skip:a.skip + a.max], a.skip):
            print("%4d  %s" % (i, ln))
        if len(lines) > a.skip + a.max:
            print("... %d more" % (len(lines) - a.skip - a.max))


if __name__ == "__main__":
    sys.exit(main())
