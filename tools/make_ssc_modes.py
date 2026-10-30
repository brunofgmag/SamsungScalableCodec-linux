#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Patch Opus' celt/modes.c so that opus_custom_mode_create() builds the SSC mode
(band layout + allocation table taken from the Samsung encoder; see ssc_tables.h). Fs >= 96000 selects the UHQ mode tables.

    make_ssc_modes.py OPUS_CELT_DIR OUT.c
"""
import sys

src, out = sys.argv[1] + "/modes.c", sys.argv[2]
s = open(src).read()

def sub(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("pattern not found exactly once: %r" % old)
    s = s.replace(old, new)

# SSC frames are always 8 short blocks of 108 samples, also at 96 kHz (stock Opus would use 4 blocks of 216 there)
sub("   if ((opus_int32)frame_size*75 >= Fs && (frame_size%16)==0)\n", "   if ((frame_size%16)==0)\n")
sub('#include "config.h"\n', '#include "config.h"\n#include <string.h>\n#include "ssc_tables.h"\n#ifndef SSC_USE_ALLOC_B\n#define SSC_USE_ALLOC_B 1\n#endif\n#ifndef SSC96_USE_ALLOC_B\n#define SSC96_USE_ALLOC_B 1\n#endif\n')
sub("   mode->eBands = compute_ebands(Fs, mode->shortMdctSize, res, &mode->nbEBands);\n",
    "   { opus_int16 *eb = (opus_int16*)opus_alloc(sizeof(ssc_ebands));\n"
    "     memcpy(eb, Fs >= 96000 ? ssc96_ebands : ssc_ebands, sizeof(ssc_ebands)); mode->eBands = eb; mode->nbEBands = SSC_NB_EBANDS; }\n")
sub("   compute_allocation_table(mode);\n",
    "   { unsigned char *av = (unsigned char*)opus_alloc(sizeof(ssc_alloc));\n"
    "     memcpy(av, Fs >= 96000 ? (SSC96_USE_ALLOC_B ? ssc96_alloc_b : ssc96_alloc) : (SSC_USE_ALLOC_B ? ssc_alloc_b : ssc_alloc),\n"
    "            sizeof(ssc_alloc)); mode->allocVectors = av; mode->nbAllocVectors = SSC_NB_ALLOC_VECTORS; }\n")
sub("   compute_pulse_cache(mode, mode->maxLM);\n",
    "   compute_pulse_cache(mode, mode->maxLM);\n"
    "   { int ci; const short *cidx = Fs >= 96000 ? ssc96_cache_index : ssc_cache_index;\n"
    "     for (ci = 0; ci < (int)(sizeof(ssc_cache_index)/sizeof(ssc_cache_index[0])); ci++)\n"
    "        ((opus_int16*)mode->cache.index)[ci] = cidx[ci]; }\n"
    "   /* SSC applies no pre-emphasis (ssc_preemphasis only shifts the samples), so no de-emphasis either */\n"
    "   mode->preemph[0] = 0; mode->preemph[1] = 0;\n")
open(out, "w").write(s)
print("wrote", out)
