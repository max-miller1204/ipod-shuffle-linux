#!/usr/bin/env bash
# Builds the demo library, opens the shipped app on a display of its own and a
# session bus of its own, and runs driver.py against it.
#
# The bus is private because these actions are how a machine reaches "the
# running Shuffle", and the one already running on the developer's session is
# theirs. The display is gtk4-broadwayd for the same reason: it serves the
# window over HTTP to the headless browser that photographs it, instead of
# opening one on the screen of whoever is sitting here.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DEMO="${DEMO_ROOT:-/tmp/gio-actions-demo}"
DISPLAY_NUMBER="${BROADWAY_DISPLAY_NUMBER:-7}"
PORT=$((8080 + DISPLAY_NUMBER))

if [ ! -d "$DEMO/MAX SHUFFLE" ]; then
    rm -rf "$DEMO"
    /usr/bin/python3 "$REPO/tools/demo-library.py" "$DEMO" > /tmp/demo-build.log
fi

gtk4-broadwayd ":$DISPLAY_NUMBER" > /tmp/broadwayd-gio.log 2>&1 &
BROADWAY_PID=$!
trap 'kill "$BROADWAY_PID" 2>/dev/null || true' EXIT
sleep 1

DEMO_ROOT="$DEMO" \
    BROADWAY_DISPLAY=":$DISPLAY_NUMBER" \
    BROADWAY_URL="http://127.0.0.1:$PORT/" \
    SHUTTER_PORT=$((9400 + DISPLAY_NUMBER)) \
    dbus-run-session -- /usr/bin/python3 "$HERE/driver.py"
