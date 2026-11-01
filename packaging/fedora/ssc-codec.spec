# SPDX-License-Identifier: GPL-3.0-or-later
%global commit      ac65b5bc56e2eed3c9a1d7ad756870859e085ab0
%global shortcommit %(c=%{commit}; echo ${c:0:7})
%global gitname     SamsungScalableCodec-linux

Name:           ssc-codec
Version:        0.1.0
Release:        1.git%{shortcommit}%{?dist}
Summary:        Samsung Scalable/Seamless Codec (SSC) A2DP codec plugin for PipeWire

# original code GPL-3.0-or-later; vendored CELT (Opus) BSD-3-Clause; PipeWire headers MIT
License:        GPL-3.0-or-later AND BSD-3-Clause AND MIT
URL:            https://github.com/lori28167/%{gitname}
Source0:        %{url}/archive/%{commit}/%{gitname}-%{shortcommit}.tar.gz

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  pipewire-devel
BuildRequires:  bluez-libs-devel

Requires:       pipewire
# the plugin takes PipeWire's "aptx" slot (same file name)
Conflicts:      pipewire-codec-aptx
Provides:       bundled(opus-celt) = 1.5.2

%description
Streams A2DP audio to Samsung Galaxy Buds with Samsung's Scalable/Seamless Codec (SSC),
including the 96 kHz UHQ profile. It is a PipeWire (bluez5) codec plugin that replaces the
aptx plugin, so aptx stops working while this package is installed.

Tested only on Samsung Galaxy Buds3 Pro. Experimental, provided as is, without warranty; the author
accepts no responsibility for how it is used.

The plugin uses PipeWire's private codec interface: rebuild it after every PipeWire
update (until then PipeWire may refuse it and fall back to AAC/SBC).

%prep
%autosetup -n %{gitname}-%{commit}

%build
%meson
%meson_build

%check
%meson_test

%install
%meson_install
# store the PipeWire version the plugin was built for
mkdir -p %{buildroot}%{_datadir}/%{name}
pkg-config --modversion libpipewire-0.3 > %{buildroot}%{_datadir}/%{name}/built-for-pipewire

%posttrans
built=$(cat %{_datadir}/%{name}/built-for-pipewire 2>/dev/null)
now=$(rpm -q --qf '%%{VERSION}' pipewire 2>/dev/null)
if [ -n "$built" ] && [ "$built" != "$now" ]; then
    echo "ssc-codec: built for PipeWire $built, installed is $now: update ssc-codec (rebuild) or PipeWire will fall back to AAC/SBC" >&2
fi
echo "ssc-codec: restart WirePlumber and reconnect the earbuds: systemctl --user restart wireplumber" >&2

%files
%license LICENSE third_party/celt/COPYING.opus third_party/pipewire-bluez5/COPYING.pipewire
%doc README.md NOTES.md
%{_libdir}/spa-0.2/bluez5/libspa-codec-bluez5-aptx.so
%{_datadir}/%{name}/

%changelog
* Sat Oct 31 2026 lori28167 <lori28167@users.noreply.github.com> - 0.1.0-1.gitac65b5b
- First package: SSC and SSC UHQ A2DP codec plugin
