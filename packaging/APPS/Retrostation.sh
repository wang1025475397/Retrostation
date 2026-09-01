#!/bin/bash
# Retrostation entry point for the stock "APPS" menu.
#
# Install this file as /mnt/mmc/Roms/APPS/<Name>.sh -- NOT inside the app's
# sub-directory.  The stock menu lists only *.sh directly under
# /mnt/mmc/Roms/APPS and ignores sub-directories, so this thin wrapper is what
# makes the app appear in the menu at all.
#
# It sets the environment the stock launcher does not provide and then hands
# over to the real launcher, which owns the restart loop.
#
# The name doubles as the install directory, so this one file serves every
# variant: Retrostation.sh runs APPS/Retrostation/, and a copy named
# Retrostation-Release.sh runs APPS/Retrostation-Release/ -- each with its own
# config, index and log, so a release build can sit next to a dev build.

name="$(basename "$0" .sh)"
progdir="/mnt/mmc/Roms/APPS/$name"

# Measured on the RG DS: scripts started from the APPS menu inherit none of
# this, unlike an interactive SSH shell.  Without XDG_RUNTIME_DIR + WAYLAND_
# DISPLAY, SDL cannot find the compositor and dies before the first frame.
export PYSDL2_DLL_PATH="${PYSDL2_DLL_PATH:-/usr/lib}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

# SDL picks its own driver; forcing one breaks dual-screen.  Verified: with
# SDL_VIDEODRIVER unset it selects Wayland and reports two 640x480 outputs.

if [ ! -x "$progdir/retrostation.sh" ]; then
    echo "$name is not installed at $progdir" >&2
    sleep 3
    exit 1
fi

# Optional escape hatch: stock overlays occasionally survive into an APPS
# session and cover the bottom screen.  Off by default -- set it in the shell
# only when the bottom panel stays hidden.
if [ "${RETROSTATION_KILL_OVERLAY:-0}" = "1" ]; then
    for proc in subscreen.dge muos2.bin dmenu_ln; do
        pkill -x "$proc" 2>/dev/null
    done
    sleep 1
fi

exec "$progdir/retrostation.sh" "$@"
