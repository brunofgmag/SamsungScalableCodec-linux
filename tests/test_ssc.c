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
	static const double min_snr[SSC_PRESET_COUNT] = { 20, 20, 23, 28, 30 };
	int16_t *pcm = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * NFRAMES);
	float *dec_out = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * NFRAMES);
	int preset;

	make_signal(pcm, NFRAMES, rate);
	for (preset = 0; preset < SSC_PRESET_COUNT; preset++) {
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

int main(void)
{
	test_errors();
	test_determinism_and_reset();
	test_layout_and_roundtrip(44100);
	test_layout_and_roundtrip(48000);
	if (failures)
		printf("%d check(s) failed\n", failures);
	else
		printf("all checks passed\n");
	return failures != 0;
}
