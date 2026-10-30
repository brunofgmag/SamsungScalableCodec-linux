/* SPDX-License-Identifier: GPL-3.0-or-later */
/* SPDX-FileCopyrightText: 2026 lori28167 */
/*
 * SSC: encoder for the Samsung Scalable Codec (A2DP vendor codec 0x0075:0x0103).
 *
 * The bitstream is CELT (Opus) with a short list of differences found by analysing the codec; see NOTES.md.
 * This library produces one SSC frame per call; an A2DP packet carries exactly one frame after the RTP header.
 */
#ifndef SSC_H
#define SSC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* A2DP identification */
#define SSC_VENDOR_ID 0x00000075u
#define SSC_CODEC_ID 0x0103u

/* Samples per channel in one frame (18.0 ms at 48 kHz, 19.6 ms at 44.1 kHz, 9.0 ms at 96 kHz). */
#define SSC_FRAME_SAMPLES 864
/* Frame header: FF EE <seq<<4|flags> <info>. */
#define SSC_HEADER_SIZE 4
/* The sink's read buffer is 800 bytes. */
#define SSC_MAX_FRAME_SIZE 800

/*
 * Bitrate presets. The sink derives the frame length from the info byte of the header, so only these
 * combinations are valid. The first five belong to the 44.1/48 kHz encoders, the UHQ ones to the 96 kHz encoder.
 */
enum ssc_preset {
	SSC_PRESET_88K = 0,	/* 224 byte frames */
	SSC_PRESET_96K,		/* 244 */
	SSC_PRESET_128K,	/* 324 */
	SSC_PRESET_192K,	/* 484 */
	SSC_PRESET_229K,	/* 576 */
	SSC_PRESET_UHQ_152K,	/* 96 kHz: 174 byte frames (info e6) */
	SSC_PRESET_UHQ_291K,	/* 330 (e0) */
	SSC_PRESET_UHQ_584K,	/* 660 (e3) */
	SSC_PRESET_COUNT
};

enum ssc_error {
	SSC_OK = 0,
	SSC_ERR_ARGUMENT = -1,
	SSC_ERR_BUFFER = -2,
	SSC_ERR_UNSUPPORTED = -3,
	SSC_ERR_MEMORY = -4,
	SSC_ERR_INTERNAL = -5
};

typedef struct ssc_encoder ssc_encoder;

/* Total length in bytes (header included) of a frame of this preset, or a negative error. */
int ssc_frame_bytes(int preset);
/* Nominal bitrate of a preset in bit/s, or a negative error. */
int ssc_preset_bitrate(int preset);
/* 1 for the 96 kHz (UHQ) presets, 0 for the others, or a negative error. */
int ssc_preset_is_uhq(int preset);
/* Among the 44.1/48 kHz presets: the one whose nominal bitrate is the highest not above bps (the lowest if bps is smaller). */
int ssc_preset_for_bitrate(int bps);

/*
 * Create an encoder. Supported: 2 channels, 44100, 48000 or 96000 Hz (UHQ). The codec itself is rate agnostic: the same
 * 864-sample frames are used at all rates, so a frame lasts 9 ms at 96 kHz. A 96 kHz encoder only takes UHQ presets
 * and the others only the non UHQ presets.
 */
ssc_encoder *ssc_encoder_new(int sample_rate, int channels, int *error);
void ssc_encoder_free(ssc_encoder *enc);
/* Switch the pitch pre-filter on (default) or off. Off costs some efficiency on tonal signals; the sink cannot be told either way. */
void ssc_encoder_set_prefilter(ssc_encoder *enc, int on);
/* Forget all history (start of a stream or after a discontinuity). */
void ssc_encoder_reset(ssc_encoder *enc);

/*
 * Encode SSC_FRAME_SAMPLES interleaved 16-bit samples per channel into `out`.
 * Returns the number of bytes written (ssc_frame_bytes(preset)), or a negative error.
 */
int ssc_encode(ssc_encoder *enc, const int16_t *pcm, int preset, uint8_t *out, size_t out_size);
/*
 * Same, for interleaved float samples with full scale at +-1.0. The codec has 24 bit of input precision in its
 * HiFi mode (see NOTES.md): a float or 24 bit source need not be rounded to 16 bit first. Do not mix this with
 * ssc_encode() on one encoder unless the streams are meant to be continuous (the state is shared).
 */
int ssc_encode_float(ssc_encoder *enc, const float *pcm, int preset, uint8_t *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif /* SSC_H */
