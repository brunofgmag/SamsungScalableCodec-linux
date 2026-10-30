/* SPDX-License-Identifier: GPL-3.0-or-later */
/* SPDX-FileCopyrightText: 2026 lori28167 */
/*
 * SSC encoder: CELT core configured as in the Samsung codec, plus the 4-byte frame header.
 */
#include "ssc.h"

#include <stdlib.h>
#include <string.h>

#include "celt.h"
#include "opus_custom.h"

/* The codec always runs the 44.1 kHz mode with 864-sample frames; 48 kHz audio is fed to it unchanged. */
#define SSC_MODE_RATE 44100

/* The 96 kHz mode of the library; the tables of both modes are in third_party/celt/ssc_tables.h */
#define SSC_UHQ_MODE_RATE 96000

static const struct {
	int bitrate;
	int frame_bytes;
	int info;	/* low bits of header byte 3: the sink maps it to the frame length (f-class: bit 4 set) */
} presets[SSC_PRESET_COUNT] = {
	{ 88000, 224, 0x10 }, { 96000, 244, 0x11 }, { 128000, 324, 0x12 }, { 192000, 484, 0x13 }, { 229000, 576, 0x14 },
	{ 152000, 174, 0x06 }, { 291000, 330, 0x00 }, { 584000, 660, 0x03 },
};

struct ssc_encoder {
	OpusCustomMode *mode;
	OpusCustomEncoder *celt;
	int channels;
	int uhq;
	unsigned seq;
};

int ssc_frame_bytes(int preset)
{
	if (preset < 0 || preset >= SSC_PRESET_COUNT)
		return SSC_ERR_ARGUMENT;
	return presets[preset].frame_bytes;
}

int ssc_preset_bitrate(int preset)
{
	if (preset < 0 || preset >= SSC_PRESET_COUNT)
		return SSC_ERR_ARGUMENT;
	return presets[preset].bitrate;
}

int ssc_preset_is_uhq(int preset)
{
	if (preset < 0 || preset >= SSC_PRESET_COUNT)
		return SSC_ERR_ARGUMENT;
	return preset >= SSC_PRESET_UHQ_152K;
}

int ssc_preset_for_bitrate(int bps)
{
	int i, best = 0;

	if (bps <= 0)
		return SSC_ERR_ARGUMENT;
	for (i = 0; i <= SSC_PRESET_229K; i++)
		if (presets[i].bitrate <= bps)
			best = i;
	return best;
}

ssc_encoder *ssc_encoder_new(int sample_rate, int channels, int *error)
{
	ssc_encoder *enc;
	int err = SSC_OK, celt_err = 0;

	if (channels != 2 || (sample_rate != 44100 && sample_rate != 48000 && sample_rate != SSC_UHQ_MODE_RATE)) {
		err = SSC_ERR_UNSUPPORTED;
		goto fail;
	}
	enc = calloc(1, sizeof(*enc));
	if (enc == NULL) {
		err = SSC_ERR_MEMORY;
		goto fail;
	}
	enc->channels = channels;
	enc->uhq = sample_rate == SSC_UHQ_MODE_RATE;
	enc->mode = opus_custom_mode_create(enc->uhq ? SSC_UHQ_MODE_RATE : SSC_MODE_RATE, SSC_FRAME_SAMPLES, &celt_err);
	if (enc->mode == NULL) {
		err = SSC_ERR_INTERNAL;
		goto fail_free;
	}
	enc->celt = opus_custom_encoder_create(enc->mode, channels, &celt_err);
	if (enc->celt == NULL) {
		err = celt_err == OPUS_ALLOC_FAIL ? SSC_ERR_MEMORY : SSC_ERR_INTERNAL;
		goto fail_free;
	}
	/* Samsung's stream has no custom-mode signalling byte, is constant bitrate, and is the highest complexity. */
	opus_custom_encoder_ctl(enc->celt, CELT_SET_SIGNALLING(0));
	opus_custom_encoder_ctl(enc->celt, OPUS_SET_VBR(0));
	opus_custom_encoder_ctl(enc->celt, OPUS_SET_COMPLEXITY(10));
	if (error)
		*error = SSC_OK;
	return enc;

fail_free:
	ssc_encoder_free(enc);
fail:
	if (error)
		*error = err;
	return NULL;
}

void ssc_encoder_free(ssc_encoder *enc)
{
	if (enc == NULL)
		return;
	if (enc->celt)
		opus_custom_encoder_destroy(enc->celt);
	if (enc->mode)
		opus_custom_mode_destroy(enc->mode);
	free(enc);
}

void ssc_encoder_reset(ssc_encoder *enc)
{
	if (enc == NULL)
		return;
	opus_custom_encoder_ctl(enc->celt, OPUS_RESET_STATE);
	enc->seq = 0;
}

/* Check the arguments common to all sample formats and return the frame length of the preset. */
static int encode_begin(const ssc_encoder *enc, const void *pcm, int preset, const uint8_t *out, size_t out_size)
{
	int frame_bytes;

	if (enc == NULL || pcm == NULL || out == NULL)
		return SSC_ERR_ARGUMENT;
	frame_bytes = ssc_frame_bytes(preset);
	if (frame_bytes < 0)
		return frame_bytes;
	if (ssc_preset_is_uhq(preset) != enc->uhq)
		return SSC_ERR_ARGUMENT;
	if (out_size < (size_t)frame_bytes)
		return SSC_ERR_BUFFER;
	return frame_bytes;
}

/* Write the 4-byte header in front of the CELT payload of `n` bytes. */
static int encode_end(ssc_encoder *enc, int n, int preset, int frame_bytes, uint8_t *out)
{
	if (n != frame_bytes - SSC_HEADER_SIZE)
		return SSC_ERR_INTERNAL;

	out[0] = 0xff;				/* sync */
	if (enc->uhq) {
		/* e-class: 8-bit sequence over byte 1 (high) and byte 2 (low nibble first); byte 3 = info only (no band flags) */
		out[1] = (uint8_t)(0xe0 | (enc->seq >> 4 & 0x0f));
		out[2] = (uint8_t)((enc->seq & 0x0f) << 4 | 0x01);	/* flags nibble 1: all 22 bands are coded */
		out[3] = (uint8_t)presets[preset].info;
	} else {
		out[1] = 0xee;
		out[2] = (uint8_t)((enc->seq & 0x0f) << 4 | 0x01);	/* 4-bit sequence, flags: all 22 bands coded */
		out[3] = (uint8_t)(0xe0 | presets[preset].info);	/* info: selects the frame length on the sink side */
	}
	enc->seq++;
	return frame_bytes;
}

int ssc_encode(ssc_encoder *enc, const int16_t *pcm, int preset, uint8_t *out, size_t out_size)
{
	int frame_bytes = encode_begin(enc, pcm, preset, out, out_size);

	if (frame_bytes < 0)
		return frame_bytes;
	return encode_end(enc, opus_custom_encode(enc->celt, pcm, SSC_FRAME_SAMPLES, out + SSC_HEADER_SIZE,
			frame_bytes - SSC_HEADER_SIZE), preset, frame_bytes, out);
}

int ssc_encode_float(ssc_encoder *enc, const float *pcm, int preset, uint8_t *out, size_t out_size)
{
	int frame_bytes = encode_begin(enc, pcm, preset, out, out_size);

	if (frame_bytes < 0)
		return frame_bytes;
	return encode_end(enc, opus_custom_encode_float(enc->celt, pcm, SSC_FRAME_SAMPLES, out + SSC_HEADER_SIZE,
			frame_bytes - SSC_HEADER_SIZE), preset, frame_bytes, out);
}
