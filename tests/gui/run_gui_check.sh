#!/bin/bash
# Run one single-phase GUI check script (tests/gui/check_issue_*.py) in a real,
# isolated Blender window, with the same isolation as run_gui_smoke.sh.
#   tests/gui/run_gui_check.sh tests/gui/check_issue_135.py
# The script must end the process itself with os._exit(code) (0 pass, 1 failed
# checks, 2 driver error, 3 watchdog) and print its checks as "SPYRITE_GUI ..." lines.
# Non-zero exit also on a Blender crash or any traceback/error line in the log.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT=${1:?usage: run_gui_check.sh <check script>}
BLENDER=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
ADDON=${ADDON:-$REPO/addon/spyrite_tile}
OUT=${SPYRITE_GUI_OUT:-$(mktemp -d)}
mkdir -p "$OUT"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
mkdir -p "$SCRATCH/ext/user_default"
ln -s "$ADDON" "$SCRATCH/ext/user_default/spyrite_tile"
log="$OUT/$(basename "$SCRIPT" .py).log"
HOME="$SCRATCH" BLENDER_USER_RESOURCES="$SCRATCH/blender_user_resources" \
BLENDER_USER_EXTENSIONS="$SCRATCH/ext" SPYRITE_GUI_OUT="$OUT" \
    "$BLENDER" --factory-startup --enable-event-simulate --python "$SCRIPT" >"$log" 2>&1 &
pid=$!
( sleep 170; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
killer=$!
wait "$pid"; code=$?
kill "$killer" 2>/dev/null; wait "$killer" 2>/dev/null
status=0
echo "$(basename "$SCRIPT"): blender exit code $code (log: $log)"
[ "$code" -eq 0 ] || status=1
if grep -nEi 'Traceback|^ERROR|[A-Za-z]+Error:| error ' "$log" | grep -v 'SPYRITE_GUI '; then
    echo "$(basename "$SCRIPT"): Python errors in the Blender log"; status=1
fi
grep 'SPYRITE_GUI ' "$log" | tail -40
exit $status
