#!/usr/bin/env bash
# Runs driver-gui-screenshots.py under a display and photographs it.
#
# CI runs the window checks under xvfb; this machine has no X server, so the
# display is gtk4-broadwayd - a GDK backend that serves the window over HTTP -
# and the pictures are taken through a headless browser looking at it, which
# the driver keeps connected for the whole run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DISPLAY_NUMBER="${BROADWAY_DISPLAY_NUMBER:-8}"
PORT=$((8080 + DISPLAY_NUMBER))

gtk4-broadwayd ":$DISPLAY_NUMBER" > /tmp/broadwayd-rails.log 2>&1 &
BROADWAY_PID=$!
trap 'kill "$BROADWAY_PID" 2>/dev/null || true' EXIT
sleep 1

GDK_BACKEND=broadway \
    BROADWAY_DISPLAY=":$DISPLAY_NUMBER" \
    BROADWAY_URL="http://127.0.0.1:$PORT/" \
    SHUTTER_PORT=$((9400 + DISPLAY_NUMBER)) \
    /usr/bin/python3 "$HERE/driver-gui-screenshots.py"

# The display is larger than the window it is showing, and the empty canvas
# around it is not evidence of anything. Cropped to what GTK actually painted.
/usr/bin/python3 - "$HERE" <<'PY'
import sys
from pathlib import Path

from PIL import Image, ImageChops

for path in sorted(Path(sys.argv[1]).glob("2[012]-*.png")):
    image = Image.open(path).convert("RGB")
    canvas = Image.new("RGB", image.size, image.getpixel((0, 0)))
    box = ImageChops.difference(image, canvas).getbbox()
    if box is None:
        continue
    left, top, right, bottom = box
    image.crop((max(left - 8, 0), max(top - 8, 0),
                min(right + 8, image.width),
                min(bottom + 8, image.height))).save(path)
    print(f"cropped {path.name} to {right - left + 16}x{bottom - top + 16}")
PY
