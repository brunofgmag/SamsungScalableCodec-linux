#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
#
# Build, test and install the SSC PipeWire codec plugin.
#
#   ./install.sh             build, run the tests, install (asks for sudo), restart WirePlumber
#   ./install.sh --uninstall remove the plugin and restart WirePlumber
#   ./install.sh --status    show the installed plugin, the PipeWire version it was built for and the codec in use
#   ./install.sh --dry-run   build and test only
#
# The plugin links against PipeWire's private codec interface, so it has to be rebuilt (just run this script again)
# after every PipeWire update. If the host refuses the plugin, PipeWire falls back to AAC/SBC.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
NAME=libspa-codec-bluez5-aptx.so
BUILD=build

die() { echo "error: $*" >&2; exit 1; }

plugin_dir() {
    pkg-config --variable=plugindir libspa-0.2 2>/dev/null | sed 's|$|/bluez5|' || true
}

restart_session() {
    echo "restarting WirePlumber (reconnect the earbuds afterwards)"
    systemctl --user restart wireplumber
}

DIR=$(plugin_dir)
[ -n "$DIR" ] || die "libspa-0.2 not found: install pipewire-devel and bluez-libs-devel"

case "${1:-}" in
--status)
    ls -l "$DIR/$NAME" 2>/dev/null || echo "not installed in $DIR"
    echo "PipeWire now: $(pkg-config --modversion libpipewire-0.3)"
    [ -f "$BUILD/meson-info/intro-buildoptions.json" ] && echo "last build in $BUILD/ (rebuild with ./install.sh if PipeWire changed)"
    pw-dump 2>/dev/null | python3 -c '
import json, sys
for o in json.load(sys.stdin):
    p = o.get("info", {}).get("props", {})
    if str(p.get("node.name", "")).startswith("bluez_output"):
        print("%s: codec %s, %s" % (p["node.name"], p.get("api.bluez5.codec"), o["info"].get("state")))
' || true
    exit 0
    ;;
--uninstall)
    echo "removing $DIR/$NAME"
    sudo rm -f "$DIR/$NAME"
    restart_session
    exit 0
    ;;
--dry-run|"")
    ;;
*)
    die "unknown option $1 (see the header of this file)"
    ;;
esac

command -v meson >/dev/null || die "meson is needed"
if [ ! -d "$BUILD" ]; then
    meson setup "$BUILD"
fi
# 'meson test' does not rebuild the plugin: compile first, always
meson compile -C "$BUILD"
meson test -C "$BUILD" --print-errorlogs
[ -f "$BUILD/$NAME" ] || die "the plugin was not built (needs pipewire-devel and bluez-libs-devel)"

[ "${1:-}" = "--dry-run" ] && { echo "built $BUILD/$NAME, tests passed (dry run, nothing installed)"; exit 0; }

if [ -f "$DIR/$NAME" ] && [ ! -f "$DIR/$NAME.previous" ]; then
    echo "keeping the installed plugin as $NAME.previous"
    sudo cp -p "$DIR/$NAME" "$DIR/$NAME.previous"
fi
sudo install -m 0755 "$BUILD/$NAME" "$DIR/$NAME"
echo "installed $DIR/$NAME for PipeWire $(pkg-config --modversion libpipewire-0.3)"
restart_session
echo "check with: ./install.sh --status"
echo "profiles: 'ssc' (44.1/48 kHz) and 'ssc_uhq' (96 kHz) in the sound settings; start UHQ at low volume"
