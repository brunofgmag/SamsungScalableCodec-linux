#!/usr/bin/env python3
"""Patch Opus' celt/modes.c so that opus_custom_mode_create() builds the SSC mode
(band layout + allocation table taken from the Samsung encoder; see ssc_tables.h).

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

sub('#include "config.h"\n', '#include "config.h"\n#include <string.h>\n#include "ssc_tables.h"\n#ifndef SSC_USE_ALLOC_B\n#define SSC_USE_ALLOC_B 1\n#endif\n')
sub("   mode->eBands = compute_ebands(Fs, mode->shortMdctSize, res, &mode->nbEBands);\n",
    "   { opus_int16 *eb = (opus_int16*)opus_alloc(sizeof(ssc_ebands));\n"
    "     memcpy(eb, ssc_ebands, sizeof(ssc_ebands)); mode->eBands = eb; mode->nbEBands = SSC_NB_EBANDS; }\n")
sub("   compute_allocation_table(mode);\n",
    "   { unsigned char *av = (unsigned char*)opus_alloc(sizeof(ssc_alloc));\n"
    "     memcpy(av, SSC_USE_ALLOC_B ? ssc_alloc_b : ssc_alloc, sizeof(ssc_alloc)); mode->allocVectors = av; mode->nbAllocVectors = SSC_NB_ALLOC_VECTORS; }\n")
sub("   compute_pulse_cache(mode, mode->maxLM);\n",
    "   compute_pulse_cache(mode, mode->maxLM);\n"
    "   { int ci; for (ci = 0; ci < (int)(sizeof(ssc_cache_index)/sizeof(ssc_cache_index[0])); ci++)\n"
    "        ((opus_int16*)mode->cache.index)[ci] = ssc_cache_index[ci]; }\n"
    "   /* SSC applies no pre-emphasis (ssc_preemphasis only shifts the samples), so no de-emphasis either */\n"
    "   mode->preemph[0] = 0; mode->preemph[1] = 0;\n")
open(out, "w").write(s)
print("wrote", out)
