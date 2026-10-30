#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Create the SSC flavour of Opus' CELT sources from a pristine opus-1.5.2/celt directory.

    make_ssc_celt.py OPUS_CELT_DIR OUT_DIR [--debug]

Changes, each verified against Samsung's libScalable_Encoder.so (see NOTES.md):
  1. range coder width: EC_CODE_BITS 32 -> 24 (rng starts at 0x800000, byte = val >> 23, renormalise below 0x8000)
  2. the postfilter tapset is not transmitted (decoder: constant, encoder: symbol omitted)
  3. dynalloc boosts of bands 18-20 stay below 1001 (see below)
  4. the 96 kHz (UHQ) mode transmits no tf symbols at all: tf_res is the table default (tf_select_table[LM][4*isTransient])
  --debug adds env-controlled prints (DBGE: coarse band energies per frame) used by the comparison tools.
The mode itself (22 bands, allocation, pulse cache, no pre-emphasis) is built by tools/make_ssc_modes.py.
"""
import shutil
import sys


def patch(path, old, new):
    s = open(path).read()
    if s.count(old) != 1:
        sys.exit("%s: pattern found %d times: %r" % (path, s.count(old), old))
    open(path, "w").write(s.replace(old, new))


def main():
    src, dst = sys.argv[1], sys.argv[2]
    debug = "--debug" in sys.argv
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)

    patch(dst + "/mfrngcod.h", "# define EC_CODE_BITS  (32)", "# define EC_CODE_BITS  (24)")

    tapset_def = '#include "config.h"\n#ifndef SSC_TAPSET\n#define SSC_TAPSET 0\n#endif\n'
    dec = dst + "/celt_decoder.c"
    patch(dec, '#include "config.h"\n', tapset_def)
    patch(dec, "postfilter_tapset = ec_dec_icdf(dec, tapset_icdf, 2);",
          "postfilter_tapset = SSC_TAPSET; /* SSC does not transmit the tapset */")

    enc = dst + "/celt_encoder.c"
    patch(enc, '#include "config.h"\n', tapset_def)
    patch(enc, "      prefilter_tapset = st->tapset_decision;\n",
          "      prefilter_tapset = SSC_TAPSET; /* SSC: fixed tapset, not transmitted */\n")
    patch(enc, "         ec_enc_icdf(enc, prefilter_tapset, tapset_icdf, 2);\n",
          "         /* SSC does not transmit the postfilter tapset */\n")

    # The decoder side of SSC widens big allocation boosts of the top bands (NOTES.md, "high-band offset copy"); keeping the
    # boosts of bands 18-20 below 1001 (1/8 bit) means that rule can never fire on our streams.
    patch(enc, "            && boost < cap[i]; j++)\n",
          "            && boost < cap[i] && !(i >= 18 && i <= 20 && boost+quanta >= 1001); j++)\n")

    # UHQ: no tf symbols (the mode is recognised by its sample rate, see make_ssc_modes.py)
    patch(dec, "   tf_decode(start, end, isTransient, tf_res, LM, dec);\n",
          "   if (mode->Fs >= 96000) {\n"
          "      /* SSC UHQ does not transmit the tf resolution */\n"
          "      for (i=start;i<end;i++)\n"
          "         tf_res[i] = tf_select_table[LM][4*isTransient];\n"
          "   } else\n"
          "      tf_decode(start, end, isTransient, tf_res, LM, dec);\n")
    patch(enc, "   tf_encode(start, end, isTransient, tf_res, LM, tf_select, enc);\n",
          "   if (mode->Fs >= 96000) {\n"
          "      /* SSC UHQ does not transmit the tf resolution */\n"
          "      for (i=start;i<end;i++)\n"
          "         tf_res[i] = tf_select_table[LM][4*isTransient];\n"
          "   } else\n"
          "      tf_encode(start, end, isTransient, tf_res, LM, tf_select, enc);\n")

    if debug:
        dbg = ('   if (getenv("DBGE")) { int cc, ii; for (cc = 0; cc < C; cc++) { fprintf(stderr, "DBGE ch%d:", cc); '
               'for (ii = 0; ii < nbEBands; ii++) fprintf(stderr, " %.3f", (double)oldBandE[ii + cc*nbEBands]); '
               'fprintf(stderr, "\\n"); } }\n')
        for path, anchor in ((enc, "   st->rng = enc->rng;\n"),
                             (dec, "   unquant_energy_finalise(mode, start, end, oldBandE,")):
            s = open(path).read()
            s = s.replace('#include "config.h"\n', '#include "config.h"\n#include <stdlib.h>\n#include <stdio.h>\n', 1)
            if anchor.endswith("\n"):
                s = s.replace(anchor, anchor + dbg, 1)
            else:                                   # decoder: insert after the statement that starts at the anchor
                i = s.index(anchor)
                j = s.index(";", i) + 1
                s = s[:j] + "\n" + dbg + s[j:]
            open(path, "w").write(s)
    print("wrote", dst)


if __name__ == "__main__":
    main()
