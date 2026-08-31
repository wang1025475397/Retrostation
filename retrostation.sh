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
#  42  -> a game is queued in $LAUNCH_CMD; run it, then start fresh again.
#  43  -> the display setup changed; start fresh again, running nothing.
#  other -> crash.  Retry a few times, then give up: an unconditional restart
#           loop would trap the player on a black screen with no way out.
MAX_CRASHES=3
MAX_LOG_BYTES=262144

# Where the frontend leaves the command to run once it has exited (DESIGN
# §8.2).  The file is *sourced*, so it has to stay valid shell -- see
# ``launcher/launch.py``.  Going through a file rather than exec'ing is what
# keeps the exit codes above meaningful: replacing the process would make the
# emulator's exit code look like ours.
LAUNCH_CMD=/tmp/retrostation_launch.cmd

banner() {
    echo "----- $(date '+%F %T') $* -----" >>"$LOG" 2>&1
}

# Video decoding runs in an ffmpeg child (DESIGN §6.5).  It is stopped before a
# launch, but a crash or a SIGKILL can still leave one behind -- and it would
# then burn ~19% of a core for as long as the emulator runs.  Sweep on start and
# after every exit; the pattern only matches our own pipes.
kill_video() {
    pkill -f 'ffmpeg .*-f rawvideo' >>"$LOG" 2>&1
    return 0
}

# Rotate once, so a long session cannot fill the card.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.old"
fi

banner "start (pid $$)"

kill_video

# A command left behind by a session that died mid-launch would start a game
# the player never picked.
rm -f "$LAUNCH_CMD"

crashes=0
while true; do
    # ``-m`` matters: running main.py by path would break relative imports.
    python3 -u -m retrostation.main --config "$DIR/config.json" >>"$LOG" 2>&1
    code=$?
    banner "exit code $code"
    kill_video

    if [ "$code" -eq 0 ]; then
        break
    fi

    if [ "$code" -eq 42 ]; then
        # A game is waiting for us.  Run it in the foreground and do nothing
        # else meanwhile: the frontend has already released the display, and
        # anything we drew now would fight the emulator for it.  When the game
        # exits we loop and start the frontend again, which rebuilds the
        # session from its own state file -- that is the "back where you were"
        # behaviour DESIGN §8.2 asks for.
        if [ ! -f "$LAUNCH_CMD" ]; then
            banner "exit 42 without a launch command; stopping"
            break
        fi

        crashes=0
        banner "game start: $(cat "$LAUNCH_CMD")"
        # shellcheck disable=SC1090 -- the file is generated at runtime
        ( . "$LAUNCH_CMD"; "$@" ) >>"$LOG" 2>&1
        banner "game exit: $?"
        rm -f "$LAUNCH_CMD"
        sync
        continue
    fi

    if [ "$code" -eq 43 ]; then
        # A settings change asked for a different set of windows.  Building them
        # inside a live process is what crashes under Wayland (DESIGN §4.4), so
        # we just start again -- with nothing to run in between.
        banner "restarting for a display change"
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
