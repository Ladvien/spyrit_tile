#!/bin/bash
# Drive Spyrite Tile's palette overlay, Build/Paint/Fill tools, keymaps, panel,
# save and reopen in a real (visible) Blender window. See check_gui_smoke.py.
#
# Everything is isolated from the user's Blender: a scratch HOME, scratch
# resources and a scratch extensions dir holding a symlink to the add-on, the
# same recipe as blended's `make test-viewport-gui`. Takes about two minutes.
# Output (logs, screenshots, results JSON) lands in $SPYRITE_GUI_OUT, which
# defaults to a fresh temp dir. Exit status is non-zero on any failed check, any
# Python traceback or "Error" line in the Blender logs, or a Blender crash.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
BLENDER=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
ADDON=${ADDON:-$REPO/addon/spyrite_tile}
OUT=${SPYRITE_GUI_OUT:-$(mktemp -d)}
mkdir -p "$OUT"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
mkdir -p "$SCRATCH/ext/user_default"
ln -s "$ADDON" "$SCRATCH/ext/user_default/spyrite_tile"

status=0
for phase in 1 2; do
    log="$OUT/blender_phase$phase.log"
    HOME="$SCRATCH" BLENDER_USER_RESOURCES="$SCRATCH/blender_user_resources" \
    BLENDER_USER_EXTENSIONS="$SCRATCH/ext" \
    SPYRITE_GUI_PHASE=$phase SPYRITE_GUI_OUT="$OUT" \
        "$BLENDER" --factory-startup --enable-event-simulate \
        --python "$REPO/tests/gui/check_gui_smoke.py" >"$log" 2>&1 &
    pid=$!
    # Backstop for a hung Blender; the script has its own 110 s watchdog.
    ( sleep 170; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
    killer=$!
    wait "$pid"
    code=$?
    kill "$killer" 2>/dev/null
    wait "$killer" 2>/dev/null
    echo "phase $phase: blender exit code $code (log: $log)"
    [ "$code" -eq 0 ] || status=1
    # grep -E, -i: tracebacks and error lines from the add-on or Blender
    if grep -nEi 'Traceback|^ERROR|[A-Za-z]+Error:| error ' "$log" | grep -v 'SPYRITE_GUI '; then
        echo "phase $phase: Python errors in the Blender log"
        status=1
    fi
done
echo "results and screenshots: $OUT"
exit $status
