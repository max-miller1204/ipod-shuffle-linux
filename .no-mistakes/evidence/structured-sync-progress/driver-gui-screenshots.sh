#!/usr/bin/env bash
# Runs driver-gui-screenshots.py under a display, and turns the pictures it
# takes into the GIF beside them.
#
# CI runs the window checks under xvfb; this machine has no X server and no way
# to install one, so the display is gtk4-broadwayd - a GDK backend that serves
# the window over HTTP - and the pictures are taken through a headless browser
# looking at it, which the driver keeps connected for the whole run. What is
# photographed is the surface GTK painted, at the size the window asked for.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DISPLAY_NUMBER="${BROADWAY_DISPLAY_NUMBER:-7}"
PORT=$((8080 + DISPLAY_NUMBER))

gtk4-broadwayd ":$DISPLAY_NUMBER" > /tmp/broadwayd-evidence.log 2>&1 &
BROADWAY_PID=$!
trap 'kill "$BROADWAY_PID" 2>/dev/null || true' EXIT
sleep 1

GDK_BACKEND=broadway \
    BROADWAY_DISPLAY=":$DISPLAY_NUMBER" \
    BROADWAY_URL="http://127.0.0.1:$PORT/" \
    SHUTTER_PORT=$((9400 + DISPLAY_NUMBER)) \
    /usr/bin/python3 "$HERE/driver-gui-screenshots.py"

# The same pictures as one loop, so the bar can be watched moving rather than
# read as four stills.
ffmpeg -nostdin -loglevel error -y -framerate 1/1.4 \
    -pattern_type glob -i "$HERE/1[0123]-bar-*.png" \
    -vf "scale=980:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" \
    -loop 0 "$HERE/15-sync-bar.gif"
printf 'wrote %s\n' "$HERE/15-sync-bar.gif"
