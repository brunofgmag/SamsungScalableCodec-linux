project('ssc', 'c',
  version: '0.1.0',
  license: 'BSD-3-Clause',
  default_options: ['c_std=gnu11', 'warning_level=2', 'optimization=2'])

cc = meson.get_compiler('c')
libm = cc.find_library('m', required: false)

# CELT core (Opus 1.5.2, BSD-3-Clause) with the changes that make it speak SSC; see NOTES.md and tools/make_ssc_celt.py
celt_inc = include_directories('third_party/celt', 'third_party/celt/include')
celt_sources = files(
  'third_party/celt/bands.c', 'third_party/celt/celt.c', 'third_party/celt/celt_decoder.c',
  'third_party/celt/celt_encoder.c', 'third_party/celt/celt_lpc.c', 'third_party/celt/cwrs.c',
  'third_party/celt/entcode.c', 'third_party/celt/entdec.c', 'third_party/celt/entenc.c',
  'third_party/celt/kiss_fft.c', 'third_party/celt/laplace.c', 'third_party/celt/mathops.c',
  'third_party/celt/mdct.c', 'third_party/celt/modes.c', 'third_party/celt/pitch.c',
  'third_party/celt/quant_bands.c', 'third_party/celt/rate.c', 'third_party/celt/vq.c')

celt = static_library('ssc-celt', celt_sources,
  include_directories: celt_inc,
  c_args: ['-DHAVE_CONFIG_H', '-w'],
  dependencies: libm,
  gnu_symbol_visibility: 'hidden',
  pic: true)

ssc_inc = include_directories('src')
ssc = static_library('ssc', 'src/ssc.c',
  include_directories: [ssc_inc, celt_inc],
  c_args: ['-DHAVE_CONFIG_H'],
  link_with: celt,
  gnu_symbol_visibility: 'hidden',
  pic: true)

ssc_dep = declare_dependency(include_directories: ssc_inc, link_with: [ssc, celt], dependencies: libm)

test_ssc = executable('test_ssc', 'tests/test_ssc.c',
  include_directories: [ssc_inc, celt_inc],
  c_args: ['-DHAVE_CONFIG_H'],
  dependencies: ssc_dep)
test('ssc', test_ssc, timeout: 120)

# PipeWire bluez5 codec plugin. The host loads codec plugins from a fixed list of names; this one takes the "aptx"
# slot (not shipped by Fedora) and therefore has to be installed as libspa-codec-bluez5-aptx.so.
spa_dep = dependency('libspa-0.2', required: false)
if spa_dep.found() and cc.has_header('bluetooth/bluetooth.h')
  pw_inc = include_directories('third_party/pipewire-bluez5')
  plugin_dir = spa_dep.get_variable(pkgconfig: 'plugindir', default_value: get_option('libdir') / 'spa-0.2')
  plugin = shared_library('spa-codec-bluez5-aptx',
    ['src/pipewire/a2dp-codec-ssc.c', 'third_party/pipewire-bluez5/media-codecs.c'],
    include_directories: [pw_inc, ssc_inc],
    c_args: ['-DCODEC_PLUGIN'],
    dependencies: [spa_dep, ssc_dep, libm],
    gnu_symbol_visibility: 'hidden',
    link_args: ['-Wl,--version-script=' + meson.current_source_dir() / 'src/pipewire/exports.map'],
    link_depends: files('src/pipewire/exports.map'),
    name_prefix: 'lib',
    install: true,
    install_dir: plugin_dir / 'bluez5')

  test_plugin = executable('test_plugin', 'tests/test_plugin.c',
    include_directories: [pw_inc, ssc_inc, celt_inc],
    c_args: ['-DHAVE_CONFIG_H'],
    dependencies: [spa_dep, ssc_dep, libm, cc.find_library('dl', required: false)])
  test('plugin', test_plugin, args: [plugin.full_path()], timeout: 120)
else
  warning('libspa-0.2 or the bluez headers are missing: the PipeWire plugin will not be built')
endif
