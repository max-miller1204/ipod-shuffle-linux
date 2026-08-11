#!/usr/bin/env bash
# The reason the report is written by a program rather than by printf: the
# names on this device came from tags and YouTube titles, so a quote, a
# backslash, a newline, an emoji and a byte no UTF-8 decode accepts are all
# ordinary. Then the other half of the promise - a caller acts on a path it
# read out of the report, without touching it.
set -uo pipefail

ROOT="$1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py"
export IPOD_VENV_PYTHON="/usr/bin/python3"
export FAKE_DB_RECORD="$WORK/db-invocations.jsonl"

IPOD="$WORK/iPod"
mkdir -p "$IPOD/iPod_Control/iTunes" "$IPOD/iPod_Control/Speakable" \
    "$IPOD/iPod_Control/Music/Sigur Rós" \
    "$IPOD/iPod_Control/Music/He said \"hi\"" \
    "$IPOD/iPod_Control/Music/back\\slash"
printf 'a\n' > "$IPOD/iPod_Control/Music/Sigur Rós/Hoppípolla ★.mp3"
printf 'b\n' > "$IPOD/iPod_Control/Music/He said \"hi\"/quote'd.mp3"
printf 'c\n' > "$IPOD/iPod_Control/Music/back\\slash/tab	here.mp3"
# What a FAT volume mounted under a non-UTF-8 iocharset hands back.
printf 'd\n' > "$IPOD/iPod_Control/Music/Sigur Rós/$(printf 'undecodable-\xff')".mp3
printf '#EXTM3U\niPod_Control/Music/Sigur Rós/Hoppípolla ★.mp3\n' \
    > "$IPOD/Fös⚡tudagur.m3u"

echo '$ ipod-remove.sh --list --json   # every name here came from a tag'
"$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json > "$WORK/report.json"
status=$?
cat "$WORK/report.json"
printf '[exit %d]\n\n' "$status"

/usr/bin/python3 - "$WORK/report.json" <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_bytes()
report = json.loads(raw.decode("utf-8"))

print("the document is pure ASCII, so it stays valid under any locale:",
      raw.isascii())
print("it parses, and the names come back as they are on the volume:")
for track in report["tracks"]:
    print("   ", repr(track))
for playlist in report["playlists"]:
    print("    playlist", repr(playlist["name"]), "->", playlist["entries"])
PY

echo
echo '# a caller acting on a path it read out of the report, unmodified -'
echo '# including the one holding a byte no UTF-8 decode accepts'
for pick in Hopp undecodable; do
    target=$(/usr/bin/python3 -c '
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
path = [t for t in report["tracks"] if sys.argv[2] in t][0]
sys.stdout.buffer.write(path.encode("utf-8", "surrogateescape"))
' "$WORK/report.json" "$pick")
    printf '\n$ ipod-remove.sh --yes %q\n' "$target"
    "$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes "$target"
    printf '[exit %d]\n' "$?"
done

echo
echo '$ ipod-remove.sh --list --json | python3 -c "read what is left"'
"$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json \
    | /usr/bin/python3 -c '
import json
import sys

report = json.load(sys.stdin)
print(report["track_count"], "tracks left, and the playlist went with them:")
for track in report["tracks"]:
    print("   ", ascii(track))
print("    playlists:", report["playlists"])
'
