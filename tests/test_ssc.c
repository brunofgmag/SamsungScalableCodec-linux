/* SPDX-License-Identifier: GPL-3.0-or-later */
/* SPDX-FileCopyrightText: 2026 lori28167 */
/*
 * Tests for the SSC encoder: frame layout, determinism, error handling and a round trip through the CELT decoder
 * (configured with the same SSC mode) that measures the signal-to-noise ratio of every preset.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "celt.h"
#include "opus_custom.h"
#include "ssc.h"

static int failures;

#define CHECK(cond, ...) do { \
	if (!(cond)) { \
		failures++; \
		printf("FAIL %s:%d: ", __FILE__, __LINE__); \
		printf(__VA_ARGS__); \
		printf("\n"); \
	} \
} while (0)

#define NFRAMES 40
#define WARMUP 8
#define DELAY 108	/* MDCT overlap: the decoder output lags the input by this many samples */

static void make_signal(int16_t *pcm, int frames, int rate)
{
	long n;

	for (n = 0; n < (long)frames * SSC_FRAME_SAMPLES; n++) {
		double t = (double)n / rate;
		double l = 6000 * sin(2 * M_PI * 330 * t) + 3000 * sin(2 * M_PI * 1800 * t) + 1500 * sin(2 * M_PI * 5200 * t);
		double r = 6000 * sin(2 * M_PI * 660 * t) + 3000 * sin(2 * M_PI * 1800 * t + 1) + 1500 * sin(2 * M_PI * 5200 * t);

		pcm[2 * n] = (int16_t)l;
		pcm[2 * n + 1] = (int16_t)r;
	}
}

static double snr_db(const int16_t *in, const float *out, int frames)
{
	double sig = 0, noise = 0;
	long n;

	for (n = (long)WARMUP * SSC_FRAME_SAMPLES; n < (long)frames * SSC_FRAME_SAMPLES; n++) {
		int c;

		for (c = 0; c < 2; c++) {
			double x = in[2 * (n - DELAY) + c], y = out[2 * n + c] * 32768.0;

			sig += x * x;
			noise += (x - y) * (x - y);
		}
	}
	return 10 * log10(sig / (noise + 1e-9));
}

static void test_layout_and_roundtrip(int rate)
{
	static const double min_snr[SSC_PRESET_UHQ_152K] = { 20, 20, 23, 28, 30 };
	int16_t *pcm = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * NFRAMES);
	float *dec_out = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * NFRAMES);
	int preset;

	make_signal(pcm, NFRAMES, rate);
	for (preset = 0; preset < SSC_PRESET_UHQ_152K; preset++) {
		int err = -1, celt_err = 0, i, fb = ssc_frame_bytes(preset);
		ssc_encoder *enc = ssc_encoder_new(rate, 2, &err);
		OpusCustomMode *mode = opus_custom_mode_create(44100, SSC_FRAME_SAMPLES, &celt_err);
		OpusCustomDecoder *dec = opus_custom_decoder_create(mode, 2, &celt_err);
		uint8_t frame[SSC_MAX_FRAME_SIZE];
		double snr;

		CHECK(enc != NULL && err == SSC_OK, "encoder creation failed (%d)", err);
		CHECK(dec != NULL, "decoder creation failed");
		if (!enc || !dec)
			continue;
		opus_custom_decoder_ctl(dec, CELT_SET_SIGNALLING(0));
		for (i = 0; i < NFRAMES; i++) {
			int n = ssc_encode(enc, pcm + 2 * SSC_FRAME_SAMPLES * i, preset, frame, sizeof(frame));

			CHECK(n == fb, "preset %d frame %d: length %d, expected %d", preset, i, n, fb);
			CHECK(frame[0] == 0xff && frame[1] == 0xee, "preset %d frame %d: bad sync", preset, i);
			CHECK((frame[2] >> 4) == (i & 15) && (frame[2] & 15) == 1, "preset %d frame %d: bad byte 2 (%02x)", preset, i, frame[2]);
			CHECK(frame[3] == 0xf0 + preset && (frame[3] & 0x1f) == 0x10 + preset, "preset %d frame %d: bad info byte %02x", preset, i, frame[3]);
			n = opus_custom_decode_float(dec, frame + SSC_HEADER_SIZE, fb - SSC_HEADER_SIZE,
						     dec_out + 2 * SSC_FRAME_SAMPLES * i, SSC_FRAME_SAMPLES);
			CHECK(n == SSC_FRAME_SAMPLES, "preset %d frame %d: decoder returned %d", preset, i, n);
		}
		snr = snr_db(pcm, dec_out, NFRAMES);
		printf("rate %d preset %d (%d byte frames, %d bit/s): round trip SNR %.1f dB\n", rate, preset, fb, ssc_preset_bitrate(preset), snr);
		CHECK(snr >= min_snr[preset], "preset %d: SNR %.1f dB is below %.1f dB", preset, snr, min_snr[preset]);
		ssc_encoder_free(enc);
		opus_custom_decoder_destroy(dec);
		opus_custom_mode_destroy(mode);
	}
	free(pcm);
	free(dec_out);
}

static void test_determinism_and_reset(void)
{
	int16_t *pcm = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * 6);
	uint8_t a[6][SSC_MAX_FRAME_SIZE], b[6][SSC_MAX_FRAME_SIZE], c[SSC_MAX_FRAME_SIZE];
	ssc_encoder *e1 = ssc_encoder_new(48000, 2, NULL), *e2 = ssc_encoder_new(48000, 2, NULL);
	int i, fb = ssc_frame_bytes(SSC_PRESET_229K);

	make_signal(pcm, 6, 48000);
	for (i = 0; i < 6; i++) {
		ssc_encode(e1, pcm + 2 * SSC_FRAME_SAMPLES * i, SSC_PRESET_229K, a[i], sizeof(a[i]));
		ssc_encode(e2, pcm + 2 * SSC_FRAME_SAMPLES * i, SSC_PRESET_229K, b[i], sizeof(b[i]));
		CHECK(memcmp(a[i], b[i], fb) == 0, "frame %d differs between two identical encoders", i);
	}
	ssc_encoder_reset(e1);
	ssc_encode(e1, pcm, SSC_PRESET_229K, c, sizeof(c));
	CHECK(memcmp(a[0], c, fb) == 0, "first frame after reset differs from the first frame of a new encoder");
	ssc_encoder_free(e1);
	ssc_encoder_free(e2);
	free(pcm);
}

static void test_errors(void)
{
	int err = 0;
	int16_t pcm[2 * SSC_FRAME_SAMPLES] = { 0 };
	uint8_t small[10], big[SSC_MAX_FRAME_SIZE];
	ssc_encoder *enc;

	CHECK(ssc_encoder_new(48000, 1, &err) == NULL && err == SSC_ERR_UNSUPPORTED, "mono must be rejected");
	CHECK(ssc_encoder_new(32000, 2, &err) == NULL && err == SSC_ERR_UNSUPPORTED, "32 kHz must be rejected");
	enc = ssc_encoder_new(44100, 2, &err);
	CHECK(enc != NULL, "44.1 kHz stereo must work");
	CHECK(ssc_encode(enc, pcm, SSC_PRESET_229K, small, sizeof(small)) == SSC_ERR_BUFFER, "short buffer must be reported");
	CHECK(ssc_encode(enc, pcm, 99, big, sizeof(big)) == SSC_ERR_ARGUMENT, "bad preset must be reported");
	CHECK(ssc_frame_bytes(SSC_PRESET_88K) == 224 && ssc_frame_bytes(SSC_PRESET_96K) == 244 && ssc_frame_bytes(SSC_PRESET_128K) == 324 &&
	      ssc_frame_bytes(SSC_PRESET_192K) == 484 && ssc_frame_bytes(SSC_PRESET_229K) == 576, "frame lengths differ from the sink's table");
	CHECK(ssc_preset_for_bitrate(100000) == SSC_PRESET_96K && ssc_preset_for_bitrate(229000) == SSC_PRESET_229K &&
	      ssc_preset_for_bitrate(1000000) == SSC_PRESET_229K && ssc_preset_for_bitrate(1) == SSC_PRESET_88K, "preset selection by bitrate");
	ssc_encoder_free(enc);
}

/* The float entry point must give the very same bits as the 16 bit one for 16 bit data, and must keep what 16 bit rounding loses. */
static void test_float_input(void)
{
	enum { FRAMES = 30 };
	const int preset = SSC_PRESET_229K, fb = ssc_frame_bytes(SSC_PRESET_229K);
	int16_t *pcm = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	float *flt = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	float *quiet = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	float *dec_out = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	int16_t *rounded = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	ssc_encoder *e16 = ssc_encoder_new(48000, 2, NULL), *ef = ssc_encoder_new(48000, 2, NULL);
	uint8_t a[SSC_MAX_FRAME_SIZE], b[SSC_MAX_FRAME_SIZE];
	long n, i;
	int pass;

	make_signal(pcm, FRAMES, 48000);
	for (n = 0; n < 2L * SSC_FRAME_SAMPLES * FRAMES; n++)
		flt[n] = pcm[n] * (1.0f / 32768.0f);
	for (i = 0; i < FRAMES; i++) {
		int n16 = ssc_encode(e16, pcm + 2 * SSC_FRAME_SAMPLES * i, preset, a, sizeof(a));
		int nf = ssc_encode_float(ef, flt + 2 * SSC_FRAME_SAMPLES * i, preset, b, sizeof(b));

		CHECK(n16 == fb && nf == fb, "frame %ld: lengths %d and %d", i, n16, nf);
		CHECK(memcmp(a, b, fb) == 0, "frame %ld: float input differs from the same data as 16 bit", i);
	}
	ssc_encoder_free(e16);
	ssc_encoder_free(ef);

	/* a signal around one 16 bit step (-90 dBFS): the float path codes it, the rounded copy is mostly quantisation noise */
	for (n = 0; n < (long)FRAMES * SSC_FRAME_SAMPLES; n++) {
		double t = (double)n / 48000;
		double l = 0.9 * sin(2 * M_PI * 440 * t), r = 1.3 * sin(2 * M_PI * 1000 * t) + 0.5 * sin(2 * M_PI * 3100 * t);

		quiet[2 * n] = (float)(l / 32768.0);
		quiet[2 * n + 1] = (float)(r / 32768.0);
		rounded[2 * n] = (int16_t)lrint(l);
		rounded[2 * n + 1] = (int16_t)lrint(r);
	}
	for (pass = 0; pass < 2; pass++) {
		int celt_err = 0;
		OpusCustomMode *mode = opus_custom_mode_create(44100, SSC_FRAME_SAMPLES, &celt_err);
		OpusCustomDecoder *dec = opus_custom_decoder_create(mode, 2, &celt_err);
		ssc_encoder *enc = ssc_encoder_new(48000, 2, NULL);
		double sig = 0, noise = 0, snr;

		opus_custom_decoder_ctl(dec, CELT_SET_SIGNALLING(0));
		for (i = 0; i < FRAMES; i++) {
			int len = pass == 0 ? ssc_encode_float(enc, quiet + 2 * SSC_FRAME_SAMPLES * i, preset, a, sizeof(a))
					    : ssc_encode(enc, rounded + 2 * SSC_FRAME_SAMPLES * i, preset, a, sizeof(a));

			CHECK(len == fb, "quiet frame %ld: length %d", i, len);
			opus_custom_decode_float(dec, a + SSC_HEADER_SIZE, fb - SSC_HEADER_SIZE, dec_out + 2 * SSC_FRAME_SAMPLES * i, SSC_FRAME_SAMPLES);
		}
		for (n = (long)WARMUP * SSC_FRAME_SAMPLES; n < (long)FRAMES * SSC_FRAME_SAMPLES; n++) {
			int c;

			for (c = 0; c < 2; c++) {
				double x = quiet[2 * (n - DELAY) + c], y = dec_out[2 * n + c];

				sig += x * x;
				noise += (x - y) * (x - y);
			}
		}
		snr = 10 * log10(sig / (noise + 1e-30));
		printf("-90 dBFS tones, %s input: round trip SNR %.1f dB\n", pass == 0 ? "float" : "16 bit rounded", snr);
		if (pass == 0)
			CHECK(snr >= 18, "float input below the 16 bit step: SNR %.1f dB", snr);
		else
			CHECK(snr < 12, "rounded input was expected to lose the signal, SNR %.1f dB", snr);
		ssc_encoder_free(enc);
		opus_custom_decoder_destroy(dec);
		opus_custom_mode_destroy(mode);
	}
	free(pcm);
	free(flt);
	free(quiet);
	free(dec_out);
	free(rounded);
}

/* UHQ: 96 kHz, 9 ms frames, e-class headers with an 8-bit sequence number */
static void test_uhq(void)
{
	static const int presets[3] = { SSC_PRESET_UHQ_152K, SSC_PRESET_UHQ_291K, SSC_PRESET_UHQ_584K };
	static const int frame_len[3] = { 174, 330, 660 }, info[3] = { 6, 0, 3 };
	static const double min_snr[3] = { 25, 33, 36 };
	enum { FRAMES = 300 };
	int16_t *pcm = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	float *flt = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	float *dec_out = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * FRAMES);
	uint8_t frame[1024];
	int k, err = -1;
	long n;

	make_signal(pcm, FRAMES, 96000);
	for (n = 0; n < 2L * SSC_FRAME_SAMPLES * FRAMES; n++)
		flt[n] = pcm[n] * (1.0f / 32768.0f);
	CHECK(ssc_encoder_new(96000, 2, &err) != NULL && err == SSC_OK, "96 kHz encoder creation");
	for (k = 0; k < 3; k++) {
		int celt_err = 0, i, preset = presets[k];
		ssc_encoder *enc = ssc_encoder_new(96000, 2, NULL);
		OpusCustomMode *mode = opus_custom_mode_create(96000, SSC_FRAME_SAMPLES, &celt_err);
		OpusCustomDecoder *dec = opus_custom_decoder_create(mode, 2, &celt_err);
		double sig = 0, noise = 0;

		CHECK(ssc_preset_is_uhq(preset) == 1 && ssc_frame_bytes(preset) == frame_len[k], "preset %d table", preset);
		opus_custom_decoder_ctl(dec, CELT_SET_SIGNALLING(0));
		for (i = 0; i < FRAMES; i++) {
			int len = ssc_encode_float(enc, flt + 2 * SSC_FRAME_SAMPLES * i, preset, frame, sizeof(frame));

			CHECK(len == frame_len[k], "uhq preset %d frame %d: length %d", preset, i, len);
			CHECK(frame[0] == 0xff && frame[1] == (0xe0 | (i >> 4 & 15)) && frame[2] == (((i & 15) << 4) | 1) && frame[3] == info[k],
			      "uhq preset %d frame %d: bad header %02x %02x %02x %02x", preset, i, frame[0], frame[1], frame[2], frame[3]);
			opus_custom_decode_float(dec, frame + SSC_HEADER_SIZE, len - SSC_HEADER_SIZE, dec_out + 2 * SSC_FRAME_SAMPLES * i, SSC_FRAME_SAMPLES);
		}
		for (n = (long)WARMUP * SSC_FRAME_SAMPLES; n < (long)FRAMES * SSC_FRAME_SAMPLES; n++) {
			int c;

			for (c = 0; c < 2; c++) {
				double x = flt[2 * (n - DELAY) + c], y = dec_out[2 * n + c];

				sig += x * x;
				noise += (x - y) * (x - y);
			}
		}
		printf("UHQ preset %d (%d byte frames): round trip SNR %.1f dB\n", preset, frame_len[k], 10 * log10(sig / (noise + 1e-30)));
		CHECK(10 * log10(sig / (noise + 1e-30)) >= min_snr[k], "uhq preset %d: SNR too low", preset);
		CHECK(ssc_encode(enc, pcm, SSC_PRESET_229K, frame, sizeof(frame)) == SSC_ERR_ARGUMENT, "normal preset on a 96 kHz encoder must be refused");
		ssc_encoder_free(enc);
		opus_custom_decoder_destroy(dec);
		opus_custom_mode_destroy(mode);
	}
	{
		ssc_encoder *e48 = ssc_encoder_new(48000, 2, NULL);

		CHECK(ssc_encode(e48, pcm, SSC_PRESET_UHQ_291K, frame, sizeof(frame)) == SSC_ERR_ARGUMENT, "UHQ preset on a 48 kHz encoder must be refused");
		ssc_encoder_free(e48);
	}
	free(pcm);
	free(flt);
	free(dec_out);
}

int main(void)
{
	test_errors();
	test_determinism_and_reset();
	test_float_input();
	test_uhq();
	test_layout_and_roundtrip(44100);
	test_layout_and_roundtrip(48000);
	if (failures)
		printf("%d check(s) failed\n", failures);
	else
		printf("all checks passed\n");
	return failures != 0;
}
