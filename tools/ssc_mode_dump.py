#!/usr/bin/env python3
"""Dump the CELT-like mode (band layout etc.) that the SSC encoder state points to.

    ssc_mode_dump.py LIB.so [--fs 48000]
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--ch", type=int, default=2)
    a = ap.parse_args()
    o = Oracle(a.lib)
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", a.fs, a.ch, 16, err)
    mode = struct.unpack("<Q", o.read(st, 8))[0]
    raw = o.read(mode, 0x60)
    print("mode ptr 0x%x" % mode)
    # layout observed in the SSC library (differs from Opus' CELTMode: no preemph[4] inline)
    Fs, overlap, nb, eff = struct.unpack("<4i", raw[:16])
    eb_ptr = struct.unpack("<Q", raw[16:24])[0]
    nbShort, shortSz, nbAlloc = struct.unpack("<3i", raw[24:36])
    alloc_ptr, logn_ptr, win_ptr = struct.unpack("<3Q", raw[40:64])
    mdct_n, mdct_shift = struct.unpack("<2i", raw[64:72])
    print("Fs=%d overlap=%d nbEBands=%d effEBands=%d" % (Fs, overlap, nb, eff))
    print("nbShortMdcts=%d shortMdctSize=%d nbAllocVectors=%d mdct.n=%d mdct.maxshift=%d" % (nbShort, shortSz, nbAlloc, mdct_n, mdct_shift))
    ebands = struct.unpack("<%dh" % (nb + 1), o.read(eb_ptr, 2 * (nb + 1)))
    print("eBands:", list(ebands))
    alloc = o.read(alloc_ptr, nbAlloc * nb)
    print("allocVectors (%dx%d):" % (nbAlloc, nb))
    for i in range(nbAlloc):
        print("  ", list(alloc[i * nb:(i + 1) * nb]))
    print("logN:", list(struct.unpack("<%dh" % nb, o.read(logn_ptr, 2 * nb))))
    print("window[0:8]:", list(struct.unpack("<8h", o.read(win_ptr, 16))), " window[%d-8:%d]:" % (overlap, overlap),
          list(struct.unpack("<8h", o.read(win_ptr + 2 * (overlap - 8), 16))))


if __name__ == "__main__":
    main()
