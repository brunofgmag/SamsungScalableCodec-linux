#!/bin/bash
# usage: dec_canon.sh DECODE_TEST VECTOR_FILE  -> canonical symbol trace of frame 0 on stdout
TRACE0=1 "$1" "$2" 4 | sed -E 's/^bit_logp\(val=(-?[0-9]+), logp=([0-9]+)\)/logp \1 \2/; s/^lap\(val=(-?[0-9]+), fs=([0-9]+), decay=([0-9]+)\)/lap \1 \2 \3/; s/^icdf\(s=(-?[0-9]+), ftb=([0-9]+)\)/icdf \1 \2/; s/^uint\(fl=([0-9]+), ft=([0-9]+)\)/uint \1 \2/; s/^bits\(fl=([0-9]+), bits=([0-9]+)\)/bits \1 \2/; s/^decode\(ft=([0-9]+)\) -> .*/enc \1/; s/^decode_bin\(bits=([0-9]+)\) -> .*/bin \1/' | grep -E '^(logp|lap|icdf|uint|bits|enc|bin) '
