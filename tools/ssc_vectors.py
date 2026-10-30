#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Generate reference test vectors with the emulated Samsung encoder.

File layout (little endian): "SSCV", u32 version (2), u32 nframes, u32 frame_size, u32 channels, u32 bitrate,
u32 fs, u32 bits, then per frame: f32 pcm[frame_size*channels] (full scale +-1.0), u16 packet_len, u8 packet[packet_len].
The encoder gets one int32 per sample (see ssc_preemphasis): 16-bit data as is for bits=16, 24-bit data
(full scale 2^23) otherwise (bits=24 HiFi, bits=34 = 0x22 UHQ).

    ssc_vectors.py LIB.so OUT.bin [--fs 44100] [--rate 229000] [--frames 40] [--bits 16] [--signal tones|noise|hf|sweep|quiet|loud]
"""
import argparse
import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssc_oracle import Oracle  # noqa: E402


def signal(kind, n, c, fs, total, rng):
    """One sample (full scale 1.0) of channel c at index n."""
    t = n / fs
    amp = 1.0 / 32768
    if kind == "noise":
        return rng.uniform(-8000, 8000) * amp
    if kind == "hf":        # content in the topmost band plus a low tone (18 kHz at 44.1/48 kHz, 40 kHz at 96 kHz)
        f = 18000 if fs <= 48000 else 40000
        return (6000 * math.sin(2 * math.pi * f * t + c) + 3000 * math.sin(2 * math.pi * 440 * t)) * amp
    if kind == "sweep":     # log chirp 100 Hz -> 0.9 * Nyquist over the whole file
        T = total / fs
        k = math.log(0.9 * fs / 2 / 100)
        return 8000 * math.sin(2 * math.pi * 100 * T / k * (math.exp(k * t / T) - 1) + c) * amp
    if kind == "quiet":
        return (30 * math.sin(2 * math.pi * 330 * (c + 1) * t) + 10 * math.sin(2 * math.pi * 1800 * t + c)) * amp
    if kind == "loud":
        return 0.7 * math.sin(2 * math.pi * 330 * (c + 1) * t) + 0.25 * math.sin(2 * math.pi * 1800 * t + c)
    return (6000 * math.sin(2 * math.pi * 330 * (c + 1) * t) + 3000 * math.sin(2 * math.pi * 1800 * t + c)
            + 1500 * math.sin(2 * math.pi * 5200 * t)) * amp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("out")
    ap.add_argument("--fs", type=int, default=44100)
    ap.add_argument("--ch", type=int, default=2)
    ap.add_argument("--rate", type=int, default=229000)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--frame", type=int, default=864)
    ap.add_argument("--bits", type=int, default=16, help="4th argument of ssc_encoder_create: 16, 24 or 34 (0x22, UHQ)")
    ap.add_argument("--signal", default="tones")
    a = ap.parse_args()

    o = Oracle(a.lib)
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", a.fs, a.ch, a.bits, err)
    out = o.alloc(size=16384)
    rng = random.Random(7)
    scale = 32768.0 if a.bits == 16 else 8388608.0
    total = a.frames * a.frame
    n = 0
    blob = b"SSCV" + struct.pack("<7I", 2, a.frames, a.frame, a.ch, a.rate, a.fs, a.bits)
    for _ in range(a.frames):
        pcm = []
        for _ in range(a.frame):
            for c in range(a.ch):
                pcm.append(signal(a.signal, n, c, a.fs, total, rng))
            n += 1
        ints = [int(round(max(-1.0, min(1.0 - 1e-9, v)) * scale)) for v in pcm]
        pp = o.alloc(struct.pack("<%di" % len(ints), *ints))
        r = o.call("ssc_encode", st, pp, a.frame, out, a.rate) & 0xFFFFFFFF
        pkt = o.read(out, r)
        # store what the encoder really saw (integers are exact in float32 up to 24 bits)
        blob += struct.pack("<%df" % len(ints), *[x / scale for x in ints]) + struct.pack("<H", len(pkt)) + pkt
    open(a.out, "wb").write(blob)
    print("wrote %s: %d frames, %d bytes" % (a.out, a.frames, len(blob)))


if __name__ == "__main__":
    main()
