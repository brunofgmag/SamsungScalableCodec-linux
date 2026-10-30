#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Generate reference test vectors with the emulated Samsung encoder.

File layout (little endian): u32 nframes, u32 frame_size, u32 channels, u32 bitrate, u32 fs,
then per frame: s16 pcm[frame_size*channels], u16 packet_len, u8 packet[packet_len].
    ssc_vectors.py LIB.so OUT.bin [--fs 44100] [--rate 229000] [--frames 40]
"""
import argparse
import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("out")
    ap.add_argument("--fs", type=int, default=44100)
    ap.add_argument("--ch", type=int, default=2)
    ap.add_argument("--rate", type=int, default=229000)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--frame", type=int, default=864)
    ap.add_argument("--signal", default="tones", help="tones | noise")
    a = ap.parse_args()

    o = Oracle(a.lib)
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", a.fs, a.ch, 16, err)
    out = o.alloc(size=16384)
    rng = random.Random(7)
    n = 0
    blob = struct.pack("<5I", a.frames, a.frame, a.ch, a.rate, a.fs)
    for _ in range(a.frames):
        pcm = bytearray()
        for _ in range(a.frame):
            for c in range(a.ch):
                if a.signal == "noise":
                    v = rng.uniform(-8000, 8000)
                elif a.signal == "hf18":        # content in the last band (17.2-20.8 kHz) plus a low tone
                    t = n / a.fs
                    v = 6000 * math.sin(2 * math.pi * 18000 * t + c) + 3000 * math.sin(2 * math.pi * 440 * t)
                elif a.signal == "sweep":       # log chirp 100 Hz -> 20 kHz over the whole file
                    t = n / a.fs
                    T = a.frames * a.frame / a.fs
                    ph = 2 * math.pi * 100 * T / math.log(200) * (math.exp(math.log(200) * t / T) - 1)
                    v = 8000 * math.sin(ph + c)
                else:
                    t = n / a.fs
                    v = (6000 * math.sin(2 * math.pi * 330 * (c + 1) * t)
                         + 3000 * math.sin(2 * math.pi * 1800 * t + c)
                         + 1500 * math.sin(2 * math.pi * 5200 * t))
                pcm += struct.pack("<h", int(v))
            n += 1
        # the encoder wants one int32 per sample (value in the low bits), see ssc_preemphasis: out = in << 12
        pcm32 = b"".join(struct.pack("<i", x) for x in struct.unpack("<%dh" % (a.frame * a.ch), bytes(pcm)))
        pp = o.alloc(pcm32)
        r = o.call("ssc_encode", st, pp, a.frame, out, a.rate) & 0xFFFFFFFF
        pkt = o.read(out, r)
        blob += bytes(pcm) + struct.pack("<H", len(pkt)) + pkt
    open(a.out, "wb").write(blob)
    print("wrote %s: %d frames, %d bytes" % (a.out, a.frames, len(blob)))


if __name__ == "__main__":
    main()
