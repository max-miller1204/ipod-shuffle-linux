#!/usr/bin/env bash
# Every rename this change is about, pressed in the real window against a demo
# iPod that ipod-sync.sh actually wrote to.
#
#   run-scenarios.sh <repo> <out-dir>
#
# Each scenario gets a demo built from scratch, because accepting a rename runs
# ipod-remove.sh against the volume and what it takes off does not come back.
# The "before" scenario is the same press with the pre-change playlist_view.py
# checked out in its place, which is the whole of the reproduction: nothing else
# about the app, the device or the machine differs between the two runs.
#
# Needs a nested X server (Xephyr) and, on this machine, the installed database
# builder: the demo moves HOME, which is where install.sh keeps it.
set -euo pipefail

REPO="$(cd "$1" && pwd)"
OUT="$2"
DEMO=/tmp/rename-demo
DRIVER="$REPO/.no-mistakes/evidence/refuse-stranding-playlist-rename/driver-rename-refusal.py"
BASE_COMMIT=b48928c72713d0fc1dc3cee7be17ada1e3b24859

mkdir -p "$OUT"

# A PATH the speech engines have been taken out of, which is the only thing
# that makes a run a machine without one: has_speech_engine asks the PATH it is
# given, and so does the database builder.
SHIM=/tmp/rename-nospeech-bin
rm -rf "$SHIM"
mkdir -p "$SHIM"
for dir in /usr/local/bin /usr/bin /bin; do
    [ -d "$dir" ] || continue
    for tool in "$dir"/*; do
        name="$(basename "$tool")"
        case "$name" in
            pico2wave | espeak | espeak-ng | say) continue ;;
        esac
        [ -e "$SHIM/$name" ] || ln -s "$tool" "$SHIM/$name"
    done
done
for engine in pico2wave espeak espeak-ng say; do
    if PATH="$SHIM" command -v "$engine" >/dev/null 2>&1; then
        echo "the speechless PATH still finds $engine" >&2
        exit 1
    fi
done
PATH="$SHIM" command -v bash rsync find >/dev/null || {
    echo "the speechless PATH lost something the scripts need" >&2
    exit 1
}

export IPOD_VENV_PYTHON="$HOME/ipod-tools/venv/bin/python"
export IPOD_DB_TOOL="$HOME/ipod-tools/IPod-Shuffle-4g/ipod-shuffle-4g.py"
export GDK_BACKEND=x11
export GSK_RENDERER=cairo
export DISPLAY="${DISPLAY:-:77}"

build_demo() {
    rm -rf "$DEMO"
    /usr/bin/python3 "$REPO/tools/demo-library.py" "$DEMO" >/dev/null
}

# A playlist the device holds and that lists nothing here: synced once, emptied
# since. The device copy is the file the sync itself wrote for Morning Ride,
# under the other name; the one here is written by the app's own writer.
add_emptied_playlist() {
    cp "$DEMO/MAX SHUFFLE/Morning Ride.m3u" "$DEMO/MAX SHUFFLE/Old Mixtape.m3u"
    HOME="$DEMO/home" /usr/bin/python3 - "$REPO" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ipod_gui.playlists import create_local_playlist
root = Path.home() / "Music" / "Playlists"
if create_local_playlist(root, "Old Mixtape") is None:
    raise SystemExit("could not make the emptied playlist")
PY
}

run() {
    local label="$1" speech="$2"
    shift 2
    local path="$REPO/tests/bin:$SHIM"
    if [ "$speech" = speech ]; then
        path="$REPO/tests/bin:$PATH"
    fi
    echo "== $label =="
    PATH="$path" /usr/bin/python3 "$DRIVER" \
        --repo "$REPO" --demo "$DEMO" --out "$OUT" --label "$label" "$@" \
        2> >(grep -v 'Adwaita-WARNING\|^$' >&2)
}

# 1. The bug, with the pre-change file in place: the rename is confirmed, the
#    playlist leaves the iPod, and nothing is queued to put it back.
build_demo
git -C "$REPO" show "$BASE_COMMIT:ipod_gui/playlist_view.py" \
    > "$REPO/ipod_gui/playlist_view.py"
trap 'git -C "$REPO" checkout -- ipod_gui/playlist_view.py' EXIT
run 01-before-no-speech-engine nospeech \
    --playlist "Morning Ride" --to "Evening Ride" \
    --shot 01-before-no-speech-engine-confirmation.png
git -C "$REPO" checkout -- ipod_gui/playlist_view.py
trap - EXIT

# 2. The same press, same machine, with the change in place: refused.
build_demo
run 02-after-no-speech-engine nospeech \
    --playlist "Morning Ride" --to "Evening Ride" \
    --shot 02-after-no-speech-engine-refused.png

# 3. The carve-out: an empty playlist the device holds is still renamed here,
#    and the old name still comes off the iPod.
build_demo
add_emptied_playlist
run 03-after-empty-playlist-on-device nospeech \
    --playlist "Old Mixtape" --to "Old Mixtape Revived" \
    --shot 03-after-empty-playlist-confirmation.png

# 4. The machine that can speak: confirmed, staged, and the old name removed.
build_demo
run 04-after-with-speech-engine speech \
    --playlist "Morning Ride" --to "Evening Ride" \
    --shot 04-after-with-speech-engine-confirmation.png

# 5. And a playlist the iPod is not holding, on the speechless machine: never
#    refused, because there is nothing over there to strand.
build_demo
run 05-after-not-on-the-ipod nospeech \
    --playlist "Downloads" --to "Downloads Sorted" \
    --shot 05-after-not-on-the-ipod-confirmation.png

# 6. And what the README's other half claims: the ⋯ on a playlist page is where
#    Rename… lives, and a playlist only the device holds has no Rename… or
#    Delete… there to refuse in the first place.
build_demo
rm "$DEMO/home/Music/Playlists/Morning Ride.m3u"
run 09-a-playlist-made-here nospeech \
    --playlist "Downloads" --menu-only \
    --shot 09-a-playlist-made-heres-menu.png
run 09-after-a-device-only-playlist nospeech \
    --playlist "Morning Ride" --menu-only \
    --shot 09-after-a-device-only-playlists-menu.png

echo "all scenarios ran"
