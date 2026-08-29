#!/bin/bash
# Retrostation launcher -- install into /mnt/mmc/Roms/APPS/Retrostation/.
#
# This is the shell bootstrap described in DESIGN §8.2.  It exists because a
# game must never be started from inside a process that owns SDL windows: we
# exit completely, the game takes the display, and this loop runs a fresh
# process afterwards so the session resumes where the player left off.
#
# Everything (stdout, stderr, tracebacks) is appended to log.txt next to this
# script.  There is no terminal on the device, so that file is the only way to
# diagnose a startup failure.

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/log.txt"

export RETROSTATION_ROM_ROOT="${RETROSTATION_ROM_ROOT:-/mnt/mmc/Roms}"
export RETROSTATION_CONFIG_DIR="$DIR"
export PYTHONPATH="$DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export PYSDL2_DLL_PATH="${PYSDL2_DLL_PATH:-/usr/lib}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/var/run}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

# Exit-code contract (src/retrostation/main.py):
#   0  -> the player quit the frontend; stop and hand the screen back.
#  42  -> a game ran and exited; start fresh and restore the session.
#  other -> crash.  Retry a few times, then give up: an unconditional restart
#           loop would trap the player on a black screen with no way out.
MAX_CRASHES=3
MAX_LOG_BYTES=262144

banner() {
    echo "----- $(date '+%F %T') $* -----" >>"$LOG" 2>&1
}

# Rotate once, so a long session cannot fill the card.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.old"
fi

banner "start (pid $$)"

crashes=0
while true; do
    # ``-m`` matters: running main.py by path would break relative imports.
    python3 -u -m retrostation.main --config "$DIR/config.json" >>"$LOG" 2>&1
    code=$?
    banner "exit code $code"

    if [ "$code" -eq 0 ]; then
        break
    fi

    if [ "$code" -eq 42 ]; then
        crashes=0
        sync
        continue
    fi

    crashes=$((crashes + 1))
    if [ "$crashes" -ge "$MAX_CRASHES" ]; then
        banner "giving up after $crashes consecutive failures"
        break
    fi
    sleep 1
done

exit 0
