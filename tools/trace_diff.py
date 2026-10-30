#!/usr/bin/env python3
"""Compare the canonical range-coder traces of the Samsung encoder and of the Opus decoder for one frame.

    trace_diff.py ENC.txt DEC.txt [context]
"""
import sys

enc = open(sys.argv[1]).read().split("\n")[:-1]
dec = open(sys.argv[2]).read().split("\n")[:-1]
ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 12
n = min(len(enc), len(dec))
first = next((i for i in range(n) if enc[i] != dec[i]), None)
print("encoder ops: %d, decoder ops: %d, first difference: %s" % (len(enc), len(dec), first if first is not None else "none in common prefix"))
if first is not None:
    print("\n idx   encoder (Samsung)                  decoder (Opus-based)")
    for i in range(max(0, first - 5), min(n, first + ctx)):
        print("%s%4d  %-34s %s" % (">>" if i == first else "  ", i, enc[i], dec[i]))
