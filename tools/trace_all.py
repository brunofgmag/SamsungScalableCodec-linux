#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Compare, frame by frame, the range-coder symbols written by the Samsung encoder (emulated) with the symbols
read by the Opus-based decoder, using the same input as tools/ssc_vectors.py.

    trace_all.py ENC_LIB.so VECTORS.bin DECODE_TEST [--frames 6]
"""
import argparse
import math
import os
import re
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ssc_trace  # noqa: E402
from ssc_oracle import Oracle  # noqa: E402


def decoder_traces(binary, vec):
    env = dict(os.environ, TRACEALL="1")
    txt = subprocess.run([binary, vec, "4"], env=env, capture_output=True, text=True).stdout
    frames, cur = {}, None
    for ln in txt.splitlines():
        m = re.match(r"FRAME (\d+)", ln)
        if m:
            cur = int(m.group(1)); frames[cur] = []
            continue
        if cur is None:
            continue
        for pat, rep in ((r"^bit_logp\(val=(-?\d+), logp=(\d+)\)", r"logp \1 \2"),
                         (r"^lap\(val=(-?\d+), fs=(\d+), decay=(\d+)\)", r"lap \1 \2 \3"),
                         (r"^icdf\(s=(-?\d+), ftb=(\d+)\)", r"icdf \1 \2"),
                         (r"^uint\(fl=(\d+), ft=(\d+)\)", r"uint \1 \2"),
                         (r"^bits\(fl=(\d+), bits=(\d+)\)", r"bits \1 \2"),
                         (r"^decode\(ft=(\d+)\) -> .*", r"enc \1"),
                         (r"^decode_bin\(bits=(\d+)\) -> .*", r"bin \1")):
            if re.match(pat, ln):
                frames[cur].append(re.sub(pat, rep, ln))
                break
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("vec")
    ap.add_argument("decode_test")
    ap.add_argument("--frames", type=int, default=6)
    a = ap.parse_args()
    raw = open(a.vec, "rb").read()
    nf, fsz, ch, rate, fs = struct.unpack("<5I", raw[:20])
    dec = decoder_traces(a.decode_test, a.vec)
    o = Oracle(a.lib)
    events = []
    ssc_trace.attach(o, events)
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", fs, ch, 16, err)
    out = o.alloc(size=16384)
    n = 0
    for f in range(min(a.frames, nf)):
        pcm = bytearray()
        for _ in range(fsz):
            for c in range(ch):
                t = n / fs
                v = (6000 * math.sin(2 * math.pi * 330 * (c + 1) * t) + 3000 * math.sin(2 * math.pi * 1800 * t + c)
                     + 1500 * math.sin(2 * math.pi * 5200 * t))
                pcm += struct.pack("<i", int(v))   # int32 container
            n += 1
        events.clear()
        o.call("ssc_encode", st, o.alloc(bytes(pcm)), fsz, out, rate)
        d = dec.get(f, [])
        intra = next((int(x.split()[1]) for x in d[:6] if x.startswith("logp") and x.endswith(" 3")), None)
        # the second logp(...,3) is the intra flag; the first is the transient flag
        flags3 = [int(x.split()[1]) for x in d if x.startswith("logp") and x.endswith(" 3")]
        which = "intra" if len(flags3) > 1 and flags3[1] == 1 else "inter"
        ssc_trace.a_which = which
        try:
            fin = ssc_trace.final_stream(events, 2 * 22 if ch == 2 else 22)
            e = ssc_trace.canon(ssc_trace.collapse(fin))
        except Exception as ex:  # noqa: BLE001
            print("frame %d: could not build encoder trace (%s)" % (f, ex)); continue
        k = min(len(e), len(d))
        first = next((i for i in range(k) if e[i] != d[i]), None)
        status = "IDENTICAL (%d ops)" % len(e) if first is None and len(e) == len(d) else \
            "first difference at op %s (enc %d ops, dec %d ops)" % (first, len(e), len(d))
        print("frame %d [%s pass]: %s" % (f, which, status))
        if first is not None:
            for i in range(max(0, first - 3), min(k, first + 5)):
                print("   %s%4d  enc %-30s dec %s" % (">" if i == first else " ", i, e[i], d[i]))


if __name__ == "__main__":
    main()
