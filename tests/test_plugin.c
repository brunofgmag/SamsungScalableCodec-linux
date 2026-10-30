/*
 * Loads the built PipeWire codec plugin the way the bluez5 host does (dlopen + spa_handle_factory_enum + the
 * Bluez5CodecMedia interface) and drives the codec through its callbacks: caps, configuration selection with the
 * capabilities read from real Galaxy Buds3 Pro, encoding into RTP packets, and a decode check of the audio.
 */
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <spa/utils/string.h>

#include "celt.h"
#include "opus_custom.h"
#include "media-codecs.h"
#include "rtp.h"
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
#define DELAY 108

/* what BlueZ reports for the vendor endpoint of the Buds3 Pro: vendor 0x75, codec 0x0103, config byte 0x3e */
static const uint8_t buds_caps[7] = { 0x75, 0x00, 0x00, 0x00, 0x03, 0x01, 0x3e };

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

static struct spa_audio_info make_info(int rate)
{
	struct spa_audio_info info;

	memset(&info, 0, sizeof(info));
	info.media_type = SPA_MEDIA_TYPE_audio;
	info.media_subtype = SPA_MEDIA_SUBTYPE_raw;
	info.info.raw.format = SPA_AUDIO_FORMAT_S16;
	info.info.raw.rate = rate;
	info.info.raw.channels = 2;
	return info;
}

int main(int argc, char **argv)
{
	void *lib;
	int (*factory_enum)(const struct spa_handle_factory **, uint32_t *);
	const struct spa_handle_factory *factory = NULL;
	uint32_t index = 0;
	struct spa_handle *handle;
	void *iface = NULL;
	const struct spa_bluez5_codec_a2dp *host;
	const struct media_codec *codec = NULL;
	uint8_t caps[A2DP_MAX_CAPS_SIZE], config[A2DP_MAX_CAPS_SIZE];
	struct media_codec_audio_info want = { 48000, 2 };
	int res, i;

	if (argc < 2) {
		fprintf(stderr, "usage: %s plugin.so\n", argv[0]);
		return 2;
	}
	lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
	if (lib == NULL) {
		fprintf(stderr, "dlopen: %s\n", dlerror());
		return 2;
	}
	factory_enum = (int (*)(const struct spa_handle_factory **, uint32_t *))dlsym(lib, "spa_handle_factory_enum");
	CHECK(factory_enum != NULL, "spa_handle_factory_enum not exported");
	if (!factory_enum || factory_enum(&factory, &index) != 1)
		return 1;
	CHECK(spa_streq(factory->name, MEDIA_CODEC_FACTORY_NAME("aptx")), "unexpected factory name %s", factory->name);

	handle = calloc(1, factory->get_size(factory, NULL));
	CHECK(factory->init(factory, handle, NULL, NULL, 0) == 0, "factory init failed");
	CHECK(handle->get_interface(handle, SPA_TYPE_INTERFACE_Bluez5CodecMedia, &iface) == 0 && iface != NULL, "no codec interface");
	host = iface;
	codec = host->codecs[0];
	CHECK(codec != NULL && host->codecs[1] == NULL, "the plugin must export exactly one codec");
	CHECK(spa_streq(codec->name, "ssc"), "codec name %s", codec->name);
	CHECK(codec->codec_id == A2DP_CODEC_VENDOR && codec->vendor.vendor_id == SSC_VENDOR_ID && codec->vendor.codec_id == SSC_CODEC_ID, "wrong A2DP ids");
	CHECK(codec->id == SPA_BLUETOOTH_AUDIO_CODEC_APTX_HD, "wrong ordering id");

	/* our endpoint capabilities */
	res = codec->fill_caps(codec, 0, NULL, caps);
	CHECK(res == 7 && memcmp(caps, (const uint8_t[]){ 0x75, 0, 0, 0, 0x03, 0x01, 0x38 }, 7) == 0, "fill_caps returned %d / %02x", res, caps[6]);

	/* configuration chosen against the Buds3 Pro capabilities */
	res = codec->select_config(codec, 0, buds_caps, sizeof(buds_caps), &want, NULL, config, NULL);
	CHECK(res == 7 && config[6] == 0x18, "48 kHz: select_config returned %d, config byte %02x (expected 18)", res, config[6]);
	want.rate = 44100;
	res = codec->select_config(codec, 0, buds_caps, sizeof(buds_caps), &want, NULL, config, NULL);
	CHECK(res == 7 && config[6] == 0x28, "44.1 kHz: config byte %02x (expected 28)", config[6]);
	{
		uint8_t hifi_only[7] = { 0x75, 0, 0, 0, 0x03, 0x01, 0x04 };
		uint8_t wrong_codec[7] = { 0x75, 0, 0, 0, 0x04, 0x01, 0x3e };

		CHECK(codec->select_config(codec, 0, hifi_only, 7, &want, NULL, config, NULL) == -ENOTSUP, "caps without a normal-mode rate must be refused");
		CHECK(codec->select_config(codec, 0, wrong_codec, 7, &want, NULL, config, NULL) == -ENOTSUP, "a different vendor codec id must be refused");
	}
	want.rate = 48000;
	res = codec->select_config(codec, 0, buds_caps, sizeof(buds_caps), &want, NULL, config, NULL);

	{
		uint8_t buf[512];
		struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buf, sizeof(buf));
		struct spa_pod *param = NULL;

		CHECK(codec->enum_config(codec, 0, config, 7, SPA_PARAM_EnumFormat, 0, &b, &param) == 1 && param != NULL, "enum_config failed");
		CHECK(codec->enum_config(codec, 0, config, 7, SPA_PARAM_EnumFormat, 1, &b, &param) == 0, "enum_config must stop after one format");
	}

	/* encoding */
	for (int mtu_case = 0; mtu_case < 2; mtu_case++) {
		size_t mtu = mtu_case == 0 ? 895 : 300;
		int expect_frame = mtu_case == 0 ? 576 : 244;	/* with a 300 byte MTU the 229k preset (12 + 576) does not fit */
		struct spa_audio_info info = make_info(48000);
		void *enc = codec->init(codec, 0, config, 7, &info, NULL, mtu);
		int16_t *pcm = malloc(sizeof(int16_t) * 2 * SSC_FRAME_SAMPLES * NFRAMES);
		float *dec_out = malloc(sizeof(float) * 2 * SSC_FRAME_SAMPLES * NFRAMES);
		int celt_err = 0;
		OpusCustomMode *mode = opus_custom_mode_create(44100, SSC_FRAME_SAMPLES, &celt_err);
		OpusCustomDecoder *dec = opus_custom_decoder_create(mode, 2, &celt_err);
		double sig = 0, noise = 0;

		CHECK(enc != NULL, "codec init failed (mtu %zu)", mtu);
		if (!enc)
			continue;
		opus_custom_decoder_ctl(dec, CELT_SET_SIGNALLING(0));
		CHECK(codec->get_block_size(enc) == SSC_FRAME_SAMPLES * 4, "block size %d", codec->get_block_size(enc));
		make_signal(pcm, NFRAMES, 48000);
		for (i = 0; i < NFRAMES; i++) {
			uint8_t packet[1024];
			size_t out = 0;
			int flush = -1, hdr, used;
			struct rtp_header *rtp = (struct rtp_header *)packet;

			hdr = codec->start_encode(enc, packet, sizeof(packet), (uint16_t)(1000 + i), 864u * i);
			CHECK(hdr == 12 && rtp->v == 2 && rtp->pt == 96 && ntohs(rtp->sequence_number) == 1000 + i && ntohl(rtp->timestamp) == 864u * i,
			      "bad RTP header (len %d)", hdr);
			used = codec->encode(enc, pcm + 2 * SSC_FRAME_SAMPLES * i, SSC_FRAME_SAMPLES * 4, packet + hdr, sizeof(packet) - hdr, &out, &flush);
			CHECK(used == SSC_FRAME_SAMPLES * 4 && flush == NEED_FLUSH_ALL && (int)out == expect_frame,
			      "mtu %zu frame %d: consumed %d, flush %d, wrote %zu (expected %d)", mtu, i, used, flush, out, expect_frame);
			CHECK(packet[hdr] == 0xff && packet[hdr + 1] == 0xee && (packet[hdr + 2] >> 4) == (i & 15), "bad frame header at %d", i);
			CHECK(hdr + (int)out <= (int)mtu, "packet %d bytes exceeds the MTU %zu", hdr + (int)out, mtu);
			res = opus_custom_decode_float(dec, packet + hdr + SSC_HEADER_SIZE, (int)out - SSC_HEADER_SIZE, dec_out + 2 * SSC_FRAME_SAMPLES * i, SSC_FRAME_SAMPLES);
			CHECK(res == SSC_FRAME_SAMPLES, "decoder returned %d", res);
		}
		for (long n = (long)WARMUP * SSC_FRAME_SAMPLES; n < (long)NFRAMES * SSC_FRAME_SAMPLES; n++)
			for (int c = 0; c < 2; c++) {
				double x = pcm[2 * (n - DELAY) + c], y = dec_out[2 * n + c] * 32768.0;

				sig += x * x;
				noise += (x - y) * (x - y);
			}
		printf("mtu %zu: %d byte frames, plugin round trip SNR %.1f dB\n", mtu, expect_frame, 10 * log10(sig / (noise + 1e-9)));
		CHECK(10 * log10(sig / (noise + 1e-9)) > 22, "round trip SNR too low");
		{
			uint32_t enc_delay = 0, dec_delay = 99;

			codec->get_delay(enc, &enc_delay, &dec_delay);
			CHECK(enc_delay == 108 && dec_delay == 0, "delay %u/%u", enc_delay, dec_delay);
		}
		codec->deinit(enc);
		opus_custom_decoder_destroy(dec);
		opus_custom_mode_destroy(mode);
		free(pcm);
		free(dec_out);
	}
	{
		struct spa_audio_info bad = make_info(48000);
		void *enc;

		bad.info.raw.format = SPA_AUDIO_FORMAT_F32;
		enc = codec->init(codec, 0, config, 7, &bad, NULL, 895);
		CHECK(enc == NULL, "a non S16 stream must be refused");
		CHECK(codec->init(codec, 0, config, 7, &(struct spa_audio_info){ 0 }, NULL, 100) == NULL, "garbage info must be refused");
	}
	if (handle->clear)
		handle->clear(handle);
	free(handle);
	dlclose(lib);
	printf(failures ? "%d check(s) failed\n" : "all checks passed\n", failures);
	return failures != 0;
}
