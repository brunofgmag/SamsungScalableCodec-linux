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

/* Samples per channel in one frame (18.0 ms at 48 kHz, 19.6 ms at 44.1 kHz). */
#define SSC_FRAME_SAMPLES 864
/* Frame header: FF EE <seq<<4|flags> <info>. */
#define SSC_HEADER_SIZE 4
/* The sink's read buffer is 800 bytes. */
#define SSC_MAX_FRAME_SIZE 800

/*
 * Bitrate presets. The sink derives the frame length from the info byte of the header, so only these
 * combinations are valid.
 */
enum ssc_preset {
	SSC_PRESET_88K = 0,	/* 224 byte frames */
	SSC_PRESET_96K,		/* 244 */
	SSC_PRESET_128K,	/* 324 */
	SSC_PRESET_192K,	/* 484 */
	SSC_PRESET_229K,	/* 576 */
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
/* Preset whose nominal bitrate is the highest not above bps (the lowest if bps is smaller), or a negative error. */
int ssc_preset_for_bitrate(int bps);

/*
 * Create an encoder. Supported: 2 channels, 44100 or 48000 Hz. The codec itself is rate agnostic: the same
 * 864-sample frames are used at both rates.
 */
ssc_encoder *ssc_encoder_new(int sample_rate, int channels, int *error);
void ssc_encoder_free(ssc_encoder *enc);
/* Forget all history (start of a stream or after a discontinuity). */
void ssc_encoder_reset(ssc_encoder *enc);

/*
 * Encode SSC_FRAME_SAMPLES interleaved 16-bit samples per channel into `out`.
 * Returns the number of bytes written (ssc_frame_bytes(preset)), or a negative error.
 */
int ssc_encode(ssc_encoder *enc, const int16_t *pcm, int preset, uint8_t *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif /* SSC_H */
