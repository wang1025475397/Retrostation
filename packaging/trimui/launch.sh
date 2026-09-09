#!/bin/sh
# Retrostation entry point for the TrimUI Smart Pro (CrossMix OS).
#
# CrossMix runs <App>/launch.sh and keeps the menu metadata in <App>/config.json
# -- which is exactly where Retrostation would keep its own settings, so the
# settings live in data/ instead (see RETROSTATION_CONFIG_DIR below).
#
# Everything the frontend prints goes to log.txt next to this file: there is no
# terminal on the device, so that file is the only way to diagnose a startup
# failure.

DIR=$(cd "$(dirname "$0")" && pwd)

# Python.  CrossMix ships a full CPython 3.11 on the card, but a script started
# from the Apps menu gets no PATH at all, and stock TrimUI has no Python
# anywhere -- so this is looked up rather than assumed, and a device that
# already provides one is left to provide it.
PYTHON=""
for candidate in \
    /mnt/SDCARD/System/bin/python3.11 \
    /mnt/SDCARD/System/bin/python3 \
    python3.11 \
    python3
do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON=$candidate
        break
    fi
done

if [ -z "$PYTHON" ]; then
    # Say so on the screen: a silent exit just looks like a broken icon.
    if [ -x /mnt/SDCARD/System/bin/sdl2imgshow ]; then
        /mnt/SDCARD/System/bin/sdl2imgshow \
            -i "/mnt/SDCARD/trimui/res/crossmix-os/bg-info.png" \
            -f "/mnt/SDCARD/System/resources/DejaVuSans.ttf" \
            -s 28 -c "220,220,220" \
            -t "Retrostation: no Python 3 found" &
        sleep 3
        pkill -f sdl2imgshow
    fi
    echo "retrostation: no python3 found" >&2
    exit 1
fi
export PYTHON

# ffmpeg and ffprobe come with the app: this firmware has none, and the one
# other apps ship is not ours to depend on.  They need the two codecs in lib/.
export PATH="$DIR/bin:$PATH"
export LD_LIBRARY_PATH="$DIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# The application itself, plus Pillow -- this Python has no site-packages of
# its own and cannot install any (its ssl module is missing, so pip cannot
# even reach PyPI).  Both are shipped as plain directories for that reason.
export PYTHONPATH="$DIR/src:$DIR/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

# Settings, index and log live in data/, never next to CrossMix's config.json.
# data-config.json is the shipped default, copied once -- an upgrade must never
# overwrite what the player has tuned.
export RETROSTATION_CONFIG_DIR="$DIR/data"
export RETROSTATION_ROM_ROOT="${RETROSTATION_ROM_ROOT:-/mnt/SDCARD/Roms}"
mkdir -p "$RETROSTATION_CONFIG_DIR"
if [ ! -f "$RETROSTATION_CONFIG_DIR/config.json" ] && [ -f "$DIR/data-config.json" ]; then
    cp "$DIR/data-config.json" "$RETROSTATION_CONFIG_DIR/config.json"
fi

# RetroArch keeps saves and states under HOME; the stock launcher points HOME
# at its own directory, and the direct launch below relies on the same.
export HOME="${RETROSTATION_RA_HOME:-/mnt/SDCARD/RetroArch}"

# The TRIMUI Player1 gamepad advertises the standard BTN_* codes -- different
# layout from the Anbernic default in input.py, so ask for the named map we
# ship alongside it.  Override either by exporting before launch.sh.
export RETROSTATION_KEYMAP="${RETROSTATION_KEYMAP:-trimui}"
export RETROSTATION_INPUT_DEVICE="${RETROSTATION_INPUT_DEVICE:-/dev/input/event3}"

# SDL picks its own video driver here; on this firmware that is "mali" at
# 1280x720.  Forcing one breaks it, so nothing is forced.

exec "$DIR/retrostation.sh" "$@"
