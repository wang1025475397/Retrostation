#!/bin/sh
# Retrostation launcher for the TrimUI Smart Pro (CrossMix OS).
#
# This is the shell bootstrap described in DESIGN §8.2.  It exists because a
# game must never be started from inside a process that owns SDL windows: the
# frontend exits completely, the game takes the display, and this loop starts a
# fresh process afterwards so the session resumes where the player left off.
# It is what a 1 GB device needs -- there is no memory to keep the frontend
# resident behind an emulator.
#
# The environment (Python, PYTHONPATH, PATH, LD_LIBRARY_PATH) is set by
# launch.sh, which execs this script.

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/log.txt"

# Exit-code contract (src/retrostation/main.py):
#   0  -> the player quit; stop and hand the screen back to the menu.
#  42  -> a game is queued in $LAUNCH_CMD; run it, then start fresh again.
#  43  -> the display setup changed; start fresh again, running nothing.
# other -> crash.  Retry a few times, then give up: an unconditional restart
#          loop would trap the player on a black screen with no way out.
MAX_CRASHES=3
MAX_LOG_BYTES=262144

# Where the frontend leaves the command to run once it has exited (DESIGN
# §8.2).  The file is *sourced*, so it has to stay valid shell -- see
# ``launcher/launch.py``.
LAUNCH_CMD=/tmp/retrostation_launch.cmd

PYTHON="${PYTHON:-python3}"

banner() {
    echo "----- $(date '+%F %T') $* -----" >>"$LOG" 2>&1
}

# Video decoding runs in an ffmpeg child (DESIGN §6.5).  It is stopped before a
# launch, but a crash can still leave one behind, and it would then burn a core
# for as long as the emulator runs.  The pattern only matches our own pipes.
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
    "$PYTHON" -u -m retrostation.main \
        --config "$RETROSTATION_CONFIG_DIR/config.json" >>"$LOG" 2>&1
    code=$?
    banner "exit code $code"
    kill_video

    if [ "$code" -eq 0 ]; then
        break
    fi

    if [ "$code" -eq 42 ]; then
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
