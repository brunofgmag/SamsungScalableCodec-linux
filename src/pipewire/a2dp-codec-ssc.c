/* SPDX-License-Identifier: GPL-3.0-or-later */
/* SPDX-FileCopyrightText: 2026 lori28167 */
/* The structure and some code are adapted from PipeWire's spa/plugins/bluez5/a2dp-codec-ldac.c, which is MIT
 * licensed (Copyright © 2018 Wim Taymans and contributors); see third_party/pipewire-bluez5/COPYING.pipewire. */
/*
 * Samsung Scalable Codec (SSC) A2DP source codec for the PipeWire bluez5 plugin.
 *
 * One A2DP packet is the RTP header followed by exactly one SSC frame (see NOTES.md). The format that the
 * PipeWire graph feeds the codec is interleaved stereo S16; a frame consumes 864 samples per channel.
 *
 * PipeWire loads codec plugins from a fixed list of names. This plugin exports the factory of the "aptx" slot
 * (api.bluez5.codec.media.aptx) and so it must be installed as libspa-codec-bluez5-aptx.so; it uses the aptx-hd
 * codec id only to be ordered above AAC in the host's preference list.
 */
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>

#include <spa/utils/string.h>
#include <spa/param/audio/format.h>
#include <spa/pod/builder.h>

#include "rtp.h"
#include "media-codecs.h"
#include "ssc.h"

#define SSC_CFG_RATE_48000	0x10
#define SSC_CFG_RATE_44100	0x20
#define SSC_CFG_NO_LIMIT	0x08	/* sink states no bitrate limitation; echoed back in the configuration */

/* Codec delay in samples: the MDCT overlap */
#define SSC_CODEC_DELAY		108

typedef struct {
	a2dp_vendor_codec_t info;
	uint8_t config;
} __attribute__((packed)) a2dp_ssc_t;

static struct spa_log *log_;

struct impl {
	ssc_encoder *enc;
	int preset;
	int rate;
	int block_size;	/* bytes of S16 stereo PCM consumed per frame */
};

static const struct media_codec_config ssc_rates[] = {
	{ SSC_CFG_RATE_48000, 48000, 1 },
	{ SSC_CFG_RATE_44100, 44100, 0 },
};

static int codec_fill_caps(const struct media_codec *codec, uint32_t flags,
		const struct spa_dict *settings, uint8_t caps[A2DP_MAX_CAPS_SIZE])
{
	static const a2dp_ssc_t a2dp_ssc = {
		.info.vendor_id = SSC_VENDOR_ID,
		.info.codec_id = SSC_CODEC_ID,
		.config = SSC_CFG_RATE_48000 | SSC_CFG_RATE_44100 | SSC_CFG_NO_LIMIT,
	};

	memcpy(caps, &a2dp_ssc, sizeof(a2dp_ssc));
	return sizeof(a2dp_ssc);
}

static int codec_select_config(const struct media_codec *codec, uint32_t flags,
		const void *caps, size_t caps_size,
		const struct media_codec_audio_info *info,
		const struct spa_dict *settings, uint8_t config[A2DP_MAX_CAPS_SIZE],
		void **config_data)
{
	a2dp_ssc_t conf;
	int i;

	if (caps_size < sizeof(conf))
		return -EINVAL;
	memcpy(&conf, caps, sizeof(conf));

	if (codec->vendor.vendor_id != conf.info.vendor_id ||
	    codec->vendor.codec_id != conf.info.codec_id)
		return -ENOTSUP;

	i = media_codec_select_config(ssc_rates, SPA_N_ELEMENTS(ssc_rates), conf.config,
			info ? info->rate : A2DP_CODEC_DEFAULT_RATE);
	if (i < 0)
		return -ENOTSUP;
	/* Normal (non HiFi, non UHQ) mode: one sample-rate bit, plus the sink's "no limitation" bit */
	conf.config = ssc_rates[i].config | (conf.config & SSC_CFG_NO_LIMIT);

	memcpy(config, &conf, sizeof(conf));
	return sizeof(conf);
}

static int codec_enum_config(const struct media_codec *codec, uint32_t flags,
		const void *caps, size_t caps_size, uint32_t id, uint32_t idx,
		struct spa_pod_builder *b, struct spa_pod **param)
{
	a2dp_ssc_t conf;
	struct spa_pod_frame f[2];
	struct spa_pod_choice *choice;
	uint32_t position[2] = { SPA_AUDIO_CHANNEL_FL, SPA_AUDIO_CHANNEL_FR };
	uint32_t i = 0;

	if (caps_size < sizeof(conf))
		return -EINVAL;
	memcpy(&conf, caps, sizeof(conf));

	if (idx > 0)
		return 0;

	spa_pod_builder_push_object(b, &f[0], SPA_TYPE_OBJECT_Format, id);
	spa_pod_builder_add(b,
			SPA_FORMAT_mediaType,	SPA_POD_Id(SPA_MEDIA_TYPE_audio),
			SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
			SPA_FORMAT_AUDIO_format, SPA_POD_Id(SPA_AUDIO_FORMAT_S16),
			0);
	spa_pod_builder_prop(b, SPA_FORMAT_AUDIO_rate, 0);

	spa_pod_builder_push_choice(b, &f[1], SPA_CHOICE_None, 0);
	choice = (struct spa_pod_choice *)spa_pod_builder_frame(b, &f[1]);
	if (conf.config & SSC_CFG_RATE_48000) {
		if (i++ == 0)
			spa_pod_builder_int(b, 48000);
		spa_pod_builder_int(b, 48000);
	}
	if (conf.config & SSC_CFG_RATE_44100) {
		if (i++ == 0)
			spa_pod_builder_int(b, 44100);
		spa_pod_builder_int(b, 44100);
	}
	if (i > 1)
		choice->body.type = SPA_CHOICE_Enum;
	spa_pod_builder_pop(b, &f[1]);

	if (i == 0)
		return -EINVAL;

	spa_pod_builder_add(b,
			SPA_FORMAT_AUDIO_channels, SPA_POD_Int(2),
			SPA_FORMAT_AUDIO_position, SPA_POD_Array(sizeof(uint32_t), SPA_TYPE_Id, 2, position),
			0);
	*param = spa_pod_builder_pop(b, &f[0]);
	return *param == NULL ? -EIO : 1;
}

static int preset_from_name(const char *s)
{
	static const struct { const char *name; int preset; } names[] = {
		{ "88k", SSC_PRESET_88K }, { "96k", SSC_PRESET_96K }, { "128k", SSC_PRESET_128K },
		{ "192k", SSC_PRESET_192K }, { "229k", SSC_PRESET_229K },
	};
	size_t i;

	for (i = 0; i < SPA_N_ELEMENTS(names); i++)
		if (spa_streq(s, names[i].name))
			return names[i].preset;
	return -1;
}

static void *codec_init(const struct media_codec *codec, uint32_t flags,
		void *config, size_t config_len, const struct spa_audio_info *info,
		void *props, size_t mtu)
{
	struct impl *this;
	a2dp_ssc_t *conf = config;
	const char *env;
	int res = 0, preset = SSC_PRESET_229K;

	if (config_len < sizeof(*conf) || info == NULL)
		return NULL;
	if (info->media_type != SPA_MEDIA_TYPE_audio || info->media_subtype != SPA_MEDIA_SUBTYPE_raw ||
	    info->info.raw.format != SPA_AUDIO_FORMAT_S16 || info->info.raw.channels != 2) {
		errno = EINVAL;
		return NULL;
	}

	this = calloc(1, sizeof(*this));
	if (this == NULL)
		return NULL;

	this->rate = (conf->config & SSC_CFG_RATE_48000) ? 48000 : 44100;
	/* Preset for experiments: SSC_PRESET=88k|96k|128k|192k|229k in the PipeWire environment */
	env = getenv("SSC_PRESET");
	if (env != NULL && preset_from_name(env) >= 0)
		preset = preset_from_name(env);
	/* the RTP header and one frame must fit in the L2CAP MTU */
	while (preset > 0 && sizeof(struct rtp_header) + (size_t)ssc_frame_bytes(preset) > mtu)
		preset--;
	if (sizeof(struct rtp_header) + (size_t)ssc_frame_bytes(preset) > mtu) {
		errno = EINVAL;
		goto error;
	}
	this->preset = preset;
	this->block_size = SSC_FRAME_SAMPLES * 2 * (int)sizeof(int16_t);
	this->enc = ssc_encoder_new(this->rate, 2, &res);
	if (this->enc == NULL) {
		errno = res == SSC_ERR_MEMORY ? ENOMEM : EINVAL;
		goto error;
	}
	spa_log_info(log_, "SSC: rate %d, preset %d (%d bit/s, %d byte frames), mtu %zu",
			this->rate, preset, ssc_preset_bitrate(preset), ssc_frame_bytes(preset), mtu);
	return this;

error:
	free(this);
	return NULL;
}

static void codec_deinit(void *data)
{
	struct impl *this = data;

	ssc_encoder_free(this->enc);
	free(this);
}

static int codec_get_block_size(void *data)
{
	struct impl *this = data;

	return this->block_size;
}

static int codec_start_encode(void *data, void *dst, size_t dst_size, uint16_t seqnum, uint32_t timestamp)
{
	struct rtp_header *header = dst;

	if (dst_size < sizeof(*header))
		return -EINVAL;
	memset(header, 0, sizeof(*header));
	header->v = 2;
	header->pt = 96;
	header->sequence_number = htons(seqnum);
	header->timestamp = htonl(timestamp);
	header->ssrc = htonl(1);
	return sizeof(*header);
}

static int codec_encode(void *data, const void *src, size_t src_size,
		void *dst, size_t dst_size, size_t *dst_out, int *need_flush)
{
	struct impl *this = data;
	int n;

	*dst_out = 0;
	*need_flush = NEED_FLUSH_NO;
	if (src == NULL || src_size < (size_t)this->block_size)
		return 0;

	n = ssc_encode(this->enc, src, this->preset, dst, dst_size);
	if (SPA_UNLIKELY(n < 0))
		return -EINVAL;

	*dst_out = n;
	*need_flush = NEED_FLUSH_ALL;	/* exactly one frame per packet */
	return this->block_size;
}

static void codec_get_delay(void *data, uint32_t *encoder, uint32_t *decoder)
{
	if (encoder)
		*encoder = SSC_CODEC_DELAY;
	if (decoder)
		*decoder = 0;
}

static void codec_set_log(struct spa_log *global_log)
{
	log_ = global_log;
	spa_log_topic_init(log_, &codec_plugin_log_topic);
}

const struct media_codec a2dp_codec_ssc = {
	/* aptx-hd id: ordered above AAC and not special-cased anywhere else in the host */
	.id = SPA_BLUETOOTH_AUDIO_CODEC_APTX_HD,
	.kind = MEDIA_CODEC_A2DP,
	.codec_id = A2DP_CODEC_VENDOR,
	.vendor = { .vendor_id = SSC_VENDOR_ID, .codec_id = SSC_CODEC_ID },
	.name = "ssc",
	.description = "Samsung Scalable Codec (SSC)",
	.fill_caps = codec_fill_caps,
	.select_config = codec_select_config,
	.enum_config = codec_enum_config,
	.init = codec_init,
	.deinit = codec_deinit,
	.get_block_size = codec_get_block_size,
	.start_encode = codec_start_encode,
	.encode = codec_encode,
	.get_delay = codec_get_delay,
	.set_log = codec_set_log,
};

MEDIA_CODEC_EXPORT_DEF(
	"aptx",
	&a2dp_codec_ssc
);
