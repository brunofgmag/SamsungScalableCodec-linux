# SSC port to Linux (PipeWire SPA plugin): recon notes

## Source firmware
- `SAMFW.COM_SM-S901B_EUX_S901BXXS1AVB3_fac.zip`: Galaxy S22 (Exynos), Android 12 / One UI 4.1, build Feb 2022.
- Extracted: `AP_*.tar.md5` -> `super.img.lz4` -> `work/super/super.raw.img` -> `lpunpack` -> `work/super/parts/{system,vendor,product,odm}.img` (all F2FS).
- Mounted read-only with `udisksctl loop-setup -r -f X.img` + `udisksctl mount -b /dev/loopN -o ro`
  at `/run/media/$USER/{_,vendor,product}` (`_` = system).
- Copies of the relevant files are in `work/libs/`.

## What this firmware contains
- Only the **Samsung Scalable Codec** (SSC, incl. a "SSC HiFi" mode). No "Seamless"/"UHQ" strings anywhere in `libbluetooth.so`.
  The newer Seamless/UHQ variant (Buds2 Pro era, One UI 4.1.1/5+) needs a newer firmware to confirm.

## Key files (system partition, aarch64)
- `/system/lib64/libScalable_Encoder.so` (102 KB, dated 2021-01-01): **not stripped**, 88 exported symbols.
  Imports only malloc/free/memcpy/memset/__stack_chk_fail (+ liblog/libc/libm/libdl sonames), so it is easy to emulate.
  - API: `ssc_encoder_get_size/create/init/destroy`, `ssc_encode` (19.7 KB), `ssc_custom_mode_create`, `ssc_mdct_forward`, `ssc_fft`, ...
  - Internals use CELT/Opus names: `ec_enc_*` (range coder), `ec_laplace_encode`, `alg_quant`, `normalise_bands`,
    `haar1`, `stereo_itheta`, `patch_transient_decision`, `tf_select_table`, `eMeans`. => SSC is a CELT/Opus derivative.
  - Also embeds a `SoundBooster_DRC` (dynamic range control) block.
  - `ssc_encoder_create(Fs, channels, ?, int *err)`; internal custom mode uses frame size 0x360 (864); Fs 48000 is remapped to 44100 internally.
- `/system/lib64/libbluetooth.so`: dlopens `libScalable_Encoder.so` ("cannot open SSC encoder library"); contains
  `A2DP_ParseInfoSsc`, `A2DP_VendorCodecEqualsSsc`, `a2dp_vendor_ssc_encoder[_update]`, `a2dp_ssc_encode_frames`,
  `a2dp_ssc_get_num_frame`, `bta_av_co_is_ssc_hifi_codec`, `btif_av_need_dual_play_for_ssc`.
  Function names live in `.gnu_debugdata` (not visible to `readelf`); radare2 `aaa` (~10 min) loads them. Code is a contiguous block
  at `0x335700-0x338600`: `A2DP_Vendor*Ssc` helpers, `A2dpCodecConfigSsc::{init,setCodecConfig,setPeerCodecCapabilities}`,
  `A2DP_VendorLoadEncoderSsc` (0x3370c8), `a2dp_vendor_ssc_encoder_update` (0x337630), encode loop `fcn.003377b0` (0x3377b0).

## A2DP negotiation (verified in disassembly of libbluetooth.so, Android 12 / One UI 4.1)
Media codec info element is 10 bytes (`A2DP_VendorCodecEqualsSsc`, `...GetTrackSampleRateSsc`, `setCodecConfig`):

| byte | value |
|------|-------|
| 0 | `0x09` (length of the rest) |
| 1 | media type, `<= 0x0f` (audio = 0x00) |
| 2 | `0xFF` (non-A2DP / vendor codec) |
| 3-6 | vendor ID, little endian: `0x00000075` (Samsung) |
| 7-8 | vendor codec ID, little endian: `0x0103` |
| 9 | the single SSC config/capability byte (below) |

Byte 9:
- bits 7..4, sample rate: `0x10`=48000, `0x20`=44100, `0x40`=32000, `0x80`=96000 (`GetTrackSampleRate`; exactly one bit must be set unless bit 2 is set).
  In `setCodecConfig` these map to BTAV rate flags 0x10->0x02(48k), 0x20->0x01(44.1k), 0x80->0x08(96k); 0x40 is not handled there.
- bit 2 (`0x04`): "HiFi" mode: sample rate is forced to 48000 and track bits per sample = 24 (otherwise 16). When the chosen config is HiFi, byte 9 is `0x04 | (peer & 0x08)`.
- bit 3 (`0x08`): bitrate limitation flag (`GetTrackBitrateLimitation` returns true when the bit is clear).
- bits 1..0: not used by the helpers seen so far.
- `A2DP_VendorCodecEqualsSsc` compares only the upper nibble (`(a^b) < 0x10`).
- 96 kHz (`0x80`) makes `GetTrackBitsPerSample` return `0x22` (meaning unknown; probably a Samsung-specific sample-format code).
- `A2DP_VendorUsesRtpHeaderSsc` always returns true: media packets carry the standard RTP header.
- `A2DP_VendorGetPacketTimestampSsc` copies the first 32-bit word of the buffer as the timestamp.
- Android 12 build: no "Seamless"/"UHQ" strings, so UHQ capability bits (if any) are not described here. Buds3 Pro Modalias is `v0075pA013`.

## Still to determine
1. (done, see above) vendor ID / codec ID / capability byte. Still: read the Buds3 Pro's real capability byte from BlueZ
   (needs the buds connected to this PC; they are paired, `XX:XX:XX:XX:XX:XX`, currently disconnected) to see UHQ bits.
2. Media packet framing (`a2dp_ssc_encode_frames`, `a2dp_ssc_get_num_frame`): frames per packet, header bytes, bitrate/frame size per mode.
3. Meaning of `ssc_encoder_create/init` args and the ctl calls made in init (fcn.17930 / fcn.17940).
4. The encoder algorithm (`ssc_encode`) vs. Opus CELT reference.

## Plan
1. Oracle: run `libScalable_Encoder.so` under Unicorn (aarch64 emulation, malloc/memcpy/memset hooks) to produce reference bitstreams.
2. Reimplement the encoder in C (starting from the Opus CELT structure), diff against the oracle until bit-exact.
3. Wrap as `libspa-codec-bluez5-ssc.so` (`spa_bluez5_codec_a2dp`), build against PipeWire 1.6.10.
4. Do not redistribute Samsung binaries; the plugin must be an independent reimplementation.

## Encoder oracle (works): `tools/ssc_oracle.py`
Run with the project venv (`.venv`, unicorn 2.1.4 + pyelftools): `.venv/bin/python -I tools/ssc_oracle.py work/libs/libScalable_Encoder.so --fs 48000 --ch 2 --bits 16 --rate 128000 --frames 4`.
Verified API (from disassembly + emulation):
- `ssc_encoder_get_size(channels, Fs)` -> 9620 bytes for stereo 48 kHz.
- `ssc_encoder_create(Fs, channels, bits, int *err)`; `ssc_encoder_init(st, Fs, channels, bits)`; `bits` is stored at `st+0x6c` (0x18 = 24-bit HiFi/UHQ path).
  Fs=48000 is remapped to 44100 inside the encoder state (`st+0x60`); frame size is fixed at 864 samples/channel.
- `ssc_encode(st, const int16_t *pcm, int frame_size, uint8_t *out, int bitrate_bps)` -> bytes written. Accepted bitrates seen in the code: 87000, 96000, 128000, 192000, 229000.
- Output packet header (4 bytes): `ff ee` (sync word 0xEEFF, little endian), byte 2 = sequence nibble in the high nibble (`01,11,21,31,...`) + a low nibble of 1,
  byte 3 = bitrate/mode info (`0xf2` at 128 kbps, 16-bit; `0xf4` for 229 kbps; depends on bitrate and on bits==24). Then the CELT-like payload.
- Example: stereo 48 kHz 16-bit 128 kbps -> 324 bytes per frame (4 header + 320 payload), i.e. 864 samples/ch = 19.6 ms at the internal 44.1 kHz.
- Observed (oracle, stereo, 44.1/48 kHz): the stream is CBR, packet size depends only on the bitrate:

  | bitrate | packet bytes (incl. 4 header) | byte 2 low nibble | byte 3 |
  |---|---|---|---|
  | 87000  | 220 | 1 | f2 |
  | 96000  | 244 | 1 | f1 |
  | 128000 | 324 | 1 | f2 |
  | 192000 | 484 | d | f3 |
  | 229000 | 576 | d | f4 |

  Payload = 216/240/320/480/572 bytes, i.e. about `bitrate * 20 ms / 8` rounded down to even.
  (87000 and 128000 give the same byte 3; the difference must be inferred from the packet length.)
- 96 kHz, 24-bit, 229000: 266-byte packets (262 payload) -> a different size rule than <=48 kHz; low nibble d, byte 3 f4.
- 32 kHz crashes the emulated library (`ssc_encoder_get_size` reads unmapped memory): not a supported encoder rate, even though the capability byte has a 32 kHz bit.
- OPEN: PCM input format when `bits=24`. Output differs from the 16-bit path for every input layout tried (s16, s32<<8, s32<<16, packed s24),
  so the 24-bit path has its own coding; read `ssc_encode` for `st+0x6c == 0x18` to learn the sample layout.

## Galaxy S23 firmware (Android 14 / One UI 6.1, `S911BXXS8CYBD`, Feb 2025): newer encoder + stack
- Source: `~/Downloads/AP_S911BXXS8CYBD_..._OS14.tar.md5.zip` -> `work/ap14/super.img.lz4` -> `work/super14/parts/*.img` (all **EROFS**).
  Kernel mount via udisks FAILED for EROFS; use userspace instead: `dump.erofs --ls --path=/system/lib64 system.img` and
  `fsck.erofs --extract=<NEW-file-or-dir> --path=/system/lib64/libScalable_Encoder.so system.img` (destination must not exist).
- `work/s23/libScalable_Encoder.so` (103,608 B, NDK r25c): same API as Android 12 but all internals renamed `ssc_*` and many more are exported:
  `ssc_encode`, `ssc_quant_all_bands`, `ssc_pitch_search`, `ssc_comb_filter`, `ssc_remove_doubling`, `ssc_compute_allocation`, `ssc_ec_enc_*`,
  `ssc_mdct_forward`, `ssc_fft`, `tone_detect`, `powercalc`, `last_band_info_calc`, and UHQ-specific **`uhq_headroom`, `normal_headroom`,
  `headroom_gain`, `headroom_process`, `cutoff_start_uhq_index`, `previous_uhq_index`** (+ SSC_DRC_*). Best reference for the UHQ path.
- Bluetooth stack is now in the APEX `com.android.btservices.apex` (unzip -> `apex_payload.img` is ext4 -> `debugfs -R "dump /lib64/libbluetooth_jni.so out"`).
  Extracted to `work/s23/bt/libbluetooth_jni.so` (14.6 MB). SSC symbols: `A2DP_VendorGetSscType`, `A2DP_VendorGetBitRateSsc`, `A2DP_VendorAdjustCodecSsc`,
  `A2DP_BuildInfoSsc`, `A2DP_ParseInfoSsc`, `A2DP_IsVendor{Peer,Source}CodecValidSsc`, `A2dpSscToHalConfig`, `a2dp_vendor_ssc_{send_frames,set_current_encoder_info,...}`,
  and AIDL `SehCodecConfiguration::sscConfig` / `SehCodecCapabilities::sscCapabilities`.
- TODO: disassemble `A2DP_VendorGetSscType` / `GetBitRateSsc` / `AdjustCodecSsc` / `A2dpSscToHalConfig` to get the Normal/HiFi/UHQ types, bitrate table and
  the bits=24 PCM format; run the S23 encoder in the oracle (`tools/ssc_oracle.py`; the lib is still self-contained: check its imports first).

## LIVE capabilities of the Buds3 Pro (read from BlueZ over D-Bus, buds connected to this laptop)
Remote endpoints under `/org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX/` (`busctl --system get-property org.bluez <path> org.bluez.MediaEndpoint1 Codec|Capabilities`):
- `sep1` SBC: caps `3f ff 02 34`.   `sep2` AAC (codec 2): caps `80 01 8c 83 e8 00` (currently the active transport).
- **`sep3` vendor codec (255): caps `75 00 00 00 | 03 01 | 3e`** = vendor 0x00000075, codec 0x0103, config byte **0x3E = 0b00111110**.
  BlueZ's `Capabilities` for a vendor codec is `vendor(4) + codec_id(2) + codec-specific bytes`; the 4 leading bytes of the AVDTP element
  (len, media type, codec type = 0xff) are not included. SetConfiguration takes the same layout.
- All endpoints report `DelayReporting: true`.

### Byte-9 bits, as the S23 (One UI 6.1) stack parses them (`A2DP_ParseInfoSsc` fills `tA2DP_SSC_CIE`)
CIE layout: `[0] vendor u32, [4] codec id u16, [6] rate = b & 0xf0, [7] = b & 8, [8] = b & 4, [9] = b & 2, [10] = b & 1`.
A *configuration* is valid if bit1 or bit2 is set (no rate bit needed), otherwise exactly one rate bit is required (else error 0xc3). A capability is always accepted.
`A2DP_VendorGetSscType`: parse fails -> 0x80; **bit1 (0x02) -> 0x400 (UHQ)**; else rate 0x80 (96 kHz) -> 0x200; else **bit2 (0x04) -> 0x100 (HiFi, 24-bit/48k)**; else 0x80 (normal).
`A2DP_VendorGetTrackBitsPerSampleSsc`: bit1 (UHQ) or 96 kHz -> **0x22**; else bit2 -> 24 (0x18); else 16. (Meaning of 0x22 still unknown; likely selects the encoder's UHQ input mode.)
`A2DP_VendorGetBitRateSsc` returns a constant 192000.
=> Buds3 Pro 0x3E = 44.1 kHz + 48 kHz (0x20|0x10) + bit3 (0x08, no bitrate limitation) + HiFi (0x04) + **UHQ (0x02)**; **no 96 kHz, no 32 kHz**.
=> A source that wants UHQ should configure byte 9 = 0x02 (optionally `| 0x08`); HiFi = 0x04; normal = one rate bit (0x10 or 0x20) `| 0x08`.
S23 stack function addresses (libbluetooth_jni.so, symbol table recovered from `.gnu_debugdata` -> xz -> ELF): `A2DP_ParseInfoSsc` 0x6dde24, `A2DP_VendorGetSscType` 0x6ded9c,
`A2dpCodecConfigSsc::setCodecConfig` 0x6dfa3c (3108 B), `setPeerCodecCapabilities` 0x6e0660, `a2dp_vendor_ssc_encoder_init` 0x6e10e0, `a2dp_vendor_ssc_send_frames` 0x6e1b40 (6872 B), `A2DP_VendorLoadEncoderSsc` 0x6e0c84.

## S23 encoder behaviour + how the phone feeds it (from `a2dp_vendor_ssc_encoder_init` 0x6e10e0 / `send_frames` 0x6e1b40, plus oracle runs)
- `work/s23/libScalable_Encoder.so` runs in `tools/ssc_oracle.py` unchanged (imports: malloc/free/memcpy/memset/__stack_chk_fail/__cxa_*/__register_atfork).
  Same `create(Fs, ch, bits, &err)` / `init` / `encode(st, pcm, frame, out, bitrate)` signatures; state is 9540 B (was 9620).
  16-bit/48k/128k stereo: first frame identical to the Android 12 encoder, later frames differ (state-dependent change between versions), so do not expect bit-exactness vs. both.
- `bits` argument (also `st+0x6c`) = `A2DP_VendorGetTrackBitsPerSampleSsc`: **16 -> s16 samples, 24 (0x18, HiFi) -> packed s24 (3 bytes), 0x22 (UHQ / 96 kHz) -> s32 (4 bytes)**.
  The stack computes PCM bytes per interval as `rate * interval_ms * (bits >> 3) * channels / 1000` (stored at `+0x1c`); channels = 2.
  Phone read sizes seen: 0xd80 = 3456 B = 864 stereo s16; 0x1440 = 5184 B = 864 stereo s24; 0x3600 = 13824 B = 96 kHz, 18 ms, s32 stereo.
- Encode interval (`[+0x340]`): **UHQ = 10 ms** (`strb 10`), others use the default byte (20 ms). Encoder state also stores rate, bits, channel count, bitrate-limitation flag, `isUHQ` flag (`GetSscType == 0x400`).
- Oracle, S23, UHQ (bits=34), 48 kHz stereo, rate 229000: frame 432 -> 290 B packet, **480 (10 ms) -> 322 B**, 864 -> 576 B, 960 -> 640 B (about 2/3 byte per sample/ch);
  frame 1728 faults in the emulator (stack/limit, not investigated). Header is the same `ff ee <seq<<4|d> f4`.
- NOT yet known: frames per A2DP packet / MTU packing (`a2dp_ssc_get_num_frame`), exact bitrate list for UHQ, the PLC/volume handling (`volumeStereo(short*,int,float,float)` is applied on s16 PCM in the phone, irrelevant for Linux).

## RESULT: the SSC bitstream is Opus CELT with a short list of Samsung differences (validated)
`ssc_encode` is a fixed-point CELT encoder (Opus-1.1/1.2 era names) with the following differences from Opus 1.5.2's CELT. All were found by
tracing the emulated encoder's range-coder calls (`tools/ssc_trace.py`) against an Opus-based decoder, and fixed one by one until the traces matched.
1. **Range coder width: `EC_CODE_BITS` = 24 instead of 32** (init: rng=0x800000, nbits_total=25; renormalise below 0x8000, byte = val>>23).
   Everything else in entenc/entdec/laplace/cwrs/pulse enumeration is Opus-compatible: byte-exact against Opus on random symbol sequences
   (`tools/rc_compare.py` + `work/ref/build/rc_replay`), PVQ enumeration identical in 79/80 random cases (the odd one is N=32, K=8; not yet explained).
2. **Mode (one mode per sample rate class; the 44100 one is used for 44.1 and 48 kHz input)**: 8 short MDCTs of 108 bins (frame = 864), overlap 108, **22 bands**
   `eBands = 0 1 2 3 4 5 6 7 8 10 12 14 16 20 24 28 34 40 48 60 72 84 102`, 11 allocation rows (`ssc_tables.h`, from the library).
   The library holds two allocation tables (mode+0x28 "A" and mode+0x30 "B"); `ssc_compute_allocation` picks one with a flag. **Table B reproduces the 229/128 kbps
   16-bit streams** (table A gives 4 vs 3 fine-energy bits for band 20). Which flag selects B is not known yet (candidates: bits==16, stereo, bitrate).
   Pulse-cache `index` differs from Opus' for band 20 (band 20 uses the row of the 18-wide band 21); `bits` and `caps` are identical to Opus' computation.
3. **No pre-emphasis / de-emphasis** (`ssc_preemphasis` is only `out = in << 12`, or `<< 4` for 24-bit/UHQ). The encoder input is **int32 per sample**, value in the low bits.
4. **The postfilter tapset symbol is not transmitted** (pitch octave `uint(6)`, period bits, gain bits (3), then straight to the transient flag). Tapset used by the real decoder: still to check with `ssc_comb_filter`.
5. Frame layout: 4 header bytes (`ff ee`, `seq<<4 | 1|d`, bitrate byte) + CELT payload of fixed size (CBR) written into `out+4` with `ec_enc_shrink(size)`.
6. Reconstruction conventions equal Opus': decoded band energies match the encoder's own `oldBandE` to 3 decimals; delay = overlap = 108 samples.
Validation (`work/ref/build`, `decode_test24_b`, `enc_test24`; vectors in `work/vec`, two seconds of stereo tones): Samsung packets decoded with the Opus-based decoder:
corr 0.9996, SNR 31.5 dB (229 kbps) / 24.9 dB (128 kbps); **my Opus-based encoder -> same decoder: 32.3 / 25.7 dB**. Encoder energies agree with Samsung's to 3 decimals.
Reproduce: `tools/make_ssc_celt.py work/ref/opus/celt work/ref/build/celt24 --debug`, `tools/make_ssc_modes.py work/ref/build/celt24 work/ref/build/ssc_modes24.c`, tables from `tools/ssc_gen_tables.py`.
Caveat: the decoder used for validation is *my* model (validated against Samsung's encoder), not the real Buds decoder.
Still open: UHQ (bits=0x22/24, second mode at mode+0x80 with Fs=96000), HiFi 24-bit, tapset value, header bytes 2-3 semantics, frames per A2DP packet, DRC/headroom blocks,
the encoder-input pitfall (int32) must be remembered in every tool that calls `ssc_encode`.

## Wire format of one SSC frame / A2DP packet (S23 encoder in the oracle + Buds3 Pro decoder, `tools/bes_*.py`)
Buds firmware memory map: file offset F is code at `0x0c01ffe4+F` and data at `0x2c01ffe4+F` (header size 0x1c, base 0x0c020000).
Main-core functions: frame validation `0x0c0af1e4`, `preparse_packet` `0x0c0af2f0`, `store_packet` `0x0c0af6b0`, 96 kHz "UHQ" parser `0x0c0ac828`. The CELT decoder itself lives in another core's image
(tables at file 0x3c1a6c..0x3c2a00, base unknown) and was NOT analysed.
- **One A2DP packet = RTP header (12 B, standard, RTP sequence +1 per packet; gap > 1 triggers PLC) + exactly ONE SSC frame (<= 800 B).** One frame = 864 samples/channel (`0x360`).
- Frame = 4-byte header + CELT payload of fixed size: byte0 `0xFF`; byte1 `0xEE` (f-class); byte2 = `seq<<4 | flags`; byte3 = info.
- Buds validation (`0x0c0af1e4`): byte0 == 0xFF, (byte1 & 0xF0) == 0xE0, len >= 4, then `info = byte3 & 0x1f` selects the EXPECTED frame length (frame rejected if the packet is shorter):

  | byte3 | bitrate preset | expected frame length |
  |---|---|---|
  | f0 | 88 000 | 224 |
  | f1 | 96 000 | 244 |
  | f2 | 128 000 | 324 |
  | f3 | 192 000 (also the default for unlisted values) | 484 |
  | f4 | 229 000 | 576 |
  | f5 / f6 | 328 000 / 256 000 @ 96 kHz (flag=1) | 372 / 292 |
  | e0 / e3 / e6 | 291 000 / 584 000 / 152 000 @ 96 kHz (flag=1) | 330 / 660 / 174 |
  | e1 e2 e4 e5 | 308 000 / 442 000 / 886 000 / 250 000 | 484 (default) |
  e-class frames carry an 8-bit sequence: byte1 = `0xE0 | seq>>4`, byte2 = `(seq&15)<<4 | flags`; the 96 kHz UHQ parser also accepts 2 frames per packet.
- Payload bytes = frame length - 4 = `bitrate * 0.02 / 8` rounded (220, 240, 320, 480, 572 for f0..f4). The encoder accepts any bitrate but the Buds only know the presets above.
- byte2 low nibble = `1 + 4*level`, level 0..3 = a 2-bit "high-band energy" hint written by the encoder (1: none, 5: band 20, 9: band 21, d: both; first frame always d). It does NOT depend on codedBands.
  Streams decode correctly without it (Opus-model decoder ignores it), and the Buds validator/seq parser ignore it, so the plugin writes level 0 (`1`).
- byte3 bits 7..5 are overwritten by a 3-bit signal-dependent value (always 111 in the tests: f?); the Buds validator masks them out.
- Input to `ssc_encode`: int32 per sample (16-bit values as is; 24-bit values for bits != 16).
- Encoder-side quirks worth knowing: bandwidth detection (`last_band_info_calc`: last band whose L+R mean log-energy exceeds a threshold) and per-bitrate cutoff tables
  (`cutoff_start_index`, `cutoff_start_uhq_index`: bin indices 306..864 in steps of 18) decide how many bands are coded; Samsung codes band 21 (17-21 kHz) when it has energy,
  stock Opus rules skip it at these bitrates (my encoder: 7.3 dB vs Samsung's 22.9 dB on an 18 kHz tone; equal on tones/noise).

## Implementation status (library + PipeWire plugin)
- `src/ssc.c` + vendored CELT (`third_party/celt`, generated by `tools/make_ssc_celt.py` and `tools/make_ssc_modes.py`) produce the frames; `meson test` covers header layout, determinism, a round trip
  through the CELT decoder (SNR 23-32 dB at 44.1 kHz, 29-37 dB at 48 kHz for the five presets on the tone test signal) and the plugin loaded with `dlopen`.
- Cost: ~0.15 ms per 864-sample frame (1% of a core), worst case 0.6 ms.
- PipeWire hosts the bluez5 SPA monitor inside WirePlumber (the `spa.bluez5` log lines come from the wireplumber process), so restarting wireplumber is enough to load a new codec plugin.
- The host loads codec plugins from a hard-coded list (`codec-loader.c`: aac aptx faststream ldac sbc lc3plus opus opus-g lc3 g722 hfp-*). The plugin therefore occupies the `aptx` slot
  (libspa-codec-bluez5-aptx.so, factory `api.codec.bluez5.media.aptx`) and uses id `APTX_HD` for ordering (index 2 of the host's list: above AAC). `LC3PLUS_HR` would sit below AAC.
  The proper long-term route is adding an `ssc` entry upstream in PipeWire.
- Plugin behaviour: stereo S16 only; configuration = one sample-rate bit (48 kHz preferred) plus the sink's bit 3 echoed back (as Samsung's stack does); preset 229k unless the L2CAP MTU is too small
  (RTP 12 B + frame must fit); one frame per packet; RTP payload type 96, SSRC 1; flags nibble written as 1.
- Verified on hardware (Galaxy Buds3 Pro, 2026-10-30, Fedora 44, PipeWire 1.6.10): the Buds accept configuration byte 0x18 (48 kHz), the node runs with codec `ssc`, 48 kHz S16 stereo and
  no underruns, with the flags nibble fixed at 1 and the RTP marker bit 0, at the default preset (229k unless the MTU forces a lower one). Still unknown: 44.1 kHz (0x28) and the other presets on the
  sink, and what the 2-bit high-band hint does there.

## Buds3 Pro firmware (decoder side): `FOTA_R630XXU0AZG2.bin` (project root, 8.7 MB, build Jul 10 2026)
- NOT encrypted. Starts with BES (Bestechnic) image header magic `0xcafecafe`, then Thumb code at file offset 0x28
  (`ldr r0,[pc,#0x40]; bx r0`). Load base not yet determined (try correlating literal-pool pointers with string offsets).
- Contains the SSC **decoder** (stripped, but with log strings): `a2dp_decoder_scalable.cpp`, `a2dp_audio_scalable_{init,preparse_packet,
  store_packet,synchronize_packet,discards_packet,convert_list_to_samples}`, `a2dp_audio_cc_scalable_{init,decoder_reinit,set_sbm_param}`,
  `a2dp_select_scalable_capability`, `a2dp_scalable_config` ("Vendor ID:0x%08x, Codec ID:0x%04x, Scalable Codec"; "config value:0x%02x, elements[6]:0x%02x").
- **UHQ and Normal are the same codec**, selected inside the same init: `SSC UHQ ssc_decoder_init sampling rate = %d` vs `SSC Normal ssc_decoder_init ...`.
  So "Seamless/UHQ" is very likely the HiFi mode (byte-9 bits 0x04 / 0x80) of the Scalable Codec 0x0075:0x0103 already present in the Android 12 firmware.
  => the Android 14 phone firmware is probably not needed for the bitstream; still useful only to double-check phone-side UHQ encoder parameters.
- Stream details visible in strings: packets have a header with **sync words** (`SCALABLE_HEAD_SIZE`), a **sequence number** (gap triggers PLC),
  UHQ packets log `UHQ seq` from `data[3]` and a timestamp; bitrate adaptation uses `SSC bitrate{info:0x%02X->QosLevel:%d}`; "SBM" (SoundBooster) offsets/direction.
- Use: decoder = round-trip oracle for the encoder; its packet pre-parser defines the exact packet header.
