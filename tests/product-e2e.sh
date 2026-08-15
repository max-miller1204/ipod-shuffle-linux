#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_PATH="$PATH"

# Everything else the installer sections reach for is a coreutil that is
# always there. uv is not, and it is what builds the environment install.sh
# installs into, so say so here rather than a thousand lines in as a symlink
# to nothing.
command -v uv >/dev/null 2>&1 || {
    echo "uv is required to run this suite; see https://docs.astral.sh/uv/" >&2
    exit 1
}
REAL_UV="$(command -v uv)"

# And the GTK4 bindings, for the same reason. The launcher fallback and the
# report an install ends with are both checked here against the distro
# interpreter, and neither has a right answer on a machine where that
# interpreter cannot really drive GTK4. Stand-in bindings decide the optional
# GStreamer answers further down, but they cannot stand in here: what those
# two checks are reading is a real environment built with the distro's site
# packages, which is the whole of what the bindings reach the project through.
if ! /usr/bin/python3 - <<'GTK_PROBE' >/dev/null 2>&1
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
GTK_PROBE
then
    echo "the GTK4 bindings are required to run this suite; install" \
        "python3-gi, gir1.2-gtk-4.0 and gir1.2-adw-1 for /usr/bin/python3" >&2
    exit 1
fi

# Defaults to a temporary directory so the suite can be run with no setup.
# Set EVIDENCE_DIR to keep the artefacts somewhere durable for inspection.
EVIDENCE_DIR="${EVIDENCE_DIR:-$(mktemp -d)}"
mkdir -p "$EVIDENCE_DIR"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

IPOD="$TEST_ROOT/Alex's iPod"
SOURCE="$TEST_ROOT/Road Trip"
BACKUP="$TEST_ROOT/backup"
DB_RECORD="$EVIDENCE_DIR/database-invocations.jsonl"

mkdir -p \
    "$IPOD/iPod_Control/iTunes" \
    "$IPOD/iPod_Control/Music/Old Album" \
    "$IPOD/iPod_Control/Speakable/System" \
    "$IPOD/iPod_Control/Device" \
    "$SOURCE/Disc 1"
printf 'spoken battery prompt\n' > "$IPOD/iPod_Control/Speakable/System/battery.wav"
printf 'device identity\n' > "$IPOD/iPod_Control/Device/SysInfo"
printf 'old song\n' > "$IPOD/iPod_Control/Music/Old Album/OLD1.mp3"
printf 'old database\n' > "$IPOD/iPod_Control/iTunes/iTunesDB"
printf 'old preferences\n' > "$IPOD/iPod_Control/iTunes/iTunesPrefs"
printf 'new song\n' > "$SOURCE/Disc 1/01 - Highway.mp3"
printf 'unsupported\n' > "$SOURCE/Disc 1/cover.flac"
: > "$DB_RECORD"

export IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py"
export IPOD_VENV_PYTHON="/usr/bin/python3"
export FAKE_DB_RECORD="$DB_RECORD"

run_approved() {
    local script="$1" plan identity token arg
    local -a plan_args=()
    shift
    for arg in "$@"; do
        case "$arg" in
            --progress-json|--progress-json=*) ;;
            *) plan_args+=("$arg") ;;
        esac
    done
    plan="$("$script" --dry-run "${plan_args[@]}")"
    identity="$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["device"]["identity"])' "$plan")"
    token="$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["confirmationToken"])' "$plan")"
    "$script" --expect-device "$identity" --confirm-token "$token" "$@"
}

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --dir-playlists \
    --playlist-voiceover \
    "$SOURCE" > "$EVIDENCE_DIR/sync-and-playlists.txt" 2>&1

test -f "$IPOD/iPod_Control/Music/Road Trip/Disc 1/01 - Highway.mp3"
test ! -e "$IPOD/iPod_Control/Music/Road Trip/Disc 1/cover.flac"
diff -u <(printf '%s\n' \
    --auto-dir-playlists \
    -1 \
    --playlist-voiceover) \
    "$IPOD/iPod_Control/.sync-options"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --rebuild-only >> "$EVIDENCE_DIR/sync-and-playlists.txt" 2>&1

grep -Fq 'Reusing saved options: --auto-dir-playlists -1 --playlist-voiceover' \
    "$EVIDENCE_DIR/sync-and-playlists.txt"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --id3-playlists \
    --voiceover \
    --playlist-voiceover \
    --rebuild-only > "$EVIDENCE_DIR/gui-compatible-options.txt" 2>&1

diff -u <(printf '%s\n' \
    --auto-id3-playlists \
    '{artist}' \
    --track-voiceover \
    --playlist-voiceover) \
    "$IPOD/iPod_Control/.sync-options"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --id3-playlists='{genre}' \
    --voiceover \
    --playlist-voiceover \
    --rebuild-only >> "$EVIDENCE_DIR/gui-compatible-options.txt" 2>&1

/usr/bin/python3 "$ROOT/tests/gui-state-smoke.py" "$IPOD" \
    > "$EVIDENCE_DIR/gui-playlist-state.json"

/usr/bin/python3 "$ROOT/tests/gui-detection-smoke.py" \
    > "$EVIDENCE_DIR/gui-detection.json"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --forget-options \
    --rebuild-only > "$EVIDENCE_DIR/forget-options.txt" 2>&1
test ! -e "$IPOD/iPod_Control/.sync-options"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --id3-playlists \
    --rebuild-only > "$EVIDENCE_DIR/playlist-without-voiceover-warning.txt" 2>&1
grep -Fq 'Playlists without --playlist-voiceover will be unnamed on the device.' \
    "$EVIDENCE_DIR/playlist-without-voiceover-warning.txt"

EFFECTIVE_OPTIONS_IPOD="$TEST_ROOT/effective-options-target"
mkdir -p \
    "$EFFECTIVE_OPTIONS_IPOD/iPod_Control/iTunes" \
    "$EFFECTIVE_OPTIONS_IPOD/iPod_Control/Music" \
    "$EFFECTIVE_OPTIONS_IPOD/iPod_Control/Speakable"
"$ROOT/ipod-sync.sh" \
    --ipod "$EFFECTIVE_OPTIONS_IPOD" \
    --dir-playlists \
    "$SOURCE" > "$EVIDENCE_DIR/saved-dir-playlists-setup.txt" 2>&1
"$ROOT/ipod-sync.sh" \
    --ipod "$EFFECTIVE_OPTIONS_IPOD" \
    --rebuild-only > "$EVIDENCE_DIR/saved-dir-playlists-warning.txt" 2>&1
grep -Fq 'Playlists without --playlist-voiceover will be unnamed on the device.' \
    "$EVIDENCE_DIR/saved-dir-playlists-warning.txt"
"$ROOT/ipod-sync.sh" \
    --ipod "$EFFECTIVE_OPTIONS_IPOD" \
    --id3-playlists \
    --rebuild-only > "$EVIDENCE_DIR/saved-id3-playlists-setup.txt" 2>&1
"$ROOT/ipod-sync.sh" \
    --ipod "$EFFECTIVE_OPTIONS_IPOD" \
    --rebuild-only > "$EVIDENCE_DIR/saved-id3-playlists-warning.txt" 2>&1
grep -Fq 'Playlists without --playlist-voiceover will be unnamed on the device.' \
    "$EVIDENCE_DIR/saved-id3-playlists-warning.txt"

# The GUI no longer offers a grouping, and passes only the two voiceover flags,
# on every machine. That is what has to retire a grouping saved by a version
# that did: the device above is still carrying --auto-id3-playlists, and a run
# given flags of its own overwrites the file rather than replaying it. Without
# this a user who had once chosen "By genre" would keep getting generated
# playlists with nothing left in the window to turn them off with.
"$ROOT/ipod-sync.sh" \
    --ipod "$EFFECTIVE_OPTIONS_IPOD" \
    --voiceover \
    --playlist-voiceover \
    --rebuild-only > "$EVIDENCE_DIR/gui-flags-clear-grouping.txt" 2>&1
diff -u <(printf '%s\n' \
    --track-voiceover \
    --playlist-voiceover) \
    "$EFFECTIVE_OPTIONS_IPOD/iPod_Control/.sync-options"

# --clear is destructive. A closed stdin refuses it even when --yes is passed,
# until the caller returns the token from the exact dry-run plan.
# On a throwaway device, because --clear deletes what later checks still expect
# to find on the shared one.
YES_SOURCE="$TEST_ROOT/yes-source"
YES_IPOD="$TEST_ROOT/clear-target"
mkdir -p "$YES_SOURCE" \
    "$YES_IPOD/iPod_Control/iTunes" \
    "$YES_IPOD/iPod_Control/Music/Existing" \
    "$YES_IPOD/iPod_Control/Speakable"
printf 'a track\n' > "$YES_SOURCE/track.mp3"
printf 'already there\n' > "$YES_IPOD/iPod_Control/Music/Existing/old.mp3"
printf '%s\n' '#EXTM3U' > "$YES_IPOD/Existing.M3U"

if "$ROOT/ipod-sync.sh" \
    --ipod "$YES_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null > "$EVIDENCE_DIR/clear-without-token.txt" 2>&1; then
    echo "--clear accepted --yes as machine authorization" >&2
    exit 1
fi
grep -Fq 'Destructive action refused: confirmation is not authorization' \
    "$EVIDENCE_DIR/clear-without-token.txt"
test -s "$YES_IPOD/iPod_Control/Music/Existing/old.mp3"
test -f "$YES_IPOD/Existing.M3U"

clear_plan="$("$ROOT/ipod-sync.sh" \
    --ipod "$YES_IPOD" \
    --clear \
    --yes \
    --dry-run \
    "$YES_SOURCE" < /dev/null)"
/usr/bin/python3 -c '
import json, sys
plan = json.loads(sys.argv[1])
assert plan["action"] == "sync"
assert plan["destructive"] is True
assert plan["arguments"][-1] == sys.argv[2]
assert plan["confirmationToken"]
' "$clear_plan" "$YES_SOURCE"
run_approved "$ROOT/ipod-sync.sh" \
    --ipod "$YES_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null > "$EVIDENCE_DIR/clear-with-token.txt" 2>&1
test -f "$YES_IPOD/iPod_Control/Music/yes-source/track.mp3"
test ! -e "$YES_IPOD/iPod_Control/Music/Existing/old.mp3"
test ! -e "$YES_IPOD/Existing.M3U"

PLAYLIST_ONLY_IPOD="$TEST_ROOT/playlist-only-clear-target"
mkdir -p \
    "$PLAYLIST_ONLY_IPOD/iPod_Control/iTunes" \
    "$PLAYLIST_ONLY_IPOD/iPod_Control/Music" \
    "$PLAYLIST_ONLY_IPOD/iPod_Control/Speakable"
printf '%s\n' '[playlist]' > "$PLAYLIST_ONLY_IPOD/Only List.PLS"
if "$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_ONLY_IPOD" \
    --clear \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/clear-playlist-only-without-yes.txt" 2>&1; then
    echo "--clear removed a playlist without confirmation" >&2
    exit 1
fi
grep -Fq 'Destructive action refused: confirmation is not authorization' \
    "$EVIDENCE_DIR/clear-playlist-only-without-yes.txt"
test -f "$PLAYLIST_ONLY_IPOD/Only List.PLS"
run_approved "$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_ONLY_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/clear-playlist-only-with-token.txt" 2>&1
test ! -e "$PLAYLIST_ONLY_IPOD/Only List.PLS"

# Everything the handshake refuses, on one device that starts with two tracks.
# A machine caller has exactly one way through - the plan it was printed,
# returned unaltered, against the device it was printed for - and the three
# ways past it that look plausible from outside are a token that was guessed,
# a token that belonged to another plan, and a token that belonged to the iPod
# which used to be plugged in here. Each of them is one keystroke away from
# the deletion the plan described.
HANDSHAKE_IPOD="$TEST_ROOT/handshake-target"
mkdir -p \
    "$HANDSHAKE_IPOD/iPod_Control/iTunes" \
    "$HANDSHAKE_IPOD/iPod_Control/Music/Album" \
    "$HANDSHAKE_IPOD/iPod_Control/Speakable/System" \
    "$HANDSHAKE_IPOD/iPod_Control/Device"
printf 'spoken battery prompt\n' \
    > "$HANDSHAKE_IPOD/iPod_Control/Speakable/System/battery.wav"
printf 'the iPod the caller inspected\n' \
    > "$HANDSHAKE_IPOD/iPod_Control/Device/SysInfo"
printf 'keep me\n' > "$HANDSHAKE_IPOD/iPod_Control/Music/Album/01 - Keep.mp3"
printf 'keep me too\n' \
    > "$HANDSHAKE_IPOD/iPod_Control/Music/Album/02 - Also Keep.mp3"

# What a refusal is worth is read off the volume rather than out of the
# sentence it printed: a run that says it refused and deletes anyway is the
# failure all of this exists to prevent, and it would print the same words.
device_state() {
    find "$1" -mindepth 1 -printf '%y %P\n' | sort
    find "$1" -type f -exec md5sum {} + | sed "s|$1/||" | sort
}
device_state "$HANDSHAKE_IPOD" > "$EVIDENCE_DIR/handshake-device-before.txt"

handshake_sync_plan="$("$ROOT/ipod-sync.sh" \
    --ipod "$HANDSHAKE_IPOD" --clear --yes --dry-run "$YES_SOURCE" \
    < /dev/null 2> "$EVIDENCE_DIR/handshake-plan-sync.stderr.txt")"
handshake_remove_plan="$("$ROOT/ipod-remove.sh" \
    --ipod "$HANDSHAKE_IPOD" --yes --dry-run 'Album/01 - Keep.mp3' \
    < /dev/null 2> "$EVIDENCE_DIR/handshake-plan-remove.stderr.txt")"
handshake_wipe_plan="$("$ROOT/ipod-wipe.sh" \
    --ipod "$HANDSHAKE_IPOD" --yes --dry-run \
    < /dev/null 2> "$EVIDENCE_DIR/handshake-plan-wipe.stderr.txt")"
printf '%s\n' "$handshake_sync_plan" "$handshake_remove_plan" \
    "$handshake_wipe_plan" > "$EVIDENCE_DIR/handshake-plans.ndjson"

# Three plans for the same mounted iPod: the same device either way, a
# separate approval each, and a volume none of them wrote to.
/usr/bin/python3 -c '
import json, sys
plans = [json.loads(line) for line in open(sys.argv[1])]
mount = sys.argv[2]
assert [p["action"] for p in plans] == ["sync", "remove", "wipe"], plans
for plan in plans:
    assert plan["destructive"] is True, plan
    assert plan["device"]["mount"] == mount, plan
    assert plan["device"]["identity"], plan
    assert plan["confirmationToken"], plan
assert len({p["device"]["identity"] for p in plans}) == 1, plans
assert len({p["confirmationToken"] for p in plans}) == 3, plans
' "$EVIDENCE_DIR/handshake-plans.ndjson" "$HANDSHAKE_IPOD"
device_state "$HANDSHAKE_IPOD" > "$EVIDENCE_DIR/handshake-device-after-plans.txt"
diff -u "$EVIDENCE_DIR/handshake-device-before.txt" \
    "$EVIDENCE_DIR/handshake-device-after-plans.txt"

handshake_identity="$(/usr/bin/python3 -c \
    'import json,sys; print(json.loads(sys.argv[1])["device"]["identity"])' \
    "$handshake_remove_plan")"
handshake_remove_token="$(/usr/bin/python3 -c \
    'import json,sys; print(json.loads(sys.argv[1])["confirmationToken"])' \
    "$handshake_remove_plan")"

# A token that is not this plan's is not a weaker approval, it is none: a
# caller that invents one, or that keeps returning the one it was given
# yesterday, is a caller that never read what it is about to do.
handshake_wrong=0
"$ROOT/ipod-remove.sh" \
    --ipod "$HANDSHAKE_IPOD" \
    --yes \
    --confirm-token \
    0000000000000000000000000000000000000000000000000000000000000000 \
    'Album/01 - Keep.mp3' < /dev/null \
    > "$EVIDENCE_DIR/handshake-wrong-token.txt" 2>&1 || handshake_wrong=$?
test "$handshake_wrong" -eq 7
grep -Fq 'Destructive action refused: confirmation is not authorization' \
    "$EVIDENCE_DIR/handshake-wrong-token.txt"

# The token is this plan's, and the run is not: one names the other track, the
# other adds an option. Approval that survived either would be approval of the
# words "delete something" rather than of the deletion the caller read.
handshake_changed_target=0
"$ROOT/ipod-remove.sh" \
    --ipod "$HANDSHAKE_IPOD" \
    --yes \
    --confirm-token "$handshake_remove_token" \
    'Album/02 - Also Keep.mp3' < /dev/null \
    > "$EVIDENCE_DIR/handshake-changed-target.txt" 2>&1 \
    || handshake_changed_target=$?
test "$handshake_changed_target" -eq 7
grep -Fq 'Destructive action refused: confirmation is not authorization' \
    "$EVIDENCE_DIR/handshake-changed-target.txt"

handshake_changed_options=0
"$ROOT/ipod-remove.sh" \
    --ipod "$HANDSHAKE_IPOD" \
    --yes \
    --eject \
    --confirm-token "$handshake_remove_token" \
    'Album/01 - Keep.mp3' < /dev/null \
    > "$EVIDENCE_DIR/handshake-changed-options.txt" 2>&1 \
    || handshake_changed_options=$?
test "$handshake_changed_options" -eq 7
grep -Fq 'Destructive action refused: confirmation is not authorization' \
    "$EVIDENCE_DIR/handshake-changed-options.txt"

device_state "$HANDSHAKE_IPOD" > "$EVIDENCE_DIR/handshake-device-after-refusals.txt"
diff -u "$EVIDENCE_DIR/handshake-device-before.txt" \
    "$EVIDENCE_DIR/handshake-device-after-refusals.txt"

# The same command with the plan's own token and the plan's own device goes
# through, which is what makes the three refusals above answers to what was
# wrong with them rather than a script that refuses everything.
"$ROOT/ipod-remove.sh" \
    --ipod "$HANDSHAKE_IPOD" \
    --yes \
    --expect-device "$handshake_identity" \
    --confirm-token "$handshake_remove_token" \
    'Album/01 - Keep.mp3' < /dev/null \
    > "$EVIDENCE_DIR/handshake-approved-remove.txt" 2>&1
test ! -e "$HANDSHAKE_IPOD/iPod_Control/Music/Album/01 - Keep.mp3"
test -s "$HANDSHAKE_IPOD/iPod_Control/Music/Album/02 - Also Keep.mp3"

# The iPod that was inspected, unplugged and replaced at the same mount point
# by another one before the run it authorized arrives. A volume formatted
# without a filesystem UUID answers with the device's own SysInfo instead,
# which is what a second iPod plugged in here changes; findmnt is answered the
# way it answers for such a volume, so this reads the same wherever the
# suite's temporary directory happens to live.
STALE_PATH="$TEST_ROOT/stale-device-bin"
mkdir -p "$STALE_PATH"
printf '%s\n' '#!/usr/bin/env bash' 'printf -- "-\n"' > "$STALE_PATH/findmnt"
chmod +x "$STALE_PATH/findmnt"
stale_plan="$(PATH="$STALE_PATH:$BASE_PATH" "$ROOT/ipod-wipe.sh" \
    --ipod "$HANDSHAKE_IPOD" --yes --dry-run < /dev/null 2>/dev/null)"
stale_identity="$(/usr/bin/python3 -c \
    'import json,sys; print(json.loads(sys.argv[1])["device"]["identity"])' \
    "$stale_plan")"
stale_token="$(/usr/bin/python3 -c \
    'import json,sys; print(json.loads(sys.argv[1])["confirmationToken"])' \
    "$stale_plan")"
case "$stale_identity" in
    sysinfo:?*) ;;
    *) echo "a volume with no UUID did not identify itself by SysInfo" >&2
       exit 1 ;;
esac
printf 'a different iPod\n' \
    > "$HANDSHAKE_IPOD/iPod_Control/Device/SysInfo"
device_state "$HANDSHAKE_IPOD" > "$EVIDENCE_DIR/handshake-device-swapped.txt"

# Every script that changes the device asks, including a plain sync with no
# --clear in it: copying an album onto the wrong iPod deletes nothing and is
# still not what the caller approved.
for stale_script in ipod-sync.sh ipod-remove.sh ipod-wipe.sh; do
    case "$stale_script" in
        ipod-sync.sh)   stale_args=("$YES_SOURCE") ;;
        ipod-remove.sh) stale_args=(--yes --confirm-token "$stale_token"
                                    'Album/02 - Also Keep.mp3') ;;
        ipod-wipe.sh)   stale_args=(--yes --confirm-token "$stale_token") ;;
    esac
    stale_refused=0
    PATH="$STALE_PATH:$BASE_PATH" "$ROOT/$stale_script" \
        --ipod "$HANDSHAKE_IPOD" \
        --expect-device "$stale_identity" \
        "${stale_args[@]}" < /dev/null \
        > "$EVIDENCE_DIR/handshake-stale-$stale_script.txt" 2>&1 \
        || stale_refused=$?
    if (( stale_refused == 0 )); then
        echo "$stale_script acted on an iPod that had been replaced" >&2
        exit 1
    fi
    grep -Fq "Expected device '$stale_identity'" \
        "$EVIDENCE_DIR/handshake-stale-$stale_script.txt"
done
device_state "$HANDSHAKE_IPOD" > "$EVIDENCE_DIR/handshake-device-after-stale.txt"
diff -u "$EVIDENCE_DIR/handshake-device-swapped.txt" \
    "$EVIDENCE_DIR/handshake-device-after-stale.txt"

/usr/bin/python3 "$ROOT/tests/operation-authorization-pty.py" \
    "$ROOT" "$EVIDENCE_DIR"

# A plan is the whole of stdout, on a device that has something to say first.
# Every sync that was given options saves them, and the next run announces the
# ones it is replaying: printed where the plan goes, that sentence leaves the
# caller with a document json.loads refuses and so with no token, and a --clear
# it can never authorize. The options themselves still belong in the plan,
# because they are part of what the run will do.
SAVED_OPTIONS_IPOD="$TEST_ROOT/saved-options-clear-target"
mkdir -p \
    "$SAVED_OPTIONS_IPOD/iPod_Control/iTunes" \
    "$SAVED_OPTIONS_IPOD/iPod_Control/Music" \
    "$SAVED_OPTIONS_IPOD/iPod_Control/Speakable"
"$ROOT/ipod-sync.sh" \
    --ipod "$SAVED_OPTIONS_IPOD" \
    --dir-playlists \
    --playlist-voiceover \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/saved-options-first-sync.txt" 2>&1
test -s "$SAVED_OPTIONS_IPOD/iPod_Control/.sync-options"

saved_options_plan="$("$ROOT/ipod-sync.sh" \
    --ipod "$SAVED_OPTIONS_IPOD" \
    --clear \
    --yes \
    --dry-run \
    "$YES_SOURCE" < /dev/null \
    2> "$EVIDENCE_DIR/saved-options-plan.stderr.txt")"
/usr/bin/python3 -c '
import json, sys
plan = json.loads(sys.argv[1])
assert plan["action"] == "sync"
assert plan["destructive"] is True
assert "--auto-dir-playlists" in plan["arguments"], plan["arguments"]
assert "--playlist-voiceover" in plan["arguments"], plan["arguments"]
assert "existing-tracks=1" in plan["arguments"], plan["arguments"]
assert plan["confirmationToken"]
' "$saved_options_plan"
grep -Fq 'Reusing saved options' "$EVIDENCE_DIR/saved-options-plan.stderr.txt"
test -s "$SAVED_OPTIONS_IPOD/iPod_Control/Music/yes-source/track.mp3"
run_approved "$ROOT/ipod-sync.sh" \
    --ipod "$SAVED_OPTIONS_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/saved-options-clear-with-token.txt" 2>&1
test -s "$SAVED_OPTIONS_IPOD/iPod_Control/Music/yes-source/track.mp3"

# An iPod that has never held music has no iPod_Control/Music directory: the
# sync creates it. Counting what a --clear would delete happens before that,
# and a count of a directory that is not there is none rather than a run that
# stops with nothing printed to say why.
NO_MUSIC_IPOD="$TEST_ROOT/no-music-dir-target"
mkdir -p \
    "$NO_MUSIC_IPOD/iPod_Control/iTunes" \
    "$NO_MUSIC_IPOD/iPod_Control/Speakable"
no_music_plan="$("$ROOT/ipod-sync.sh" \
    --ipod "$NO_MUSIC_IPOD" \
    --clear \
    --yes \
    --dry-run \
    "$YES_SOURCE" < /dev/null)"
/usr/bin/python3 -c '
import json, sys
plan = json.loads(sys.argv[1])
assert "existing-tracks=0" in plan["arguments"], plan["arguments"]
assert "existing-playlists=0" in plan["arguments"], plan["arguments"]
' "$no_music_plan"
test ! -e "$NO_MUSIC_IPOD/iPod_Control/Music"
run_approved "$ROOT/ipod-sync.sh" \
    --ipod "$NO_MUSIC_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/clear-no-music-dir.txt" 2>&1
test -s "$NO_MUSIC_IPOD/iPod_Control/Music/yes-source/track.mp3"

# The same device wiped with a backup: there is nothing to copy, so the backup
# has no Music directory either, and verifying none copied against none there
# is a pass. The Speakable prompts are still what a wipe keeps.
NO_MUSIC_WIPE_IPOD="$TEST_ROOT/no-music-dir-wipe-target"
mkdir -p \
    "$NO_MUSIC_WIPE_IPOD/iPod_Control/iTunes" \
    "$NO_MUSIC_WIPE_IPOD/iPod_Control/Speakable/System"
printf 'spoken battery prompt\n' \
    > "$NO_MUSIC_WIPE_IPOD/iPod_Control/Speakable/System/battery.wav"
run_approved "$ROOT/ipod-wipe.sh" \
    --ipod "$NO_MUSIC_WIPE_IPOD" \
    --backup "$TEST_ROOT/no-music-backup" \
    --yes < /dev/null \
    > "$EVIDENCE_DIR/wipe-no-music-dir.txt" 2>&1
grep -Fq 'Backup verified: 0 track(s)' "$EVIDENCE_DIR/wipe-no-music-dir.txt"
test -s "$NO_MUSIC_WIPE_IPOD/iPod_Control/Speakable/System/battery.wav"
test -d "$NO_MUSIC_WIPE_IPOD/iPod_Control/Music"

# A library from before UTF-8 is still a library. A folder named in latin-1 is
# a sequence of bytes no decoder claims, and it reaches the plan as an
# argument like any other: a run that could not write that name out would end
# an ordinary sync where the copy should have been, and nothing about the
# names on a person's disk is theirs to change.
LATIN1_SOURCE="$TEST_ROOT/$(printf 'Bj\xf6rk')"
LATIN1_IPOD="$TEST_ROOT/latin1-clear-target"
mkdir -p "$LATIN1_SOURCE" \
    "$LATIN1_IPOD/iPod_Control/iTunes" \
    "$LATIN1_IPOD/iPod_Control/Music" \
    "$LATIN1_IPOD/iPod_Control/Speakable"
printf 'a song\n' > "$LATIN1_SOURCE/01 - Human Behaviour.mp3"
latin1_plan="$("$ROOT/ipod-sync.sh" \
    --ipod "$LATIN1_IPOD" \
    --clear \
    --yes \
    --dry-run \
    "$LATIN1_SOURCE" < /dev/null \
    2> "$EVIDENCE_DIR/sync-latin1-plan.stderr.txt")"
/usr/bin/python3 -c '
import json, sys
plan = json.loads(sys.argv[1])
assert plan["arguments"][-1] == sys.argv[2], plan["arguments"]
assert plan["confirmationToken"]
' "$latin1_plan" "$LATIN1_SOURCE"
run_approved "$ROOT/ipod-sync.sh" \
    --ipod "$LATIN1_IPOD" \
    --clear \
    --yes \
    "$LATIN1_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/sync-latin1-source.txt" 2>&1
test -s "$LATIN1_IPOD/iPod_Control/Music/$(printf 'Bj\xf6rk')/01 - Human Behaviour.mp3"

# --yes has to answer every prompt, not just the caller's own. assert_shuffle
# asks its own question from inside lib.sh when a volume has no Speakable
# directory, and a script that only guarded its local confirm still stopped
# dead there - precisely when it is running unattended.
NOT_A_SHUFFLE="$TEST_ROOT/not-a-shuffle"
mkdir -p "$NOT_A_SHUFFLE/iPod_Control/iTunes" "$NOT_A_SHUFFLE/iPod_Control/Music"
for script in ipod-sync.sh ipod-remove.sh ipod-wipe.sh; do
    case "$script" in
        ipod-sync.sh)   args=(--yes "$YES_SOURCE") ;;
        ipod-remove.sh) args=(--yes --list) ;;
        ipod-wipe.sh)   args=(--yes) ;;
    esac
    if [[ "$script" == ipod-wipe.sh ]]; then
        run_approved "$ROOT/$script" --ipod "$NOT_A_SHUFFLE" "${args[@]}" \
            < /dev/null \
            > "$EVIDENCE_DIR/no-speakable-$script.stdout.txt" \
            2> "$EVIDENCE_DIR/no-speakable-$script.stderr.txt" \
            || { echo "$script approval stopped at the Speakable prompt" >&2; exit 1; }
    else
        "$ROOT/$script" --ipod "$NOT_A_SHUFFLE" "${args[@]}" \
            < /dev/null \
            > "$EVIDENCE_DIR/no-speakable-$script.stdout.txt" \
            2> "$EVIDENCE_DIR/no-speakable-$script.stderr.txt" \
            || { echo "$script --yes stopped at the Speakable prompt" >&2; exit 1; }
    fi
    grep -Fq 'Continue anyway?' \
        "$EVIDENCE_DIR/no-speakable-$script.stderr.txt"
    if [[ "$script" == ipod-remove.sh ]]; then
        diff -u <(printf '%s\n' 'yes-source/track.mp3') \
            "$EVIDENCE_DIR/no-speakable-$script.stdout.txt"
    fi
done

printf '%s\n' \
    --auto-id3-playlists \
    '{artist}' \
    --playlist-voiceover > "$IPOD/iPod_Control/.sync-options"

run_approved "$ROOT/ipod-wipe.sh" \
    --ipod "$IPOD" \
    --backup "$BACKUP" \
    --yes > "$EVIDENCE_DIR/wipe-with-backup.txt" 2>&1

test -z "$(find "$IPOD/iPod_Control/Music" -type f -print -quit)"
test ! -e "$IPOD/iPod_Control/iTunes/iTunesDB"
test ! -e "$IPOD/iPod_Control/iTunes/iTunesPrefs"
test ! -e "$IPOD/iPod_Control/.sync-options"
test -s "$IPOD/iPod_Control/iTunes/iTunesSD"
test -s "$IPOD/iPod_Control/Speakable/System/battery.wav"
test -s "$IPOD/iPod_Control/Device/SysInfo"
test -s "$BACKUP/Music/Old Album/OLD1.mp3"
test -s "$BACKUP/Music/Road Trip/Disc 1/01 - Highway.mp3"
test -s "$BACKUP/iTunes/iTunesDB"

# The stub reproduces the behaviour that caused the original bug rather than
# returning JSON whatever it is asked for. Raw mode escapes a space as \x20,
# which is why mount detection cannot use it: an iPod called "Alex's iPod" came
# back as a path that matched nothing on disk. Emulating both output modes
# means reverting lib.sh to raw mode fails this assertion instead of silently
# being handed JSON it never requested.
(
    source "$ROOT/lib.sh"
    findmnt() {
        for arg in "$@"; do
            if [[ "$arg" == "--json" ]]; then
                printf '%s\n' \
                    '{"filesystems":[{"target":"/run/media/alex/Alex'\''s iPod"}]}'
                return 0
            fi
        done
        printf '%s\n' '/run/media/alex/Alex'\''s\x20iPod'
    }
    list_vfat_mounts
) > "$EVIDENCE_DIR/findmnt-space-path.txt"
grep -Fxq "/run/media/alex/Alex's iPod" "$EVIDENCE_DIR/findmnt-space-path.txt"
test ! -s "$EVIDENCE_DIR/findmnt-space-path.txt" -o \
    -z "$(grep -F 'x20' "$EVIDENCE_DIR/findmnt-space-path.txt" || true)"

(
    source "$ROOT/lib.sh"
    mkdir -p "$TEST_ROOT/no-udisks"
    # Emptying the search path is the point: it reproduces a system that ships
    # gdbus but not udisksctl, so the D-Bus fallback is exercised.
    # Confined to this subshell.
    # shellcheck disable=SC2123,SC2030
    PATH="$TEST_ROOT/no-udisks"
    gdbus() {
        printf '%s\n' "$*" > "$EVIDENCE_DIR/gdbus-call.txt"
    }
    ipod_unmount /dev/sdz
) > "$EVIDENCE_DIR/gdbus-fallback.txt"
grep -Fq \
    'org.freedesktop.UDisks2.Filesystem.Unmount {}' \
    "$EVIDENCE_DIR/gdbus-call.txt"

# The probe has to report absence rather than guessing a runtime that is not
# installed, because ipod-fetch.sh turns a negative into an explicit warning.
# Silently naming one would restore the original failure: downloads that die
# with HTTP 403 while every other part of the run looks healthy.
(
    source "$ROOT/lib.sh"
    mkdir -p "$TEST_ROOT/no-js"
    # Confined to this subshell.
    # shellcheck disable=SC2030,SC2031,SC2123
    PATH="$TEST_ROOT/no-js"
    if js_runtime; then
        echo "js_runtime named a runtime that is not installed" >&2
        exit 1
    fi
    echo "absent"
) > "$EVIDENCE_DIR/js-runtime-absent.txt"
grep -Fxq "absent" "$EVIDENCE_DIR/js-runtime-absent.txt"

# The installer must report a missing JavaScript runtime rather than offering
# the distribution's nodejs package to fix it. Ubuntu can provide nodejs 18,
# below yt-dlp's floor of 22, so installing it spends a privileged apt
# transaction on a runtime js_runtime() then rejects, leaving downloads
# failing with the same HTTP 403 while appearing to have been dealt with.
#
# Stop the installer immediately after the dependency report so this check
# cannot install or download anything.
INSTALLER_PATH="$TEST_ROOT/installer-path"
mkdir -p "$INSTALLER_PATH"
for command in bash cp dirname git mkdir python3 readlink touch uv; do
    ln -s "$(command -v "$command")" "$INSTALLER_PATH/$command"
done
INSTALL_BLOCKER="$TEST_ROOT/install-blocker"
: > "$INSTALL_BLOCKER"
# XDG_DATA_HOME is redirected in every installer run here: the desktop-entry
# step writes into it, and a test must never touch the real one.
if PATH="$INSTALLER_PATH" IPOD_TOOLS_DIR="$INSTALL_BLOCKER" \
    XDG_DATA_HOME="$TEST_ROOT/xdg-data-blocked" \
    "$ROOT/install.sh" --no-system \
    > "$EVIDENCE_DIR/install-no-runtime.txt" 2>&1; then
    echo "installer unexpectedly continued past the dependency report" >&2
    exit 1
fi
if grep -Eq 'apt install .*nodejs' "$EVIDENCE_DIR/install-no-runtime.txt"; then
    echo "installer offered a distribution nodejs too old to satisfy yt-dlp" >&2
    exit 1
fi
grep -Fq 'https://github.com/yt-dlp/yt-dlp/wiki/EJS' \
    "$EVIDENCE_DIR/install-no-runtime.txt"
grep -Fq 'YouTube downloads need Deno' \
    "$EVIDENCE_DIR/install-no-runtime.txt"

# The app-grid entry is generated before anything that needs the network and
# carries this checkout's absolute path, which is what heals a stale entry
# after the repository moves. Blocking the tools dir stops the run right
# after the entry is written.
DESKTOP_DATA="$TEST_ROOT/xdg-data"
ROOT_REAL="$(readlink -f "$ROOT")"
if XDG_DATA_HOME="$DESKTOP_DATA" IPOD_TOOLS_DIR="$INSTALL_BLOCKER" \
    "$ROOT/install.sh" --no-system \
    > "$EVIDENCE_DIR/install-desktop-entry.txt" 2>&1; then
    echo "installer unexpectedly continued past the blocked tools dir" >&2
    exit 1
fi
DESKTOP_FILE="$DESKTOP_DATA/applications/io.github.max_miller1204.IpodShuffle.desktop"
test -f "$DESKTOP_FILE"
grep -Fxq "Exec=\"$ROOT_REAL/ipod-gui.sh\"" "$DESKTOP_FILE"
grep -Fxq 'Icon=io.github.max_miller1204.IpodShuffle' "$DESKTOP_FILE"
grep -Fxq 'Terminal=false' "$DESKTOP_FILE"
test -s "$DESKTOP_DATA/icons/hicolor/scalable/apps/io.github.max_miller1204.IpodShuffle.svg"
grep -Fq 'Desktop entry installed' "$EVIDENCE_DIR/install-desktop-entry.txt"

NO_GUI_PYTHON="$TEST_ROOT/no-gui-python"
mkdir -p "$NO_GUI_PYTHON"
printf '%s\n' 'raise ImportError("GUI bindings hidden for test")' \
    > "$NO_GUI_PYTHON/gi.py"
FAILING_PRIVILEGE_PATH="$TEST_ROOT/failing-privilege-path"
mkdir -p "$FAILING_PRIVILEGE_PATH"
printf '%s\n' '#!/bin/sh' 'exit 1' > "$FAILING_PRIVILEGE_PATH/sudo"
chmod +x "$FAILING_PRIVILEGE_PATH/sudo"
if PATH="$FAILING_PRIVILEGE_PATH:$BASE_PATH" \
    PYTHONPATH="$NO_GUI_PYTHON" \
    XDG_DATA_HOME="$DESKTOP_DATA" \
    DISPLAY='' WAYLAND_DISPLAY='' \
    "$ROOT/install.sh" --yes \
    > "$EVIDENCE_DIR/install-desktop-entry-removed.txt" 2>&1; then
    echo "installer unexpectedly survived the failed privilege request" >&2
    exit 1
fi
test ! -e "$DESKTOP_FILE"
test -s "$DESKTOP_DATA/icons/hicolor/scalable/apps/io.github.max_miller1204.IpodShuffle.svg"
grep -Fq 'Desktop entry removed because the GUI dependencies are unavailable' \
    "$EVIDENCE_DIR/install-desktop-entry-removed.txt"
grep -Fq 'Requesting privileges' \
    "$EVIDENCE_DIR/install-desktop-entry-removed.txt"

# Preview playback is offered like any other optional dependency rather than
# only documented, and the offer follows the capability rather than the machine
# having GTK. The two are separate packages, so a window that runs perfectly
# well can still have no way to play a note; a check that only ever saw a
# machine with neither could not tell those apart.
#
# Stand-in bindings decide the answer, because CI installs GTK4 and never
# GStreamer, so the present case would otherwise never be exercised at all.
# Both runs stop at the blocked tools dir, immediately after the report.
GST_STUB_ROOT="$TEST_ROOT/gi-stubs"
for variant in gtk-only gtk-and-gst; do
    mkdir -p "$GST_STUB_ROOT/$variant/gi/repository"
    printf '%s\n' \
        'class _Namespace:' \
        '    """Enough of a namespace for the probes to import."""' \
        '' \
        '' \
        'Gtk = _Namespace()' \
        'Adw = _Namespace()' \
        > "$GST_STUB_ROOT/$variant/gi/repository/__init__.py"
done

# GTK4 answers, GStreamer is absent: exactly the machine the packages are for.
printf '%s\n' \
    'def require_version(namespace, version):' \
    '    if namespace == "Gst":' \
    '        raise ValueError("Namespace Gst is not available")' \
    > "$GST_STUB_ROOT/gtk-only/gi/__init__.py"

printf '%s\n' \
    'def require_version(namespace, version):' \
    '    """Every namespace these probes ask for is installed here."""' \
    > "$GST_STUB_ROOT/gtk-and-gst/gi/__init__.py"
printf '%s\n' \
    'class _ElementFactory:' \
    '    @staticmethod' \
    '    def make(factory, name):' \
    '        return object()' \
    '' \
    '' \
    'class _Gst:' \
    '    ElementFactory = _ElementFactory' \
    '' \
    '    @staticmethod' \
    '    def init(argv):' \
    '        return None' \
    '' \
    '' \
    'Gst = _Gst()' \
    >> "$GST_STUB_ROOT/gtk-and-gst/gi/repository/__init__.py"

if PYTHONPATH="$GST_STUB_ROOT/gtk-only" \
    IPOD_TOOLS_DIR="$INSTALL_BLOCKER" \
    XDG_DATA_HOME="$TEST_ROOT/xdg-data-no-gst" \
    "$ROOT/install.sh" --no-system \
    > "$EVIDENCE_DIR/install-no-gstreamer.txt" 2>&1; then
    echo "installer unexpectedly continued past the blocked tools dir" >&2
    exit 1
fi
grep -Fq 'GUI: GTK4 bindings present' "$EVIDENCE_DIR/install-no-gstreamer.txt"
if grep -Fq 'Preview playback: GStreamer present' \
    "$EVIDENCE_DIR/install-no-gstreamer.txt"; then
    echo "installer claimed GStreamer on a machine without it" >&2
    exit 1
fi
# The whole working set, not just the typelib the probe imported: a player with
# no decoders reaches the user as silence, one track at a time.
for package in gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad; do
    grep -Fq "$package" "$EVIDENCE_DIR/install-no-gstreamer.txt" \
        || { echo "installer did not offer $package" >&2; exit 1; }
done
# GTK was satisfied here, so the offer came from the playback probe alone. A
# missing runtime is still reported rather than installed; that exception is
# about nodejs versions and does not extend to GStreamer.
if grep -Eq '^\s+python3-gi$' "$EVIDENCE_DIR/install-no-gstreamer.txt"; then
    echo "installer offered GUI packages it had just found present" >&2
    exit 1
fi

if PYTHONPATH="$GST_STUB_ROOT/gtk-and-gst" \
    IPOD_TOOLS_DIR="$INSTALL_BLOCKER" \
    XDG_DATA_HOME="$TEST_ROOT/xdg-data-with-gst" \
    "$ROOT/install.sh" --no-system \
    > "$EVIDENCE_DIR/install-with-gstreamer.txt" 2>&1; then
    echo "installer unexpectedly continued past the blocked tools dir" >&2
    exit 1
fi
grep -Fq 'Preview playback: GStreamer present' \
    "$EVIDENCE_DIR/install-with-gstreamer.txt"
if grep -Fq 'gstreamer1.0-' "$EVIDENCE_DIR/install-with-gstreamer.txt"; then
    echo "installer offered GStreamer packages it had just found present" >&2
    exit 1
fi

WEIRD_ROOT="$TEST_ROOT/checkout %f \"quoted\" \\slash \$cash \`tick\`"
WEIRD_DESKTOP_DATA="$TEST_ROOT/xdg-data-weird"
mkdir -p "$WEIRD_ROOT/desktop"
cp \
    "$ROOT/install.sh" \
    "$ROOT/lib.sh" \
    "$ROOT/ipod-gui.sh" \
    "$WEIRD_ROOT/"
cp "$ROOT/desktop/io.github.max_miller1204.IpodShuffle.svg" \
    "$WEIRD_ROOT/desktop/"
if XDG_DATA_HOME="$WEIRD_DESKTOP_DATA" IPOD_TOOLS_DIR="$INSTALL_BLOCKER" \
    "$WEIRD_ROOT/install.sh" --no-system \
    > "$EVIDENCE_DIR/install-desktop-entry-escaped.txt" 2>&1; then
    echo "installer unexpectedly continued past the blocked tools dir" >&2
    exit 1
fi
WEIRD_DESKTOP_FILE="$WEIRD_DESKTOP_DATA/applications/io.github.max_miller1204.IpodShuffle.desktop"
WEIRD_LAUNCH_MARKER="$TEST_ROOT/weird-desktop-launched"
printf '%s\n' \
    '#!/bin/sh' \
    "printf '%s\\n' launched > \"\$WEIRD_LAUNCH_MARKER\"" \
    > "$WEIRD_ROOT/ipod-gui.sh"
chmod +x "$WEIRD_ROOT/ipod-gui.sh"
WEIRD_ENV_PATH="$(command -v env)"
export WEIRD_LAUNCH_MARKER
/usr/bin/python3 - \
    "$WEIRD_DESKTOP_FILE" \
    "$WEIRD_ROOT/ipod-gui.sh" \
    "$WEIRD_ENV_PATH" \
    "$WEIRD_LAUNCH_MARKER" <<'PY'
import pathlib
import sys
import time

from gi.repository import Gio

entry = Gio.DesktopAppInfo.new_from_filename(sys.argv[1])
assert entry is not None
assert entry.get_executable() == sys.argv[3], entry.get_executable()
assert entry.get_string("TryExec") == sys.argv[2], entry.get_string("TryExec")
assert entry.launch([], None)
marker = pathlib.Path(sys.argv[4])
for _ in range(200):
    if marker.exists():
        break
    time.sleep(0.01)
assert marker.read_text(encoding="utf-8") == "launched\n"
PY

# js_runtime invokes these test doubles indirectly by candidate name.
# shellcheck disable=SC2317,SC2329
(
    source "$ROOT/lib.sh"
    deno() { printf 'deno 2.2.9\n'; }
    node() { printf 'v18.19.1\n'; }
    bun() { printf '1.3.15\n'; }
    if js_runtime; then
        echo "js_runtime accepted an unsupported runtime version" >&2
        exit 1
    fi

    node() { printf 'v22.0.0\n'; }
    test "$(js_runtime)" = node

    node() { printf 'v21.99.99\n'; }
    bun() { printf '1.2.11\n'; }
    test "$(js_runtime)" = bun

    bun() { printf '1.3.14\n'; }
    test "$(js_runtime)" = bun
    echo "versions validated"
) > "$EVIDENCE_DIR/js-runtime-versions.txt"
grep -Fxq "versions validated" "$EVIDENCE_DIR/js-runtime-versions.txt"

# A PATH-managed Python can have GTK too, but install.sh validates the distro
# interpreter and builds the project environment from it. Both fallbacks must
# preserve that contract instead of letting PATH choose a different runtime.
PATH_PYTHON="$TEST_ROOT/path-python"
mkdir -p "$PATH_PYTHON"
printf '#!/bin/sh\ncat >/dev/null\nexit 0\n' > "$PATH_PYTHON/python3"
chmod +x "$PATH_PYTHON/python3"
IPOD_VENV_PYTHON="$TEST_ROOT/missing-python" \
    PATH="$PATH_PYTHON:$BASE_PATH" \
    bash -c '
        source "$1/lib.sh"
        test "$(find_gui_python)" = /usr/bin/python3
        test "$(db_python)" = /usr/bin/python3
    ' _ "$ROOT"
echo "PASS: GUI and database fallbacks preferred the validated distro Python"

# gst_available probes through whichever interpreter find_gui_python names, so
# stand-in interpreters decide the answer here. GStreamer is optional and is
# not installed in CI, and a check that only ever saw one answer would pass
# just as happily if the function had stopped probing anything at all.
printf '#!/bin/sh\ncat >/dev/null\nexit 1\n' > "$TEST_ROOT/python-no-gst"
printf '#!/bin/sh\ncat >/dev/null\nexit 0\n' > "$TEST_ROOT/python-with-gst"
chmod +x "$TEST_ROOT/python-no-gst" "$TEST_ROOT/python-with-gst"

# gst_available invokes this test double indirectly.
# shellcheck disable=SC2317,SC2329
(
    source "$ROOT/lib.sh"

    # No interpreter can drive GTK at all, so there is no window to play in.
    find_gui_python() { return 1; }
    if gst_available; then
        echo "gst_available passed with no GTK interpreter" >&2
        exit 1
    fi

    # The GUI's own interpreter, without the GStreamer bindings or plugins.
    find_gui_python() { printf '%s' "$TEST_ROOT/python-no-gst"; }
    if gst_available; then
        echo "gst_available passed without GStreamer" >&2
        exit 1
    fi

    find_gui_python() { printf '%s' "$TEST_ROOT/python-with-gst"; }
    gst_available
    echo "gstreamer probe validated"
) > "$EVIDENCE_DIR/gst-available.txt"
grep -Fxq "gstreamer probe validated" "$EVIDENCE_DIR/gst-available.txt"

# The probe itself has to be a program, not just a heredoc that looks like one.
# Nothing else can catch a syntax error in it: shellcheck sees an opaque string
# and the function is expected to fail on most machines, so a broken probe
# would withhold playback everywhere while looking exactly like an uninstalled
# GStreamer.
/usr/bin/python3 - "$ROOT/lib.sh" <<'PROBE_CHECK'
import ast
import re
import sys
from pathlib import Path

body = Path(sys.argv[1]).read_text(encoding="utf-8")
probe = re.search(r"<<'GST_PROBE'[^\n]*\n(.*?)\nGST_PROBE\n", body, re.DOTALL)
assert probe, "gst_available no longer embeds a probe"
ast.parse(probe.group(1))
print("probe parses")
PROBE_CHECK

/usr/bin/python3 - "$DB_RECORD" "$IPOD" <<'PY'
import json
import sys
from pathlib import Path

ipod = sys.argv[2]

# Only this iPod's invocations. The builder records every run in the suite,
# including those against throwaway devices other checks create, so indexing
# the raw list positionally made unrelated additions elsewhere fail here.
# The length assertion keeps that from loosening into "some subset matched".
records = [
    entry
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    if (entry := json.loads(line))[-1] == ipod
]
assert len(records) == 7, records
assert records[0] == [
    "--auto-dir-playlists", "-1", "--playlist-voiceover", ipod
]
assert records[1] == records[0]
assert records[2] == [
    "--auto-id3-playlists", "{artist}", "--track-voiceover",
    "--playlist-voiceover", ipod
]
assert records[3] == [
    "--auto-id3-playlists", "{genre}", "--track-voiceover",
    "--playlist-voiceover", ipod
]
assert records[4] == [ipod]
assert records[5] == ["--auto-id3-playlists", "{artist}", ipod]
assert records[6] == [ipod]
print(json.dumps(records, indent=2))
PY

# Persisting the options must fail loudly rather than reporting success with
# the file missing, since a later bare rebuild would then silently discard the
# playlists this file exists to preserve. Root ignores the permission bits, so
# the check only means something as an unprivileged user.
if [[ "$(id -u)" -ne 0 ]]; then
    chmod a-w "$IPOD/iPod_Control"
    write_failed=0
    "$ROOT/ipod-sync.sh" \
        --ipod "$IPOD" \
        --id3-playlists \
        --playlist-voiceover \
        --rebuild-only > "$EVIDENCE_DIR/unwritable-options.txt" 2>&1 \
        || write_failed=1
    chmod u+w "$IPOD/iPod_Control"

    if (( ! write_failed )); then
        echo "sync reported success while unable to persist its options" >&2
        exit 1
    fi
    grep -Fq 'options file' "$EVIDENCE_DIR/unwritable-options.txt"
else
    printf 'skipped: running as root, permission bits are not enforced\n' \
        > "$EVIDENCE_DIR/unwritable-options.txt"
fi

# Removing single tracks works on its own library rather than on whatever the
# sections above happened to leave behind, since the wipe emptied the device.
REMOVE_SOURCE="$TEST_ROOT/Mixtape"
mkdir -p "$REMOVE_SOURCE/Side A" "$REMOVE_SOURCE/Side B"
printf 'keep me\n'   > "$REMOVE_SOURCE/Side A/01 - Keep.mp3"
printf 'delete me\n' > "$REMOVE_SOURCE/Side A/02 - Delete.mp3"
printf 'whole side\n' > "$REMOVE_SOURCE/Side B/01 - Gone.mp3"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    --dir-playlists=1 \
    --playlist-voiceover \
    "$REMOVE_SOURCE" > "$EVIDENCE_DIR/remove-setup.txt" 2>&1

"$ROOT/ipod-remove.sh" --ipod "$IPOD" --list > "$EVIDENCE_DIR/remove-list.txt"
grep -Fxq 'Mixtape/Side A/02 - Delete.mp3' "$EVIDENCE_DIR/remove-list.txt"

# A missing builder has to be discovered before deletion. Otherwise the track
# disappears while the old database continues offering it to the player.
missing_builder_failed=0
IPOD_DB_TOOL="$TEST_ROOT/missing-db-builder.py" \
    run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    'Mixtape/Side A/02 - Delete.mp3' \
    > "$EVIDENCE_DIR/remove-missing-builder.txt" 2>&1 \
    || missing_builder_failed=1
test "$missing_builder_failed" -eq 1
test -s "$IPOD/iPod_Control/Music/Mixtape/Side A/02 - Delete.mp3"
grep -Fq 'Database tool missing' "$EVIDENCE_DIR/remove-missing-builder.txt"

# An options path that exists but cannot be read is not the same as having no
# saved options. Both mutating commands must stop before changing the library,
# or the next database would silently lose every playlist.
OPTIONS_FILE="$IPOD/iPod_Control/.sync-options"
SAVED_OPTIONS="$TEST_ROOT/saved-sync-options"
mv -- "$OPTIONS_FILE" "$SAVED_OPTIONS"
mkdir "$OPTIONS_FILE"

unreadable_remove_failed=0
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    'Mixtape/Side A/02 - Delete.mp3' \
    > "$EVIDENCE_DIR/remove-unreadable-options.txt" 2>&1 \
    || unreadable_remove_failed=1
test "$unreadable_remove_failed" -eq 1
test -s "$IPOD/iPod_Control/Music/Mixtape/Side A/02 - Delete.mp3"

READ_FAILURE_SOURCE="$TEST_ROOT/Read Failure"
mkdir "$READ_FAILURE_SOURCE"
printf 'do not copy\n' > "$READ_FAILURE_SOURCE/Unread.mp3"
unreadable_sync_failed=0
"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    "$READ_FAILURE_SOURCE/Unread.mp3" \
    > "$EVIDENCE_DIR/sync-unreadable-options.txt" 2>&1 \
    || unreadable_sync_failed=1
test "$unreadable_sync_failed" -eq 1
test ! -e "$IPOD/iPod_Control/Music/Read Failure/Unread.mp3"

rmdir -- "$OPTIONS_FILE"
mv -- "$SAVED_OPTIONS" "$OPTIONS_FILE"
grep -Fq 'Could not read saved playlist and voiceover options' \
    "$EVIDENCE_DIR/remove-unreadable-options.txt"
grep -Fq 'Could not read saved playlist and voiceover options' \
    "$EVIDENCE_DIR/sync-unreadable-options.txt"

# A pipe is non-interactive too: input that happens to contain "n" is not an
# authorization token and leaves the device untouched.
declined=0
printf 'n\n' | "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    'Mixtape/Side A/02 - Delete.mp3' > "$EVIDENCE_DIR/remove-declined.txt" 2>&1 \
    || declined=1
test "$declined" -eq 1
test -s "$IPOD/iPod_Control/Music/Mixtape/Side A/02 - Delete.mp3"
grep -Fq 'Destructive action refused: confirmation is not authorization' \
    "$EVIDENCE_DIR/remove-declined.txt"

run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    'Mixtape/Side A/02 - Delete.mp3' > "$EVIDENCE_DIR/remove-track.txt" 2>&1

test ! -e "$IPOD/iPod_Control/Music/Mixtape/Side A/02 - Delete.mp3"
test -s "$IPOD/iPod_Control/Music/Mixtape/Side A/01 - Keep.mp3"

# The rebuild a removal triggers has to reuse the options the sync saved, or
# deleting one track would silently take every playlist with it.
grep -Fq 'Reusing saved options: --auto-dir-playlists 1 --playlist-voiceover' \
    "$EVIDENCE_DIR/remove-track.txt"
diff -u <(printf '%s\n' \
    --auto-dir-playlists \
    1 \
    --playlist-voiceover) \
    "$IPOD/iPod_Control/.sync-options"

# A folder argument takes the folder with it. --dir-playlists builds one
# playlist per folder, so an empty one left behind is a playlist that plays
# nothing on a device with no screen to show that it is empty.
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    'Mixtape/Side B' > "$EVIDENCE_DIR/remove-folder.txt" 2>&1
test ! -e "$IPOD/iPod_Control/Music/Mixtape/Side B"
test -d "$IPOD/iPod_Control/Music/Mixtape/Side A"

# Same reason for the folder a last remaining track leaves behind, all the way
# up to the music root.
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    'Mixtape/Side A/01 - Keep.mp3' > "$EVIDENCE_DIR/remove-last.txt" 2>&1
test ! -e "$IPOD/iPod_Control/Music/Mixtape"
test -d "$IPOD/iPod_Control/Music"

# A track path is joined to the music folder, so it must not be able to leave
# it. The names come from the GUI's track list and from a shell prompt where a
# stray ../ is one keystroke away, and rm -rf is on the other side.
OUTSIDE="$TEST_ROOT/outside.mp3"
printf 'not on the ipod\n' > "$OUTSIDE"
: > "$EVIDENCE_DIR/remove-refusals.txt"
for refusal in '../../../outside.mp3' "$OUTSIDE" '.' 'No Such/Track.mp3'; do
    if "$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes "$refusal" \
        >> "$EVIDENCE_DIR/remove-refusals.txt" 2>&1; then
        echo "ipod-remove.sh accepted a path it should have refused: $refusal" >&2
        exit 1
    fi
done
test -s "$OUTSIDE"
grep -Fq 'Use ./ipod-wipe.sh' "$EVIDENCE_DIR/remove-refusals.txt"

# A single file lands in a folder named after the one it came from, so adding
# one track out of an album puts it where syncing the whole album would, and
# --dir-playlists still has a folder to group it by. The GUI's YouTube flow
# depends on this to copy exactly the tracks a download produced.
"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    "$SOURCE/Disc 1/01 - Highway.mp3" \
    "$SOURCE/Disc 1/cover.flac" > "$EVIDENCE_DIR/sync-single-file.txt" 2>&1

test -s "$IPOD/iPod_Control/Music/Disc 1/01 - Highway.mp3"
test ! -e "$IPOD/iPod_Control/Music/Disc 1/cover.flac"
grep -Fq 'Skipped 1 unsupported file(s)' "$EVIDENCE_DIR/sync-single-file.txt"

# A leading dash in a track name is a filename, not a flag. Both scripts take
# paths built from tags and YouTube titles, where "-1" is a plausible song.
DASH_SOURCE="$TEST_ROOT/Dashes"
mkdir -p "$DASH_SOURCE"
printf 'dash\n' > "$DASH_SOURCE/-1 Countdown.mp3"

"$ROOT/ipod-sync.sh" \
    --ipod "$IPOD" \
    -- "$DASH_SOURCE/-1 Countdown.mp3" > "$EVIDENCE_DIR/dash-name.txt" 2>&1
test -s "$IPOD/iPod_Control/Music/Dashes/-1 Countdown.mp3"

run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    -- 'Dashes/-1 Countdown.mp3' >> "$EVIDENCE_DIR/dash-name.txt" 2>&1
test ! -e "$IPOD/iPod_Control/Music/Dashes"

# A library assembled out of symlinks. This used to sync as an empty folder:
# find without -L matches a link as neither -type f nor a folder to descend,
# so every track went silently uncopied. On its own device, because a source
# holding a filesystem loop is not something to leave in the shared one.
LINK_IPOD="$TEST_ROOT/symlink-target"
LINK_SOURCE="$TEST_ROOT/Linked Library"
LINK_ARCHIVE="$TEST_ROOT/Archive"
mkdir -p \
    "$LINK_IPOD/iPod_Control/iTunes" \
    "$LINK_IPOD/iPod_Control/Music" \
    "$LINK_IPOD/iPod_Control/Speakable" \
    "$LINK_SOURCE/Disc 1" \
    "$LINK_ARCHIVE/Live"
printf 'real\n'   > "$LINK_SOURCE/Disc 1/01 - Real.mp3"
printf 'away\n'   > "$LINK_ARCHIVE/02 - Archived.mp3"
printf 'encore\n' > "$LINK_ARCHIVE/Live/03 - Encore.mp3"
# A link to a track outside the folder being synced, which is the whole point
# of a linked layout: the music lives elsewhere and the library points at it.
ln -s "$LINK_ARCHIVE/02 - Archived.mp3" "$LINK_SOURCE/Disc 1/02 - Linked.mp3"
# A linked folder, descended, including the subfolder below it.
ln -s "$LINK_ARCHIVE" "$LINK_SOURCE/Linked Album"
# A link to a track that is not there, named and counted rather than silently
# dropped, and a broken link to something the firmware could not play anyway,
# which stays quiet because it was never going to be copied.
ln -s "$TEST_ROOT/nowhere/gone.mp3" "$LINK_SOURCE/Disc 1/04 - Gone.mp3"
ln -s "$TEST_ROOT/nowhere/cover.jpg" "$LINK_SOURCE/Disc 1/cover.jpg"
# A folder that links back to its own parent. find walks it once and refuses
# to go round again; without that this sync would never finish.
ln -s "$LINK_SOURCE" "$LINK_SOURCE/Disc 1/loop"

"$ROOT/ipod-sync.sh" \
    --ipod "$LINK_IPOD" \
    "$LINK_SOURCE" > "$EVIDENCE_DIR/sync-symlinks.txt" 2>&1

LINK_MUSIC="$LINK_IPOD/iPod_Control/Music/Linked Library"
test -s "$LINK_MUSIC/Disc 1/01 - Real.mp3"
test -s "$LINK_MUSIC/Disc 1/02 - Linked.mp3"
test -s "$LINK_MUSIC/Linked Album/02 - Archived.mp3"
test -s "$LINK_MUSIC/Linked Album/Live/03 - Encore.mp3"
# Copied, never linked: the iPod is FAT and the source may be unplugged next.
test ! -L "$LINK_MUSIC/Disc 1/02 - Linked.mp3"
diff -u "$LINK_ARCHIVE/02 - Archived.mp3" "$LINK_MUSIC/Disc 1/02 - Linked.mp3"
# The layout mirrors where the links sit, not where they point, so following
# one out of the source folder cannot write outside the music directory.
test ! -e "$LINK_MUSIC/Disc 1/04 - Gone.mp3"
test ! -e "$LINK_MUSIC/Disc 1/cover.jpg"
test "$(find "$LINK_IPOD/iPod_Control/Music" -type f | wc -l)" = 4
grep -Fq 'Broken symlink, skipped: Disc 1/04 - Gone.mp3' \
    "$EVIDENCE_DIR/sync-symlinks.txt"
grep -Fq 'Skipped 1 symlink(s) pointing at a file that is not there.' \
    "$EVIDENCE_DIR/sync-symlinks.txt"
# A dangling link to something unplayable stays quiet: it was never going to
# be copied, so naming it would be noise about a file nobody asked for.
if grep -Fq 'cover.jpg' "$EVIDENCE_DIR/sync-symlinks.txt"; then
    echo "ipod-sync.sh reported a broken link it was never going to copy" >&2
    exit 1
fi
grep -Fq "Part of 'Linked Library' could not be searched:" \
    "$EVIDENCE_DIR/sync-symlinks.txt"
grep -Fq 'File system loop detected' "$EVIDENCE_DIR/sync-symlinks.txt"
grep -Fq 'Copied 4 file(s)' "$EVIDENCE_DIR/sync-symlinks.txt"
# The GUI counts what the script copies, so a linked library has to give the
# same four tracks on both sides. Counting a track the script then skips is
# what left a finished sync reading short of the end.
/usr/bin/python3 "$ROOT/tests/gui-scan-paths.py" "$LINK_SOURCE" \
    > "$EVIDENCE_DIR/gui-symlink-scan.json"
diff -u <(printf '%s\n' \
    '[' \
    '  "Disc 1/01 - Real.mp3",' \
    '  "Disc 1/02 - Linked.mp3",' \
    '  "Linked Album/02 - Archived.mp3",' \
    '  "Linked Album/Live/03 - Encore.mp3"' \
    ']') \
    "$EVIDENCE_DIR/gui-symlink-scan.json"

# Playlist files are the fourth kind of source argument, and everything below
# happens on a dedicated device so the invocation record of the shared one
# stays exactly as the assertions above expect it.
PLAYLIST_IPOD="$TEST_ROOT/playlist-target"
PLAYLIST_LIB="$TEST_ROOT/Library"
mkdir -p \
    "$PLAYLIST_IPOD/iPod_Control/iTunes" \
    "$PLAYLIST_IPOD/iPod_Control/Music" \
    "$PLAYLIST_IPOD/iPod_Control/Speakable" \
    "$PLAYLIST_LIB/Beach Boys" \
    "$PLAYLIST_LIB/Neil Young"
printf 'surf\n'     > "$PLAYLIST_LIB/Beach Boys/Surfin.mp3"
printf 'harvest\n'  > "$PLAYLIST_LIB/Neil Young/Harvest Moon.mp3"
printf 'gold\n'     > "$PLAYLIST_LIB/Neil Young/Heart of Gold.mp3"
printf 'lossless\n' > "$PLAYLIST_LIB/Neil Young/On the Beach.flac"

# Everything a playlist exported by another program may contain: CRLF line
# endings, comments, blank lines, entries relative to the playlist file,
# absolute paths, percent-encoded file:// URIs, a track that is not there, a
# format the firmware cannot play, and a stream URL.
{
    printf '#EXTM3U\r\n'
    printf '#EXTINF:180,Beach Boys - Surfin\r\n'
    printf 'Beach Boys/Surfin.mp3\r\n'
    printf '\r\n'
    printf '%s\r\n' "$PLAYLIST_LIB/Neil Young/Harvest Moon.mp3"
    printf 'file://%s/Neil%%20Young/Heart%%20of%%20Gold.mp3\r\n' "$PLAYLIST_LIB"
    printf 'Neil Young/Not Here.mp3\r\n'
    printf 'Neil Young/On the Beach.flac\r\n'
    printf 'https://example.invalid/radio\r\n'
} > "$PLAYLIST_LIB/Summer Mix.m3u"

"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Summer Mix.m3u" > "$EVIDENCE_DIR/playlist-m3u-sync.txt" 2>&1

# The rewritten list lives at the volume root with entries relative to it, in
# playlist order, with everything unplayable dropped.
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Beach Boys/Surfin.mp3' \
    'iPod_Control/Music/Neil Young/Harvest Moon.mp3' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3') \
    "$PLAYLIST_IPOD/Summer Mix.m3u"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young/Harvest Moon.mp3"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young/Heart of Gold.mp3"
test ! -e "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young/On the Beach.flac"
grep -Fq 'playlist entry is a stream, skipped' "$EVIDENCE_DIR/playlist-m3u-sync.txt"
grep -Fq 'not found on this computer' "$EVIDENCE_DIR/playlist-m3u-sync.txt"
grep -Fq 'cannot play' "$EVIDENCE_DIR/playlist-m3u-sync.txt"
# No voiceover flag anywhere yet, so the screenless-device warning applies.
grep -Fq 'Playlists without --playlist-voiceover will be unnamed on the device.' \
    "$EVIDENCE_DIR/playlist-m3u-sync.txt"
test ! -e "$PLAYLIST_IPOD/iPod_Control/.sync-options"

# A pls playlist is ordered by its numbered entries, not by line order, and
# reaches the device converted to m3u.
{
    printf '[playlist]\n'
    printf 'File2=Beach Boys/Surfin.mp3\n'
    printf 'Title2=Surfin\n'
    printf 'File1=Neil Young/Heart of Gold.mp3\n'
    printf 'NumberOfEntries=2\n'
} > "$PLAYLIST_LIB/Party.pls"
printf '%s\n' 'File1=iPod_Control/Music/Stale.mp3' > "$PLAYLIST_IPOD/Party.pls"

"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --playlist-voiceover \
    "$PLAYLIST_LIB/Party.pls" > "$EVIDENCE_DIR/playlist-pls-sync.txt" 2>&1

diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3' \
    'iPod_Control/Music/Beach Boys/Surfin.mp3') \
    "$PLAYLIST_IPOD/Party.m3u"
test ! -e "$PLAYLIST_IPOD/Party.pls"

printf '%s\n' 'File1=iPod_Control/Music/Stale.mp3' > "$PLAYLIST_IPOD/Retired.pls"
: > "$PLAYLIST_LIB/Retired.pls"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Retired.pls" > "$EVIDENCE_DIR/playlist-empty-pls-sync.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Retired.m3u"
test ! -e "$PLAYLIST_IPOD/Retired.pls"
grep -Fq "Playlist 'Retired' references no playable local files; removed from the device." \
    "$EVIDENCE_DIR/playlist-empty-pls-sync.txt"

# The warning follows the effective options, not this command line alone: the
# voiceover choice just saved covers a playlist synced without any flags.
printf '%s\n' "$PLAYLIST_LIB/Beach Boys/Surfin.mp3" > "$TEST_ROOT/Best: Hits.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$TEST_ROOT/Best: Hits.m3u" > "$EVIDENCE_DIR/playlist-saved-voiceover.txt" 2>&1
if grep -Fq 'will be unnamed on the device' \
    "$EVIDENCE_DIR/playlist-saved-voiceover.txt"; then
    echo "sync warned about missing voiceover the saved options provide" >&2
    exit 1
fi

# The filename is the spoken playlist name, and FAT rejects a colon, so the
# device copy is renamed as little as possible and the change is announced.
test -f "$PLAYLIST_IPOD/Best_ Hits.m3u"
grep -Fq 'characters FAT rejects' "$EVIDENCE_DIR/playlist-saved-voiceover.txt"

# The property everything above exists for: the real upstream builder must
# discover the rewritten lists and resolve every entry to a track it knows.
# The fake builder cannot vouch for that. CI clones the builder and passes it
# in; locally the copy install.sh keeps is used when present.
REAL_DB_TOOL="${IPOD_REAL_DB_TOOL:-$HOME/ipod-tools/IPod-Shuffle-4g/ipod-shuffle-4g.py}"
if [[ -f "$REAL_DB_TOOL" ]]; then
    /usr/bin/python3 "$REAL_DB_TOOL" --verbose --playlist-voiceover \
        "$PLAYLIST_IPOD" > "$EVIDENCE_DIR/real-builder.txt" 2>&1
    grep -Fq 'Adding playlist' "$EVIDENCE_DIR/real-builder.txt"
    if grep -Fq 'Could not find track' "$EVIDENCE_DIR/real-builder.txt"; then
        echo "the real builder could not resolve a rewritten playlist entry" >&2
        exit 1
    fi
    # One master playlist plus the three synced above, over three tracks,
    # read back from the bdhs header the firmware reads.
    /usr/bin/python3 - "$PLAYLIST_IPOD/iPod_Control/iTunes/iTunesSD" <<'PY'
import struct
import sys

data = open(sys.argv[1], "rb").read()
assert data[0:4] == b"bdhs", data[0:4]
tracks = struct.unpack_from("<I", data, 12)[0]
playlists = struct.unpack_from("<I", data, 16)[0]
assert tracks == 3, tracks
assert playlists == 4, playlists
PY
    # The spoken names that same run wrote, read back by the window. On a
    # device with no screen they are the whole of a playlist's identity, and
    # nothing but this builder decides what they are called - so whether the
    # window can find them is a question only the real one can answer.
    if command -v pico2wave > /dev/null \
        || command -v espeak > /dev/null \
        || command -v say > /dev/null; then
        /usr/bin/python3 "$ROOT/tests/gui-spoken-names.py" "$PLAYLIST_IPOD" \
            > "$EVIDENCE_DIR/gui-spoken-names.json"
    else
        printf 'skipped: no speech engine installed\n' \
            > "$EVIDENCE_DIR/gui-spoken-names.json"
        echo "NOTICE: spoken-name check skipped; install pico2wave or espeak" >&2
    fi
else
    printf 'skipped: real database builder not installed at %s\n' "$REAL_DB_TOOL" \
        > "$EVIDENCE_DIR/real-builder.txt"
    echo "NOTICE: real-builder playlist check skipped; run ./install.sh to enable" >&2
fi

# ------------------------------------------- the machine-readable device report
#
# Everything above this line is read by a person. --json is the other half, and
# the risk it carries is that ipod-report.py repeats what ipod_gui/device.py
# does rather than importing it - a decision forced by the scripts having to
# run where the GTK bindings are not installed, and one that lets the two
# drift silently. So the report the script printed is compared against the
# window's own reading of the same device, field by field.
#
# Run here because this device is the fullest one the suite builds: three
# tracks, three playlists, and, where the real builder and a speech engine are
# both present, the recordings that decide which of them can be announced.
"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --list \
    --json > "$EVIDENCE_DIR/device-report.json"
/usr/bin/python3 "$ROOT/tests/device-report.py" \
    "$PLAYLIST_IPOD" \
    "$EVIDENCE_DIR/device-report.json" \
    > "$EVIDENCE_DIR/device-report-agrees.json"

# The same script's two ways of saying what is on the device have to say the
# same thing, or the flag would be reporting a second opinion rather than the
# same answer in another shape.
"$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" --list \
    > "$EVIDENCE_DIR/device-report-plain.txt"
diff -u \
    <(/usr/bin/python3 -c '
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for track in report["tracks"]:
    print(track)
' "$EVIDENCE_DIR/device-report.json") \
    "$EVIDENCE_DIR/device-report-plain.txt"

# A report is worth nothing if half of one can reach the caller looking whole.
# An unreadable saved-options file is the case the scripts already refuse to
# guess about, because reporting "no saved options" for it would say the next
# rebuild is safe when it would drop every playlist.
mv -- "$PLAYLIST_IPOD/iPod_Control/.sync-options" "$TEST_ROOT/report-options"
mkdir "$PLAYLIST_IPOD/iPod_Control/.sync-options"
report_options_status=0
"$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" --list --json \
    > "$EVIDENCE_DIR/device-report-unreadable-options.json" \
    2> "$EVIDENCE_DIR/device-report-unreadable-options.txt" \
    || report_options_status=$?
test "$report_options_status" -eq 1
test ! -s "$EVIDENCE_DIR/device-report-unreadable-options.json"
rmdir -- "$PLAYLIST_IPOD/iPod_Control/.sync-options"
mv -- "$TEST_ROOT/report-options" "$PLAYLIST_IPOD/iPod_Control/.sync-options"

# An album the walk cannot enter is the same rule and the harder case, because
# nothing fails: Path.rglob yields nothing for a folder it cannot read, so the
# report would have counted the tracks it could see and called that the device.
# A caller told a full iPod holds nothing is one about to sync a whole library
# onto it. Skipped as root, who can read it anyway.
if [[ "$(id -u)" != 0 ]]; then
    chmod 000 "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young"
    report_walk_status=0
    "$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" --list --json \
        > "$EVIDENCE_DIR/device-report-unreadable-album.json" \
        2> "$EVIDENCE_DIR/device-report-unreadable-album.txt" \
        || report_walk_status=$?
    chmod 755 "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young"
    test "$report_walk_status" -eq 1
    test ! -s "$EVIDENCE_DIR/device-report-unreadable-album.json"
else
    printf 'skipped: running as root, which can read the folder\n' \
        > "$EVIDENCE_DIR/device-report-unreadable-album.txt"
    echo "NOTICE: unreadable-album report check skipped; run as a normal user" >&2
fi

control_playlist="$PLAYLIST_LIB/Control"$'\t'"Name"$'\177'".m3u"
printf '%s\n' "$PLAYLIST_LIB/Neil Young/Heart of Gold.mp3" > "$control_playlist"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$control_playlist" > "$EVIDENCE_DIR/playlist-control-name.txt" 2>&1
test -f "$PLAYLIST_IPOD/Control_Name_.m3u"
grep -Fq 'characters FAT rejects' "$EVIDENCE_DIR/playlist-control-name.txt"

printf '%s\n' "$PLAYLIST_LIB/Neil Young/Heart of Gold.mp3" \
    > "$PLAYLIST_LIB/mix..m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/mix..m3u" > "$EVIDENCE_DIR/playlist-trailing-dot.txt" 2>&1
test -f "$PLAYLIST_IPOD/mix_.m3u"
test ! -e "$PLAYLIST_IPOD/mix.m3u"
grep -Fq "it will be called 'mix_'" "$EVIDENCE_DIR/playlist-trailing-dot.txt"

mkdir -p "$PLAYLIST_LIB/First Collision" "$PLAYLIST_LIB/Second Collision"
printf 'first playlist track\n' > "$PLAYLIST_LIB/First Collision/First.mp3"
printf 'second playlist track\n' > "$PLAYLIST_LIB/Second Collision/Second.mp3"
printf '%s\n' "$PLAYLIST_LIB/First Collision/First.mp3" \
    > "$PLAYLIST_LIB/Rock:2026.m3u"
printf '%s\n' "$PLAYLIST_LIB/Second Collision/Second.mp3" \
    > "$PLAYLIST_LIB/Rock?2026.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Rock:2026.m3u" \
    "$PLAYLIST_LIB/Rock?2026.m3u" \
    > "$EVIDENCE_DIR/playlist-name-collision.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/First Collision/First.mp3') \
    "$PLAYLIST_IPOD/Rock_2026.m3u"
test -f "$PLAYLIST_IPOD/iPod_Control/Music/First Collision/First.mp3"
test ! -e "$PLAYLIST_IPOD/iPod_Control/Music/Second Collision/Second.mp3"
grep -Fq "$PLAYLIST_LIB/Rock:2026.m3u" \
    "$EVIDENCE_DIR/playlist-name-collision.txt"
grep -Fq "$PLAYLIST_LIB/Rock?2026.m3u" \
    "$EVIDENCE_DIR/playlist-name-collision.txt"
grep -Fq "both become 'Rock_2026.m3u'" \
    "$EVIDENCE_DIR/playlist-name-collision.txt"

mkdir -p "$PLAYLIST_LIB/Case First" "$PLAYLIST_LIB/Case Second"
printf 'case first track\n' > "$PLAYLIST_LIB/Case First/First.mp3"
printf 'case second track\n' > "$PLAYLIST_LIB/Case Second/Second.mp3"
printf '%s\n' "$PLAYLIST_LIB/Case First/First.mp3" \
    > "$PLAYLIST_LIB/CaseMix.m3u"
printf '%s\n' "$PLAYLIST_LIB/Case Second/Second.mp3" \
    > "$PLAYLIST_LIB/casemix.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/CaseMix.m3u" \
    "$PLAYLIST_LIB/casemix.m3u" \
    > "$EVIDENCE_DIR/playlist-case-collision.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Case First/First.mp3') \
    "$PLAYLIST_IPOD/CaseMix.m3u"
test ! -e "$PLAYLIST_IPOD/casemix.m3u"
test ! -e "$PLAYLIST_IPOD/iPod_Control/Music/Case Second/Second.mp3"
grep -Fq "$PLAYLIST_LIB/CaseMix.m3u" \
    "$EVIDENCE_DIR/playlist-case-collision.txt"
grep -Fq "$PLAYLIST_LIB/casemix.m3u" \
    "$EVIDENCE_DIR/playlist-case-collision.txt"
grep -Fq "both become 'CaseMix.m3u'" \
    "$EVIDENCE_DIR/playlist-case-collision.txt"

mkdir -p "$PLAYLIST_LIB/Unicode First" "$PLAYLIST_LIB/Unicode Second"
printf 'unicode first track\n' > "$PLAYLIST_LIB/Unicode First/First.mp3"
printf 'unicode second track\n' > "$PLAYLIST_LIB/Unicode Second/Second.mp3"
printf '%s\n' "$PLAYLIST_LIB/Unicode First/First.mp3" \
    > "$PLAYLIST_LIB/ΟΣ.m3u"
printf '%s\n' "$PLAYLIST_LIB/Unicode Second/Second.mp3" \
    > "$PLAYLIST_LIB/οσ.m3u"
LC_ALL=C "$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/ΟΣ.m3u" \
    "$PLAYLIST_LIB/οσ.m3u" \
    > "$EVIDENCE_DIR/playlist-locale-case-collision.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Unicode First/First.mp3') \
    "$PLAYLIST_IPOD/ΟΣ.m3u"
test ! -e "$PLAYLIST_IPOD/οσ.m3u"
test ! -e "$PLAYLIST_IPOD/iPod_Control/Music/Unicode Second/Second.mp3"
grep -Fq "both become 'ΟΣ.m3u'" \
    "$EVIDENCE_DIR/playlist-locale-case-collision.txt"

mkdir -p "$PLAYLIST_LIB/Sharp S" "$PLAYLIST_LIB/Plain SS"
printf 'sharp s track\n' > "$PLAYLIST_LIB/Sharp S/Sharp.mp3"
printf 'plain ss track\n' > "$PLAYLIST_LIB/Plain SS/Plain.mp3"
printf '%s\n' "$PLAYLIST_LIB/Sharp S/Sharp.mp3" \
    > "$PLAYLIST_LIB/Straße.m3u"
printf '%s\n' "$PLAYLIST_LIB/Plain SS/Plain.mp3" \
    > "$PLAYLIST_LIB/Strasse.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Straße.m3u" \
    "$PLAYLIST_LIB/Strasse.m3u" \
    > "$EVIDENCE_DIR/playlist-simple-case-folding.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Sharp S/Sharp.mp3') \
    "$PLAYLIST_IPOD/Straße.m3u"
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Plain SS/Plain.mp3') \
    "$PLAYLIST_IPOD/Strasse.m3u"
test -f "$PLAYLIST_IPOD/iPod_Control/Music/Sharp S/Sharp.mp3"
test -f "$PLAYLIST_IPOD/iPod_Control/Music/Plain SS/Plain.mp3"

mkdir -p \
    "$PLAYLIST_LIB/Artist A/Greatest Hits" \
    "$PLAYLIST_LIB/Artist:B/Greatest Hits"
printf 'artist a\n' > "$PLAYLIST_LIB/Artist A/Greatest Hits/01.mp3"
printf 'artist b\n' > "$PLAYLIST_LIB/Artist:B/Greatest Hits/01.mp3"
printf '%s\n' \
    "$PLAYLIST_LIB/Artist A/Greatest Hits/01.mp3" \
    "$PLAYLIST_LIB/Artist:B/Greatest Hits/01.mp3" \
    > "$PLAYLIST_LIB/Collisions.m3u"
read -r collision_checksum collision_size _ \
    < <(cksum -- "$PLAYLIST_LIB/Artist:B/Greatest Hits/01.mp3")
collision_folder="Collision-$collision_checksum-$collision_size"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Collisions.m3u" > "$EVIDENCE_DIR/playlist-collisions.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Greatest Hits/01.mp3' \
    "iPod_Control/Music/$collision_folder/01.mp3") \
    "$PLAYLIST_IPOD/Collisions.m3u"
cmp -s \
    "$PLAYLIST_LIB/Artist A/Greatest Hits/01.mp3" \
    "$PLAYLIST_IPOD/iPod_Control/Music/Greatest Hits/01.mp3"
cmp -s \
    "$PLAYLIST_LIB/Artist:B/Greatest Hits/01.mp3" \
    "$PLAYLIST_IPOD/iPod_Control/Music/$collision_folder/01.mp3"
[[ "$collision_folder" =~ ^Collision-[0-9]+-[0-9]+$ ]]
(( ${#collision_folder} <= 40 ))
grep -Fq 'Destination already holds a different track' \
    "$EVIDENCE_DIR/playlist-collisions.txt"

printf '%s\n' "$PLAYLIST_LIB/Beach Boys/Surfin.mp3" \
    > "$PLAYLIST_LIB/Changing.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Changing.m3u" > "$EVIDENCE_DIR/playlist-changing-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/Changing.m3u"
printf '%s\n' "$PLAYLIST_LIB/Beach Boys/No Longer Here.mp3" \
    > "$PLAYLIST_LIB/Changing.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Changing.m3u" > "$EVIDENCE_DIR/playlist-changing-empty.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Changing.m3u"
grep -Fq "Playlist 'Changing' references no playable local files; removed from the device." \
    "$EVIDENCE_DIR/playlist-changing-empty.txt"

mkdir -p "$PLAYLIST_LIB/Parser"
printf 'parser track\n' > "$PLAYLIST_LIB/Parser/New.mp3"
printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3' \
    > "$PLAYLIST_IPOD/Parser.m3u"
printf '%s\n' \
    "$PLAYLIST_LIB/Parser/New.mp3" \
    'file://[' \
    > "$PLAYLIST_LIB/Parser.m3u"
if "$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Parser.m3u" > "$EVIDENCE_DIR/playlist-parser-failure.txt" 2>&1; then
    echo "ipod-sync.sh accepted a playlist its parser could not finish" >&2
    exit 1
fi
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3') \
    "$PLAYLIST_IPOD/Parser.m3u"
test ! -e "$PLAYLIST_IPOD/iPod_Control/Music/Parser/New.mp3"

printf -v LONG_PLAYLIST_STEM '%0251d' 0
mkdir -p "$PLAYLIST_LIB/Long Atomic"
printf 'long atomic track\n' > "$PLAYLIST_LIB/Long Atomic/Track.mp3"
printf '%s\n' "$PLAYLIST_LIB/Long Atomic/Track.mp3" \
    > "$PLAYLIST_LIB/$LONG_PLAYLIST_STEM.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/$LONG_PLAYLIST_STEM.m3u" \
    > "$EVIDENCE_DIR/playlist-long-atomic-name.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Long Atomic/Track.mp3') \
    "$PLAYLIST_IPOD/$LONG_PLAYLIST_STEM.m3u"
test -f "$PLAYLIST_IPOD/iPod_Control/Music/Long Atomic/Track.mp3"
test -z "$(find "$PLAYLIST_IPOD" -maxdepth 1 -name '.ipod-tmp.*' -print -quit)"

FAILING_MV_PATH="$TEST_ROOT/failing-mv-path"
mkdir -p "$FAILING_MV_PATH"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    "if [[ \"\${!#}\" == \"\$FAIL_MOVE_TARGET\" ]]; then exit 1; fi" \
    "exec \"\$REAL_MV\" \"\$@\"" \
    > "$FAILING_MV_PATH/mv"
chmod +x "$FAILING_MV_PATH/mv"
REAL_MV="$(command -v mv)"
printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3' \
    > "$PLAYLIST_IPOD/Atomic.m3u"
printf '%s\n' "$PLAYLIST_LIB/Parser/New.mp3" > "$PLAYLIST_LIB/Atomic.m3u"
if env PATH="$FAILING_MV_PATH:$BASE_PATH" \
    FAIL_MOVE_TARGET="$PLAYLIST_IPOD/Atomic.m3u" \
    REAL_MV="$REAL_MV" \
    "$ROOT/ipod-sync.sh" \
        --ipod "$PLAYLIST_IPOD" \
        "$PLAYLIST_LIB/Atomic.m3u" \
        > "$EVIDENCE_DIR/playlist-atomic-sync.txt" 2>&1; then
    echo "ipod-sync.sh reported success after playlist replacement failed" >&2
    exit 1
fi
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3') \
    "$PLAYLIST_IPOD/Atomic.m3u"
test -z "$(find "$PLAYLIST_IPOD" -maxdepth 1 -name '.ipod-tmp.*' -print -quit)"

ATOMIC_REMOVE_IPOD="$TEST_ROOT/atomic-remove-target"
mkdir -p \
    "$ATOMIC_REMOVE_IPOD/iPod_Control/iTunes" \
    "$ATOMIC_REMOVE_IPOD/iPod_Control/Music/Album" \
    "$ATOMIC_REMOVE_IPOD/iPod_Control/Speakable"
printf 'one\n' > "$ATOMIC_REMOVE_IPOD/iPod_Control/Music/Album/One.mp3"
printf 'two\n' > "$ATOMIC_REMOVE_IPOD/iPod_Control/Music/Album/Two.mp3"
printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Album/One.mp3' \
    'iPod_Control/Music/Album/Two.mp3' \
    > "$ATOMIC_REMOVE_IPOD/Atomic Remove.m3u"
if PATH="$FAILING_MV_PATH:$BASE_PATH" \
    FAIL_MOVE_TARGET="$ATOMIC_REMOVE_IPOD/Atomic Remove.m3u" \
    REAL_MV="$REAL_MV" \
    run_approved "$ROOT/ipod-remove.sh" \
        --ipod "$ATOMIC_REMOVE_IPOD" \
        --yes \
        'Album/One.mp3' \
        > "$EVIDENCE_DIR/playlist-atomic-remove.txt" 2>&1; then
    echo "ipod-remove.sh reported success after playlist replacement failed" >&2
    exit 1
fi
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Album/One.mp3' \
    'iPod_Control/Music/Album/Two.mp3') \
    "$ATOMIC_REMOVE_IPOD/Atomic Remove.m3u"
test -z "$(find "$ATOMIC_REMOVE_IPOD" -maxdepth 1 -name '.ipod-tmp.*' -print -quit)"

# A removed track leaves every list that names it, and a list that loses its
# last track disappears rather than survive as a playlist that plays nothing.
mv "$PLAYLIST_IPOD/Summer Mix.m3u" "$PLAYLIST_IPOD/Summer Mix.M3U"
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    'Neil Young/Harvest Moon.mp3' > "$EVIDENCE_DIR/playlist-prune.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Beach Boys/Surfin.mp3' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3') \
    "$PLAYLIST_IPOD/Summer Mix.M3U"
grep -Fq "Playlist 'Summer Mix': dropped 1 removed track(s)" \
    "$EVIDENCE_DIR/playlist-prune.txt"

run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    'Beach Boys' > "$EVIDENCE_DIR/playlist-prune-empty.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Best_ Hits.m3u"
grep -Fq "Removed playlist 'Best_ Hits': every track it listed is gone" \
    "$EVIDENCE_DIR/playlist-prune-empty.txt"
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3') \
    "$PLAYLIST_IPOD/Party.m3u"

# Wiping clears the playlists with the tracks, and the backup keeps them: each
# list is the only record of which songs made up that playlist.
printf '%s\n' \
    '[playlist]' \
    'File1=iPod_Control/Music/Neil Young/Heart of Gold.mp3' \
    > "$PLAYLIST_IPOD/Radio.PLS"
run_approved "$ROOT/ipod-wipe.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --backup "$TEST_ROOT/playlist-backup" \
    --yes > "$EVIDENCE_DIR/playlist-wipe.txt" 2>&1
test -s "$TEST_ROOT/playlist-backup/Playlists/Summer Mix.M3U"
test -s "$TEST_ROOT/playlist-backup/Playlists/Party.m3u"
test -s "$TEST_ROOT/playlist-backup/Playlists/Radio.PLS"
test -z "$(find "$PLAYLIST_IPOD" -maxdepth 1 -type f \
    \( -iname '*.m3u' -o -iname '*.pls' \) -print -quit)"

# --clear starts the device over, so the playlists referencing the deleted
# tracks go too.
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Summer Mix.m3u" > "$EVIDENCE_DIR/playlist-clear-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/Summer Mix.m3u"
mv "$PLAYLIST_IPOD/Summer Mix.m3u" "$PLAYLIST_IPOD/Summer Mix.M3U"
run_approved "$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --clear \
    --yes \
    "$PLAYLIST_LIB/Beach Boys" < /dev/null \
    > "$EVIDENCE_DIR/playlist-clear.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Summer Mix.M3U"
grep -Fq 'Removed 1 playlist(s)' "$EVIDENCE_DIR/playlist-clear.txt"

# A playlist can be deleted by name, which removes only the list itself. The
# GUI's per-playlist trash button drives exactly this.
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Summer Mix.m3u" > "$EVIDENCE_DIR/playlist-byname-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/Summer Mix.m3u"
printf '%s\n' 'File1=iPod_Control/Music/Beach Boys/Surfin.mp3' \
    > "$PLAYLIST_IPOD/Legacy.PLS"

# An unknown name must not delete anything, and says what the device has,
# since with no screen the names are otherwise only spoken audio.
if "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'No Such List' > "$EVIDENCE_DIR/playlist-byname-unknown.txt" 2>&1; then
    echo "ipod-remove.sh deleted a playlist that does not exist" >&2
    exit 1
fi
grep -Fq 'Summer Mix' "$EVIDENCE_DIR/playlist-byname-unknown.txt"
grep -Fq 'Legacy' "$EVIDENCE_DIR/playlist-byname-unknown.txt"
test -f "$PLAYLIST_IPOD/Summer Mix.m3u"

# A separator in a playlist name is refused before it can point anywhere.
if "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist '../iPod_Control/iTunes/iTunesSD' \
    > "$EVIDENCE_DIR/playlist-byname-escape.txt" 2>&1; then
    echo "ipod-remove.sh accepted a playlist name with separators" >&2
    exit 1
fi
test -s "$PLAYLIST_IPOD/iPod_Control/iTunes/iTunesSD"

if "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist '..\\iPod_Control\\iTunes\\iTunesSD' \
    > "$EVIDENCE_DIR/playlist-byname-windows-escape.txt" 2>&1; then
    echo "ipod-remove.sh accepted a playlist name with backslash separators" >&2
    exit 1
fi
test -s "$PLAYLIST_IPOD/iPod_Control/iTunes/iTunesSD"

printf '%s\n' "$PLAYLIST_LIB/Neil Young/Heart of Gold.mp3" \
    > "$PLAYLIST_LIB/mix..v2.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/mix..v2.m3u" > "$EVIDENCE_DIR/playlist-double-dot-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/mix..v2.m3u"
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'mix..v2' > "$EVIDENCE_DIR/playlist-double-dot-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/mix..v2.m3u"

printf '%s\n' "$PLAYLIST_LIB/Neil Young/Heart of Gold.mp3" \
    > "$PLAYLIST_LIB/mix.m3u.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/mix.m3u.m3u" > "$EVIDENCE_DIR/playlist-extension-stem-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/mix.m3u.m3u"
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'mix.m3u' > "$EVIDENCE_DIR/playlist-extension-stem-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/mix.m3u.m3u"

run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Legacy' > "$EVIDENCE_DIR/playlist-pls-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Legacy.PLS"

printf '%s\n' '#EXTM3U' > "$PLAYLIST_IPOD/Priority.m3u"
printf '%s\n' '[playlist]' > "$PLAYLIST_IPOD/Priority.pls"
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Priority.m3u' > "$EVIDENCE_DIR/playlist-format-priority.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Priority.m3u"
test -f "$PLAYLIST_IPOD/Priority.pls"
run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Priority.pls' >> "$EVIDENCE_DIR/playlist-format-priority.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Priority.pls"

run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Summer Mix' > "$EVIDENCE_DIR/playlist-byname-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Summer Mix.m3u"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young/Heart of Gold.mp3"
grep -Fq 'the songs they listed stay' "$EVIDENCE_DIR/playlist-byname-delete.txt"
grep -Fq 'Rebuilding iTunesSD database' "$EVIDENCE_DIR/playlist-byname-delete.txt"

# A playlist made in the GUI is a file the GUI writes and the sync reads, so
# the two halves are checked against each other rather than each against its
# own idea of the format. tests/gui-playlists.py covers making and editing one;
# this takes a list built by that same code onto a device.
GUI_PLAYLISTS="$TEST_ROOT/gui-playlists"
mkdir -p "$GUI_PLAYLISTS"
/usr/bin/python3 - "$GUI_PLAYLISTS" "$PLAYLIST_LIB" <<PY > "$EVIDENCE_DIR/gui-playlist-build.txt"
import sys
sys.path.insert(0, "$ROOT")
from ipod_gui.playlists import (
    add_entries, create_local_playlist, move_entry, read_playlist_entries,
)

store, library = sys.argv[1], sys.argv[2]
surfin = library + "/Beach Boys/Surfin.mp3"
gold = library + "/Neil Young/Heart of Gold.mp3"
path = create_local_playlist(store, "Gym Mix")
print("added", add_entries(path, [surfin, gold]))
# Dragged into a different order, which is the one thing about a playlist the
# user arranged by hand.
print("moved", move_entry(path, 1, 0))
print("entries", read_playlist_entries(path))
add_entries(create_local_playlist(store, "Emptied"), [surfin])
PY

"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --playlist-voiceover \
    "$GUI_PLAYLISTS/Gym Mix.m3u" > "$EVIDENCE_DIR/gui-playlist-sync.txt" 2>&1

# The order the file was left in is the order on the device: the sync copies
# the entries as listed, so a drag in the window survives all the way onto a
# player with no screen to reorder it from.
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3' \
    'iPod_Control/Music/Beach Boys/Surfin.mp3') \
    "$PLAYLIST_IPOD/Gym Mix.m3u"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young/Heart of Gold.mp3"

# Taking every song out of a playlist and syncing removes it from the device,
# which is what the window's per-row Remove finally does once Sync is pressed.
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --playlist-voiceover \
    "$GUI_PLAYLISTS/Emptied.m3u" > "$EVIDENCE_DIR/gui-playlist-emptied.txt" 2>&1
test -f "$PLAYLIST_IPOD/Emptied.m3u"
/usr/bin/python3 - "$GUI_PLAYLISTS" "$PLAYLIST_LIB" <<PY
import sys
sys.path.insert(0, "$ROOT")
from ipod_gui.playlists import remove_entry

store, library = sys.argv[1], sys.argv[2]
remove_entry(store + "/Emptied.m3u", library + "/Beach Boys/Surfin.mp3")
PY
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --playlist-voiceover \
    "$GUI_PLAYLISTS/Emptied.m3u" >> "$EVIDENCE_DIR/gui-playlist-emptied.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Emptied.m3u"
grep -Fq 'removed from the device' "$EVIDENCE_DIR/gui-playlist-emptied.txt"
# The songs it listed stay: emptying a playlist is not deleting the music.
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"

/usr/bin/python3 "$ROOT/tests/gui-playlists.py" \
    2> "$EVIDENCE_DIR/gui-playlists.txt"

# The demo builder's guard, which is the one step in this repository that
# deletes a directory somebody named. It runs here as well as in CI because
# the suite is what gets run before pushing, and it needs nothing this file
# has not already got.
/usr/bin/python3 "$ROOT/tests/demo-library-guard.py" \
    > "$EVIDENCE_DIR/demo-library-guard.txt"

/usr/bin/python3 "$ROOT/tests/gui-actions-smoke.py" \
    > "$EVIDENCE_DIR/gui-actions.json"

# Downloading has to produce something the firmware can decode, which is the
# whole point of the flags rather than an incidental detail, so the arguments
# are asserted rather than just the exit status.
FETCH_OUT="$TEST_ROOT/youtube"
FETCH_RECORD="$EVIDENCE_DIR/yt-dlp-invocation.json"

# A track the output folder already held. Only what this run downloads may
# reach the device: copying the whole folder every time would push a year of
# downloads back onto a 2GB device the moment one new song is fetched.
mkdir -p "$FETCH_OUT/Old Artist"
printf 'old download\n' > "$FETCH_OUT/Old Artist/Old Song.m4a"

# These environment changes are confined to the subshell.
# shellcheck disable=SC2030,SC2031
(
    # tests/bin supplies the yt-dlp and ffmpeg doubles, and the findmnt double
    # that lets --sync autodetect the fake iPod the way a real one is found.
    PATH="$ROOT/tests/bin:$PATH"
    export FAKE_IPOD_MOUNT="$IPOD"
    export FAKE_YTDLP_RECORD="$FETCH_RECORD"
    export IPOD_VENV_YT_DLP="$ROOT/tests/bin/yt-dlp"
    "$ROOT/ipod-fetch.sh" \
        --output "$FETCH_OUT" \
        --single \
        --sync \
        'https://example.invalid/watch?v=test'
) > "$EVIDENCE_DIR/fetch-and-sync.txt" 2>&1

test -f "$FETCH_OUT/Test Artist/Test Track [testvideo].mp3"
test -s "$FETCH_OUT/.fetched"

# Artist folders are handed to the sync, not their parent. Passing $FETCH_OUT
# itself would bury every track under an extra "youtube" directory and shift
# what --dir-playlists=1 treats as the artist level.
test -f "$IPOD/iPod_Control/Music/Test Artist/Test Track [testvideo].mp3"
test ! -e "$IPOD/iPod_Control/Music/youtube"
test ! -e "$IPOD/iPod_Control/Music/Old Artist"

/usr/bin/python3 - "$FETCH_RECORD" <<'PY'
import json
import sys
from pathlib import Path

args = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))


def value_of(flag):
    return args[args.index(flag) + 1]


# Opus is what YouTube serves and the one thing the shuffle cannot play, so a
# conversion at the documented bitrate is not optional.
#
# Regression: native ffmpeg AAC crackled on a real 4G, while this MP3
# configuration played cleanly. README.md owns the hardware-bisect details.
assert value_of("--audio-format") == "mp3", args
assert value_of("--audio-quality") == "256k", args
assert "--extract-audio" in args, args

# Regression: plain "bestaudio" selects YouTube's 5.1 AAC stream at 388k, a
# 30MB download on a 2GB device that has to be downmixed to stereo anyway.
assert "audio_channels<=2" in value_of("--format"), args
assert "/bestaudio" not in value_of("--format"), args
assert value_of("--postprocessor-args").startswith("ExtractAudio:-ac 2"), args

# Regression: brickwalled pop masters already peak at full scale, and the
# encoder adds ringing on top, so the shuffle's decoder clamps and crackles.
# The limiter gives it headroom; level=false stops the limiter handing the gain
# straight back, which would leave the peaks exactly where they were.
#
# latency=true compensates the lookahead, without which every track is shifted
# late by the attack time even when the limiter never engages at all.
#
# 44.1kHz is not what caused the crackling, since AAC failed identically at
# both rates. It is pinned because it is the shuffle's native DAC rate and the
# exact configuration that was listened to on the device, and YouTube's Opus is
# always 48kHz, so dropping it would ship a format nobody verified by ear.
ppa = value_of("--postprocessor-args")
assert "alimiter=limit=0.631" in ppa, args
assert "level=false" in ppa, args
assert "latency=true" in ppa, args
assert "aresample=44100" in ppa, args

# Regression: --trim-filenames limits the whole path, not the filename, so a
# long --output truncated the song title itself and collided tracks.
assert "--trim-filenames" not in args, args

# Without this yt-dlp cannot say which files it just downloaded, and --sync
# would have to hand over the entire output folder and hope the copy skipped
# everything already on the device.
assert value_of("--print-to-file") == "after_move:filepath", args

# Regression: without a JavaScript runtime, YouTube's signature challenge goes
# unsolved and every commercial track fails with HTTP 403 while metadata
# extraction still succeeds, so the tool looks like it works right up until it
# downloads nothing.
assert value_of("--js-runtimes") == "deno", args

# Without tags the device shows scrambled filenames and tag playlists have
# nothing to group by.
assert "--embed-metadata" in args, args

# Names have to survive the copy onto vfat, where YouTube's titles otherwise
# fail on characters the filesystem rejects.
assert "--windows-filenames" in args, args

# No screen, so cover art is bytes off a 2GB device for nothing.
assert "--no-embed-thumbnail" in args, args

assert "--no-playlist" in args, args
assert value_of("--output").endswith(
    "/%(artist,uploader)s/%(track,title)s [%(id)s].%(ext)s"
), args
assert value_of("--download-archive").endswith("/.fetched"), args
print(json.dumps(args, indent=2))
PY

grep -Fq 'Downloaded 1 track(s)' "$EVIDENCE_DIR/fetch-and-sync.txt"

# Re-fetching a link already in the archive must copy nothing, rather than
# treat "I downloaded no files" as a reason to fall back to the whole folder.
# These environment changes are confined to the subshell.
# shellcheck disable=SC2030,SC2031
(
    PATH="$ROOT/tests/bin:$PATH"
    export FAKE_IPOD_MOUNT="$IPOD"
    export FAKE_YTDLP_RECORD="$EVIDENCE_DIR/yt-dlp-repeat-invocation.json"
    export IPOD_VENV_YT_DLP="$ROOT/tests/bin/yt-dlp"
    "$ROOT/ipod-fetch.sh" \
        --output "$FETCH_OUT" \
        --single \
        --sync \
        'https://example.invalid/watch?v=test'
) > "$EVIDENCE_DIR/fetch-nothing-new.txt" 2>&1

grep -Fq 'Nothing new to copy onto the iPod.' "$EVIDENCE_DIR/fetch-nothing-new.txt"
test ! -e "$IPOD/iPod_Control/Music/Old Artist"

# --new-tracks is how the GUI learns what to copy after a download, so the file
# has to name exactly what arrived and nothing the folder already held.
NEW_LIST="$TEST_ROOT/new-tracks.list"
FRESH_OUT="$TEST_ROOT/youtube-fresh"
mkdir -p "$FRESH_OUT/Old Artist"
printf 'old download\n' > "$FRESH_OUT/Old Artist/Old Song.m4a"
# These environment changes are confined to the subshell.
# shellcheck disable=SC2030,SC2031
(
    PATH="$ROOT/tests/bin:$PATH"
    export FAKE_YTDLP_RECORD="$EVIDENCE_DIR/yt-dlp-new-tracks-invocation.json"
    export IPOD_VENV_YT_DLP="$ROOT/tests/bin/yt-dlp"
    "$ROOT/ipod-fetch.sh" \
        --output "$FRESH_OUT" \
        --single \
        --new-tracks "$NEW_LIST" \
        'https://example.invalid/watch?v=test'
) > "$EVIDENCE_DIR/fetch-new-tracks.txt" 2>&1

diff -u \
    <(printf '%s\n' "$FRESH_OUT/Test Artist/Test Track [testvideo].mp3") \
    "$NEW_LIST"

# An old yt-dlp cannot report what it downloaded at all. The list has to be
# deleted rather than left stale, because a leftover file reads as a definite
# answer: the GUI would copy tracks that are no longer there, or report having
# added nothing after a download that worked.
STALE_LIST="$TEST_ROOT/stale-tracks.list"
printf '%s\n' "$TEST_ROOT/gone.m4a" > "$STALE_LIST"
FALLBACK_OUT="$TEST_ROOT/youtube-old"
mkdir -p "$FALLBACK_OUT/Legacy Artist"
printf 'already downloaded\n' > "$FALLBACK_OUT/Legacy Artist/Legacy Song.m4a"
# These environment changes are confined to the subshell.
# shellcheck disable=SC2030,SC2031
(
    PATH="$ROOT/tests/bin:$PATH"
    export FAKE_IPOD_MOUNT="$IPOD"
    export FAKE_YTDLP_RECORD="$EVIDENCE_DIR/yt-dlp-no-print-invocation.json"
    export FAKE_YTDLP_SUPPORTS_PRINT_TO_FILE=0
    export IPOD_VENV_YT_DLP="$ROOT/tests/bin/yt-dlp"
    "$ROOT/ipod-fetch.sh" \
        --output "$FALLBACK_OUT" \
        --single \
        --sync \
        --new-tracks "$STALE_LIST" \
        'https://example.invalid/watch?v=test'
) > "$EVIDENCE_DIR/fetch-no-print-to-file.txt" 2>&1

test ! -e "$STALE_LIST"
grep -Fq 'too old to report which files it downloaded' \
    "$EVIDENCE_DIR/fetch-no-print-to-file.txt"
# Syncing every artist folder is the honest fallback when it cannot say which
# tracks are new, so the folder that was already there does reach the device.
test -s "$IPOD/iPod_Control/Music/Legacy Artist/Legacy Song.m4a"

/usr/bin/python3 - "$EVIDENCE_DIR/yt-dlp-no-print-invocation.json" <<'PY'
import json
import sys
from pathlib import Path

args = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert "--print-to-file" not in args, args
PY

# Previewing a search result downloads it before playing it, which is a script,
# a staging directory and a move rather than anything the in-memory GUI checks
# can see. Driven against the same doubles, because the file landing in the
# right place is the whole feature.
PREVIEW_CACHE_DIR="$TEST_ROOT/preview-cache"
PREVIEW_LIBRARY="$TEST_ROOT/preview-library"
# These environment changes are confined to the subshell.
# shellcheck disable=SC2030,SC2031
(
    PATH="$ROOT/tests/bin:$PATH"
    export FAKE_YTDLP_RECORD="$EVIDENCE_DIR/yt-dlp-preview-invocation.json"
    export IPOD_VENV_YT_DLP="$ROOT/tests/bin/yt-dlp"
    /usr/bin/python3 "$ROOT/tests/gui-preview-fetch-smoke.py" \
        "$PREVIEW_CACHE_DIR" "$PREVIEW_LIBRARY"
) > "$EVIDENCE_DIR/gui-preview-fetch.json"

test -f "$PREVIEW_CACHE_DIR/Test Artist/Test Track [testvideo].mp3"
# The music folder is what the cache exists to keep out of this: a preview that
# landed there would add a track nobody asked to keep.
test ! -e "$PREVIEW_LIBRARY"

/usr/bin/python3 - "$EVIDENCE_DIR/yt-dlp-preview-invocation.json" <<'PY'
import json
import sys
from pathlib import Path

args = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
output = args[args.index("--output") + 1]
archive = args[args.index("--download-archive") + 1]

# A preview is one video, never the playlist a link happens to belong to.
assert "--no-playlist" in args, args

# Both paths point into the staging directory the GUI made for this download,
# and neither into the cache itself. The archive is the reason: recorded in the
# cache, it would mark a video as fetched in a folder it can be pruned out of,
# and every later preview of that video would download nothing at all.
assert "/preview-cache/.incoming/" in output, args
assert "/preview-cache/.incoming/" in archive, args
PY

OLD_FETCH_OUT="$TEST_ROOT/old-yt-dlp"
OLD_FETCH_RECORD="$EVIDENCE_DIR/old-yt-dlp-invocation.json"
# These environment changes are confined to the subshell.
# shellcheck disable=SC2030,SC2031
(
    PATH="$ROOT/tests/bin:$PATH"
    export FAKE_YTDLP_RECORD="$OLD_FETCH_RECORD"
    export FAKE_YTDLP_SUPPORTS_JS_RUNTIMES=0
    export IPOD_VENV_YT_DLP="$TEST_ROOT/missing-yt-dlp"
    "$ROOT/ipod-fetch.sh" \
        --output "$OLD_FETCH_OUT" \
        --single \
        'https://example.invalid/watch?v=test'
) > "$EVIDENCE_DIR/old-yt-dlp-fallback.txt" 2>&1

/usr/bin/python3 - "$OLD_FETCH_RECORD" <<'PY'
import json
import sys
from pathlib import Path

args = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert "--js-runtimes" not in args, args
PY
grep -Fq 'yt-dlp is too old' "$EVIDENCE_DIR/old-yt-dlp-fallback.txt"

# ------------------------------------------------------ progress as it happens
#
# The scripts report what they are doing on a second stream, one JSON object
# per line, and that is what drives the window's sync bar. The whole promise is
# that it is additional: the same run, told twice, and the terminal reading is
# not touched by anything here.
#
# Two identical devices, one run with the stream and one without, because the
# only convincing way to say the human output is unchanged is to produce both
# and compare them.
PROGRESS_SOURCE="$TEST_ROOT/progress-source/Odd Album"
mkdir -p "$PROGRESS_SOURCE"
printf 'first\n' > "$PROGRESS_SOURCE/01 - Plain.mp3"
# The names a line-based protocol or a shell-written document would break on.
# Every one of them is ordinary here: track names arrive from tags and from
# YouTube titles.
printf 'second\n' > "$PROGRESS_SOURCE/02 - Say \"hi\", it's \\ fine.mp3"
printf 'third\n' > "$PROGRESS_SOURCE/03 - Line"$'\n'"break.mp3"
printf 'artwork\n' > "$PROGRESS_SOURCE/cover.flac"
ln -s /nowhere/at/all.mp3 "$PROGRESS_SOURCE/04 - Dangling.mp3"
printf '%s\n' \
    "$PROGRESS_SOURCE/01 - Plain.mp3" \
    "/nowhere/not-on-this-computer.mp3" \
    > "$TEST_ROOT/progress-source/Weekend.m3u"

progress_ipod() {
    local root="$1"
    mkdir -p \
        "$root/iPod_Control/iTunes" \
        "$root/iPod_Control/Music" \
        "$root/iPod_Control/Speakable" \
        "$root/iPod_Control/Device"
    printf 'device identity\n' > "$root/iPod_Control/Device/SysInfo"
}

PROGRESS_IPOD="$TEST_ROOT/progress-ipod"
QUIET_IPOD="$TEST_ROOT/progress-ipod-quiet"
progress_ipod "$PROGRESS_IPOD"
progress_ipod "$QUIET_IPOD"

"$ROOT/ipod-sync.sh" \
    --ipod "$PROGRESS_IPOD" \
    --playlist-voiceover \
    --progress-json \
    "$PROGRESS_SOURCE" "$TEST_ROOT/progress-source/Weekend.m3u" \
    3> "$EVIDENCE_DIR/progress-sync.ndjson" \
    > "$EVIDENCE_DIR/progress-sync-output.txt" 2>&1
"$ROOT/ipod-sync.sh" \
    --ipod "$QUIET_IPOD" \
    --playlist-voiceover \
    "$PROGRESS_SOURCE" "$TEST_ROOT/progress-source/Weekend.m3u" \
    > "$EVIDENCE_DIR/progress-sync-quiet-output.txt" 2>&1

# Read straight after the script returned, with nothing waiting on the writer:
# the stream is closed and its encoder waited for before a run ends, so a
# caller that has the exit code has the whole document too.
test -s "$EVIDENCE_DIR/progress-sync.ndjson"

diff -u \
    <(sed "s|$QUIET_IPOD|IPOD|g" "$EVIDENCE_DIR/progress-sync-quiet-output.txt") \
    <(sed "s|$PROGRESS_IPOD|IPOD|g" "$EVIDENCE_DIR/progress-sync-output.txt")

run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PROGRESS_IPOD" --yes --progress-json=7 \
    'Odd Album/01 - Plain.mp3' \
    7> "$EVIDENCE_DIR/progress-remove.ndjson" \
    > "$EVIDENCE_DIR/progress-remove-output.txt" 2>&1

run_approved "$ROOT/ipod-wipe.sh" \
    --ipod "$PROGRESS_IPOD" --yes --backup "$TEST_ROOT/progress-backup" \
    --progress-json \
    3> "$EVIDENCE_DIR/progress-wipe.ndjson" \
    > "$EVIDENCE_DIR/progress-wipe-output.txt" 2>&1

/usr/bin/python3 - \
    "$EVIDENCE_DIR/progress-sync.ndjson" \
    "$EVIDENCE_DIR/progress-remove.ndjson" \
    "$EVIDENCE_DIR/progress-wipe.ndjson" \
    "$PROGRESS_IPOD" <<'PY'
import json
import sys
from pathlib import Path


def stream(path):
    """Every event of one run, as JSON rather than as text to search."""
    events = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert events, path
    return events


def counted(events):
    """The events that are pieces of work, in the order they happened."""
    return [event for event in events if event["event"] in ("file", "playlist")]


def check_shape(events, script):
    assert events[0] == {"event": "start", "schema": 1, "script": script}, events[0]
    # One result, last, and nothing after it: a caller reads until the stream
    # ends and takes that event as what happened.
    results = [event for event in events if event["event"] == "result"]
    assert len(results) == 1, results
    assert events[-1] is results[0], events[-1]
    assert results[0]["ok"] is (results[0]["code"] == 0), results[0]
    return results[0]


def check_counting(events):
    """The count reaches the total, having gone up by one at a time.

    A bar that stops at nine tenths is the failure this rules out, and it is
    the one that comes back whenever the walk that counts and the walk that
    copies stop agreeing about what they are counting.

    Returns what the run planned, which is the total it should have ended on
    unless it turned up work it could not have planned for.
    """
    plans = [event for event in events if event["event"] == "plan"]
    assert len(plans) == 1, plans
    items = counted(events)
    assert [item["done"] for item in items] == list(range(1, len(items) + 1)), items
    assert items[-1]["done"] == items[-1]["total"], items[-1]
    assert items[-1]["total"] >= plans[0]["total"], (plans[0], items[-1])
    return plans[0]["total"], items[-1]["total"]


sync = stream(sys.argv[1])
result = check_shape(sync, "sync")
# A copy plans everything it will report on and reports nothing else, so the
# total it ends on is the one it announced before it started.
assert check_counting(sync) == (7, 7), sync
assert result["copied"] == 3, result
assert result["unsupported"] == 1, result
assert result["broken"] == 1, result
assert result["tracks"] == 3, result

# The device it settled on, said once, so a caller that let it autodetect
# knows which iPod it was talking about.
assert [event for event in sync if event["event"] == "device"] == [
    {"event": "device", "ipod": sys.argv[4]}
], sync

# A name arrives as it is on disk. A quote, a backslash and a newline in a
# track name are all ordinary, and each one would have ended a record early
# had the shell been writing this document itself.
copied = {
    event["name"] for event in sync
    if event["event"] == "file" and event["status"] == "copied"
}
assert '02 - Say "hi", it\'s \\ fine.mp3' in copied, copied
assert "03 - Line\nbreak.mp3" in copied, copied

# Every file the run considered is accounted for, once, and by what became of
# it rather than as a copy either way.
statuses = {}
for event in sync:
    if event["event"] == "file":
        statuses.setdefault(event["status"], []).append(event["name"])
assert sorted(statuses) == ["broken", "copied", "duplicate", "missing"], statuses
assert statuses["broken"] == ["04 - Dangling.mp3"], statuses
assert statuses["missing"] == ["not-on-this-computer.mp3"], statuses
# The playlist's own track was already copied with its folder, so the playlist
# pass finds it there rather than copying it twice.
assert statuses["duplicate"] == ["01 - Plain.mp3"], statuses
# The artwork was never counted into the work: the firmware cannot play it, so
# a bar including it could never reach its end.
assert "cover.flac" not in [name for names in statuses.values() for name in names]

playlists = [event for event in sync if event["event"] == "playlist"]
assert playlists == [
    {
        "event": "playlist",
        "status": "written",
        "name": "Weekend",
        "tracks": 1,
        "done": playlists[0]["done"],
        "total": playlists[0]["total"],
    }
], playlists

# The stages a run passes through, in order, for the stretches that are one
# long wait rather than a file at a time.
assert [
    (event["name"], event["state"]) for event in sync if event["event"] == "stage"
] == [
    ("copy", "start"), ("copy", "done"), ("rebuild", "start"), ("rebuild", "done"),
], sync

removal = stream(sys.argv[2])
result = check_shape(removal, "remove")
# One track was named and one playlist turned out to name it, which is work
# the run could not have planned; the total follows the count rather than the
# count passing the total.
assert check_counting(removal) == (1, 2), removal
assert result["removed"] == 1, result
assert result["tracks"] == 2, result
assert [
    (event["status"], event["name"]) for event in removal if event["event"] == "file"
] == [("removed", "Odd Album/01 - Plain.mp3")], removal
# Removing a track takes it out of the playlists that named it, which is work
# the run never planned; the total follows rather than the count passing it.
assert [
    (event["status"], event["name"], event["tracks"])
    for event in removal
    if event["event"] == "playlist"
] == [("removed", "Weekend", 0)], removal

wipe = stream(sys.argv[3])
result = check_shape(wipe, "wipe")
assert result["removed"] == 2, result
assert result["tracks"] == 0, result
# No plan and no items: a wipe is one bulk delete, and a bar with a
# denominator nothing ever counts against would sit at nothing for all of it.
assert not [event for event in wipe if event["event"] in ("plan", "file")], wipe
assert [
    (event["name"], event["state"]) for event in wipe if event["event"] == "stage"
] == [
    ("backup", "start"), ("backup", "done"),
    ("clear", "start"), ("clear", "done"),
    ("rebuild", "start"), ("rebuild", "done"),
], wipe
PY

# A failure is reported on the stream too, with the code the caller is about to
# be given: a run that ends without saying so is one a reader waits on.
progress_failure_status=0
"$ROOT/ipod-sync.sh" \
    --ipod "$TEST_ROOT/no-ipod-is-here" --progress-json "$PROGRESS_SOURCE" \
    3> "$EVIDENCE_DIR/progress-no-ipod.ndjson" \
    > "$EVIDENCE_DIR/progress-no-ipod.txt" 2>&1 || progress_failure_status=$?
test "$progress_failure_status" -eq 3
diff -u <(printf '%s\n' \
    '{"event": "start", "schema": 1, "script": "sync"}' \
    '{"event": "result", "ok": false, "code": 3, "copied": 0, "duplicates": 0, "unsupported": 0, "broken": 0}') \
    "$EVIDENCE_DIR/progress-no-ipod.ndjson"

# And a failure the script never reached the device to have. Autodetection
# runs in a command substitution, so a run with nothing plugged in dies in a
# subshell holding its own copy of the descriptor: reporting from there would
# write the result twice and wait for an encoder that is not its child, while
# reporting from neither would leave the stream unfinished behind an exit
# code. A stand-in findmnt says there is nothing mounted, rather than trusting
# that the machine running this has no iPod on it.
NO_MOUNTS_PATH="$TEST_ROOT/no-mounts-bin"
mkdir -p "$NO_MOUNTS_PATH"
printf '%s\n' '#!/bin/sh' 'exit 0' > "$NO_MOUNTS_PATH/findmnt"
chmod +x "$NO_MOUNTS_PATH/findmnt"
progress_autodetect_status=0
env PATH="$NO_MOUNTS_PATH:$BASE_PATH" \
    "$ROOT/ipod-sync.sh" --progress-json "$PROGRESS_SOURCE" \
    3> "$EVIDENCE_DIR/progress-nothing-mounted.ndjson" \
    > "$EVIDENCE_DIR/progress-nothing-mounted.txt" 2>&1 \
    || progress_autodetect_status=$?
test "$progress_autodetect_status" -eq 3
diff -u <(printf '%s\n' \
    '{"event": "start", "schema": 1, "script": "sync"}' \
    '{"event": "result", "ok": false, "code": 3, "copied": 0, "duplicates": 0, "unsupported": 0, "broken": 0}') \
    "$EVIDENCE_DIR/progress-nothing-mounted.ndjson"
diff -u <(printf '%s\n' \
    'error: No mounted iPod found. Plug it in, or pass the mount point explicitly.') \
    <(sed 's/\x1b\[[0-9;]*m//g' "$EVIDENCE_DIR/progress-nothing-mounted.txt")

# A caller that walks away mid-run must not take the run with it. Closing the
# window during a copy is exactly this, and a shell writing to a pipe nobody
# holds is killed outright rather than told about it, which would leave an iPod
# half written because nobody was watching.
CLOSED_IPOD="$TEST_ROOT/progress-ipod-closed"
progress_ipod "$CLOSED_IPOD"
closed_reader_status=0
"$ROOT/ipod-sync.sh" \
    --ipod "$CLOSED_IPOD" --progress-json "$PROGRESS_SOURCE" \
    3>&1 1> "$EVIDENCE_DIR/progress-closed-reader.txt" 2>&1 \
    | head -1 > "$EVIDENCE_DIR/progress-closed-reader.ndjson" \
    || closed_reader_status=$?
test "$closed_reader_status" -eq 0
test "$(wc -l < "$EVIDENCE_DIR/progress-closed-reader.ndjson")" -eq 1
# Which end noticed first is a matter of timing - the writer finding the pipe
# gone, or the encoder failing to write to it - so what is pinned is that the
# run said something about it rather than which of the two sentences it was.
grep -Eq 'The progress stream (stopped|ended badly)' \
    "$EVIDENCE_DIR/progress-closed-reader.txt"
test -f "$CLOSED_IPOD/iPod_Control/Music/Odd Album/01 - Plain.mp3"

# A descriptor nobody opened is a mistake in the command rather than a run
# whose progress quietly goes nowhere.
progress_bad_fd_status=0
"$ROOT/ipod-sync.sh" --ipod "$PROGRESS_IPOD" --progress-json --rebuild-only \
    > "$EVIDENCE_DIR/progress-descriptor-closed.txt" 2>&1 3>&- \
    || progress_bad_fd_status=$?
test "$progress_bad_fd_status" -eq 1
grep -Fq 'needs descriptor 3 open for writing' \
    "$EVIDENCE_DIR/progress-descriptor-closed.txt"

progress_bad_number_status=0
"$ROOT/ipod-sync.sh" --ipod "$PROGRESS_IPOD" --progress-json=stdout --rebuild-only \
    > "$EVIDENCE_DIR/progress-descriptor-named.txt" 2>&1 \
    || progress_bad_number_status=$?
test "$progress_bad_number_status" -eq 1
grep -Fq "takes a descriptor number, not 'stdout'" \
    "$EVIDENCE_DIR/progress-descriptor-named.txt"

# And the descriptor that is no descriptor at all. The flag was still typed, so
# a run that took it as "no stream wanted" would be the one spelling that
# reports nowhere without saying so. Asked of all three scripts, because the
# flag is theirs jointly and a refusal in one of them is not the contract.
for script in sync remove wipe; do
    progress_empty_status=0
    "$ROOT/ipod-$script.sh" --ipod "$PROGRESS_IPOD" --progress-json= --yes \
        > "$EVIDENCE_DIR/progress-descriptor-empty-$script.txt" 2>&1 \
        || progress_empty_status=$?
    test "$progress_empty_status" -eq 1
    grep -Fq "takes a descriptor number, not an empty one" \
        "$EVIDENCE_DIR/progress-descriptor-empty-$script.txt"
done

# A run refused after the stream is open still ends with a result: a caller
# reads until the stream ends and takes that event as what happened, so one
# that stops after "start" leaves the bar waiting on a run that is already
# over. Both are reachable from the window, which removes a playlist by the
# name a stale row showed.
REFUSAL_IPOD="$TEST_ROOT/progress-ipod-refusal"
progress_ipod "$REFUSAL_IPOD"
mkdir -p "$REFUSAL_IPOD/iPod_Control/Music/Odd Album"
printf 'first\n' > "$REFUSAL_IPOD/iPod_Control/Music/Odd Album/01 - Plain.mp3"

progress_no_playlist_status=0
"$ROOT/ipod-remove.sh" --ipod "$REFUSAL_IPOD" --yes --progress-json --playlist \
    -- 'Not On This iPod' \
    3> "$EVIDENCE_DIR/progress-no-playlist.ndjson" \
    > "$EVIDENCE_DIR/progress-no-playlist.txt" 2>&1 \
    || progress_no_playlist_status=$?
test "$progress_no_playlist_status" -eq 1
grep -Fq "No playlist called 'Not On This iPod' on this iPod." \
    "$EVIDENCE_DIR/progress-no-playlist.txt"
diff -u <(printf '%s\n' \
    '{"event": "start", "schema": 1, "script": "remove"}' \
    "{\"event\": \"device\", \"ipod\": \"$REFUSAL_IPOD\"}" \
    '{"event": "result", "ok": false, "code": 1, "removed": 0, "playlists": 0}') \
    "$EVIDENCE_DIR/progress-no-playlist.ndjson"

progress_no_target_status=0
"$ROOT/ipod-remove.sh" --ipod "$REFUSAL_IPOD" --yes --progress-json \
    3> "$EVIDENCE_DIR/progress-no-target.ndjson" \
    > "$EVIDENCE_DIR/progress-no-target.txt" 2>&1 \
    || progress_no_target_status=$?
test "$progress_no_target_status" -eq 1
diff -u <(printf '%s\n' \
    '{"event": "start", "schema": 1, "script": "remove"}' \
    "{\"event\": \"device\", \"ipod\": \"$REFUSAL_IPOD\"}" \
    '{"event": "result", "ok": false, "code": 1, "removed": 0, "playlists": 0}') \
    "$EVIDENCE_DIR/progress-no-target.ndjson"
test -f "$REFUSAL_IPOD/iPod_Control/Music/Odd Album/01 - Plain.mp3"

# The unit separator holds the fields of a record apart, and unlike NUL it is a
# byte a Linux filename may legally hold: one arriving in a name split into a
# field of its own, which the encoder refuses - taking it down and leaving the
# rest of the run unreported behind a warning. It is replaced on the way out,
# the way the device stems already replace the bytes FAT will not take.
SEPARATOR_SOURCE="$TEST_ROOT/progress-separator-source/Odd Album"
mkdir -p "$SEPARATOR_SOURCE"
printf 'unit\n' > "$SEPARATOR_SOURCE/05 - Unit"$'\037'"separator.mp3"
SEPARATOR_IPOD="$TEST_ROOT/progress-separator-ipod"
progress_ipod "$SEPARATOR_IPOD"
"$ROOT/ipod-sync.sh" --ipod "$SEPARATOR_IPOD" --progress-json "$SEPARATOR_SOURCE" \
    3> "$EVIDENCE_DIR/progress-separator.ndjson" \
    > "$EVIDENCE_DIR/progress-separator.txt" 2>&1
/usr/bin/python3 - "$EVIDENCE_DIR/progress-separator.ndjson" <<'PY'
import json
import sys
from pathlib import Path

events = [
    json.loads(line)
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    if line
]
# The whole run is on the stream: the file, the rebuild after it, and the
# result last. A record the encoder refused would have ended it at the copy.
assert [event["event"] for event in events] == [
    "start", "device", "plan", "stage", "file", "stage", "stage", "stage", "result"
], events
copied = [event for event in events if event["event"] == "file"]
assert [(event["status"], event["name"]) for event in copied] == [
    ("copied", "05 - Unit_separator.mp3")
], copied
assert events[-1]["ok"] is True, events[-1]
assert events[-1]["copied"] == 1, events[-1]
PY
if grep -Fq 'The progress stream' "$EVIDENCE_DIR/progress-separator.txt"; then
    echo "a unit separator in a track name stopped the progress stream" >&2
    exit 1
fi

# ------------------------------------------------------- exit codes and --check
#
# The five states a caller has to branch on, each asserted as the number it is
# rather than as the sentence beside it. Both streams go to the evidence file,
# because a code is only useful while the sentence still says what happened,
# and stdin comes from /dev/null so a prompt is declined rather than waited on.
#
# The status the run actually produced is left in LAST_EXIT_CODE, so a check
# that two programs agree on a code can compare what they returned.
LAST_EXIT_CODE=0
exit_code_is() {
    local expected="$1" evidence="$2" status=0
    shift 2
    "$@" > "$EVIDENCE_DIR/$evidence" 2>&1 < /dev/null || status=$?
    LAST_EXIT_CODE="$status"
    if (( status != expected )); then
        echo "expected exit $expected, got $status: $*" >&2
        exit 1
    fi
}

# Nothing plugged in, and a path explicitly named with nothing at it. They are
# one answer to a caller - point me at an iPod - so they are one code.
# The positional parameters below belong to the bash -c program rather than to
# this file, so they are quoted against expanding here.
# shellcheck disable=SC2016
exit_code_is 3 exit-code-no-ipod.txt \
    bash -c 'source "$1"; list_vfat_mounts() { :; }; find_ipod ""' \
    _ "$ROOT/lib.sh"
exit_code_is 3 exit-code-no-ipod-at-path.txt \
    "$ROOT/ipod-remove.sh" --ipod "$TEST_ROOT/nothing-is-mounted-here" --list

mkdir -p "$TEST_ROOT/two-ipods/one/iPod_Control" "$TEST_ROOT/two-ipods/two/iPod_Control"
# shellcheck disable=SC2016
exit_code_is 4 exit-code-several-ipods.txt \
    bash -c 'source "$1"
        root="$2"
        list_vfat_mounts() { printf "%s\n" "$root/one" "$root/two"; }
        find_ipod ""' \
    _ "$ROOT/lib.sh" "$TEST_ROOT/two-ipods"
grep -Fq 'Multiple iPods found' "$EVIDENCE_DIR/exit-code-several-ipods.txt"

# The device going away part way through is the failure this project sees
# most, and it arrives as whichever command touched the volume next rather
# than as a check anybody wrote. Here it is the database builder, which is the
# last thing every device-changing script does.
cat > "$TEST_ROOT/vanishing-db-builder.py" <<'PY'
#!/usr/bin/env python3
"""Stands in for an iPod unplugged while the database is being written."""

import shutil
import sys

shutil.rmtree(sys.argv[-1])
sys.exit(1)
PY
VANISH_IPOD="$TEST_ROOT/vanishing-ipod"
VANISH_SOURCE="$TEST_ROOT/vanishing-source"
mkdir -p "$VANISH_SOURCE"
printf 'a song\n' > "$VANISH_SOURCE/01 - Song.mp3"
mkdir -p "$VANISH_IPOD/iPod_Control/iTunes" \
    "$VANISH_IPOD/iPod_Control/Music" \
    "$VANISH_IPOD/iPod_Control/Speakable"
IPOD_DB_TOOL="$TEST_ROOT/vanishing-db-builder.py" \
    exit_code_is 5 exit-code-device-gone.txt \
    "$ROOT/ipod-sync.sh" --ipod "$VANISH_IPOD" --yes "$VANISH_SOURCE"
grep -Fq 'stopped answering' "$EVIDENCE_DIR/exit-code-device-gone.txt"
# What a script leaves with when the volume goes away, kept for the writer to
# be held against further down: it has to answer with this same number.
lib_gone_code="$LAST_EXIT_CODE"

# The same answer when the volume goes away before the first count rather than
# at the last write. A wipe reads what it is about to delete before it deletes
# anything, and a device that is no longer there holds no files by every
# reading a count can take: told that as a number, the run would go on to
# verify a backup it never copied, recreate the skeleton on the mount point
# left behind, and call that a wipe. The stand-in lsusb is where the iPod goes
# because that is what assert_shuffle asks about the hardware, which is the
# last thing to run while the script still believes the volume is mounted.
UNPLUG_PATH="$TEST_ROOT/unplugging-bin"
UNPLUG_IPOD="$TEST_ROOT/unplugged-mid-wipe"
UNPLUG_BACKUP="$TEST_ROOT/unplugged-mid-wipe-backup"
mkdir -p "$UNPLUG_PATH" \
    "$UNPLUG_IPOD/iPod_Control/iTunes" \
    "$UNPLUG_IPOD/iPod_Control/Music" \
    "$UNPLUG_IPOD/iPod_Control/Speakable"
# The mount point reaches the stub through its own environment, so the name
# below belongs to the stub rather than to this file.
# shellcheck disable=SC2016
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'rm -rf -- "$UNPLUG_IPOD/iPod_Control"' \
    'exit 0' \
    > "$UNPLUG_PATH/lsusb"
chmod +x "$UNPLUG_PATH/lsusb"
# Taken while the iPod is still there, so the run below is refused for the
# device having gone rather than for an authorization it never had. An empty
# device is what makes that possible: the count the plan was hashed from is
# the same zero an unplugged one would report.
unplug_plan="$("$ROOT/ipod-wipe.sh" \
    --ipod "$UNPLUG_IPOD" \
    --backup "$UNPLUG_BACKUP" \
    --yes \
    --dry-run < /dev/null 2>/dev/null)"
unplug_token="$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["confirmationToken"])' "$unplug_plan")"
exit_code_is 5 wipe-unplugged-before-count.txt \
    env PATH="$UNPLUG_PATH:$BASE_PATH" \
    UNPLUG_IPOD="$UNPLUG_IPOD" \
    IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py" \
    "$ROOT/ipod-wipe.sh" \
    --ipod "$UNPLUG_IPOD" \
    --backup "$UNPLUG_BACKUP" \
    --yes \
    --confirm-token "$unplug_token"
grep -Fq 'stopped answering' "$EVIDENCE_DIR/wipe-unplugged-before-count.txt"
test ! -e "$UNPLUG_IPOD/iPod_Control"
test ! -e "$UNPLUG_BACKUP"

# The other half of that guard, and the reason it looks at the device rather
# than at the failure: a builder that fails while the iPod is still sitting
# there must not be reported as one that was unplugged.
cat > "$TEST_ROOT/failing-db-builder.py" <<'PY'
#!/usr/bin/env python3
"""A database builder that fails with the device still connected."""

import sys

sys.exit(1)
PY
mkdir -p "$VANISH_IPOD/iPod_Control/iTunes" \
    "$VANISH_IPOD/iPod_Control/Music" \
    "$VANISH_IPOD/iPod_Control/Speakable"
IPOD_DB_TOOL="$TEST_ROOT/failing-db-builder.py" \
    exit_code_is 1 exit-code-builder-failed.txt \
    "$ROOT/ipod-sync.sh" --ipod "$VANISH_IPOD" --yes "$VANISH_SOURCE"
if grep -Fq 'stopped answering' "$EVIDENCE_DIR/exit-code-builder-failed.txt"; then
    echo "a failed builder was reported as an unplugged iPod" >&2
    exit 1
fi

# Authorized rather than merely willing, because a removal that never came
# through the handshake is refused before it can find out what is installed.
# The plan is taken with the builder already missing, which is what a caller
# on a machine with no toolchain has to be able to do: what it gets back is
# the run's own answer, "install the tools", rather than a refusal that says
# nothing about them.
IPOD_DB_TOOL="$TEST_ROOT/no-such-db-builder.py" \
    exit_code_is 6 exit-code-missing-dependency.txt \
    run_approved "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" --yes 'Beach Boys/Surfin.mp3'
grep -Fq 'Database tool missing' "$EVIDENCE_DIR/exit-code-missing-dependency.txt"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"

# Both prompts, because the one that stopped an unattended run was never the
# caller's own: assert_shuffle asks its question from inside lib.sh.
exit_code_is 7 exit-code-declined.txt \
    "$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" 'Beach Boys/Surfin.mp3'
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"
NOT_A_SHUFFLE_DECLINED="$TEST_ROOT/not-a-shuffle-declined"
mkdir -p "$NOT_A_SHUFFLE_DECLINED/iPod_Control/iTunes" \
    "$NOT_A_SHUFFLE_DECLINED/iPod_Control/Music"
exit_code_is 7 exit-code-declined-speakable.txt \
    "$ROOT/ipod-wipe.sh" --ipod "$NOT_A_SHUFFLE_DECLINED"
grep -Fq 'This may not be a shuffle' "$EVIDENCE_DIR/exit-code-declined-speakable.txt"
# Bash prints a read prompt only to a terminal, so a run that declines by
# reaching end of input has to say why in its own words.
grep -Fq 'Aborted.' "$EVIDENCE_DIR/exit-code-declined-speakable.txt"

# Everything a caller cannot do anything different about stays 1, or the table
# would be a list of numbers rather than a list of decisions.
exit_code_is 1 exit-code-unknown-option.txt "$ROOT/ipod-remove.sh" --no-such-flag
exit_code_is 1 exit-code-not-on-the-ipod.txt \
    "$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" --yes '../escape.mp3'

# The one number ipod-report.py has to repeat, because a report run on its own
# still has to be able to say the device went away. Taken from the writer by
# running it rather than by reading it: once over a device it can describe, so
# the answer below is a device that went rather than a report that never
# works, and then over the same path with the volume gone, where it owes the
# caller the number the scripts produced above and nothing on stdout to read
# as a device.
REPORT_GONE_IPOD="$TEST_ROOT/report-gone-ipod"
mkdir -p "$REPORT_GONE_IPOD/iPod_Control/Music/Beach Boys" \
    "$REPORT_GONE_IPOD/iPod_Control/iTunes"
printf 'a song\n' > "$REPORT_GONE_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"
/usr/bin/python3 "$ROOT/ipod-report.py" device "$REPORT_GONE_IPOD" \
    > "$EVIDENCE_DIR/report-device-present.json"
grep -Fq '"Beach Boys/Surfin.mp3"' "$EVIDENCE_DIR/report-device-present.json"

rm -rf -- "$REPORT_GONE_IPOD"
report_gone_code=0
/usr/bin/python3 "$ROOT/ipod-report.py" device "$REPORT_GONE_IPOD" \
    > "$EVIDENCE_DIR/report-device-gone.json" \
    2> "$EVIDENCE_DIR/report-device-gone.txt" || report_gone_code=$?
test "$report_gone_code" -eq "$lib_gone_code"
test ! -s "$EVIDENCE_DIR/report-device-gone.json"
grep -Fq 'stopped answering' "$EVIDENCE_DIR/report-device-gone.txt"

# A machine with no python3 on it, which is the one dependency every report
# goes through. Run against a PATH holding everything but the interpreter, and
# under a timeout, because the way out of a missing dependency asks the device
# who it is - and asking through the interpreter that is missing is a question
# that answers itself forever rather than a code the caller can read.
#
# The report needs the writer and says so, once, with the missing-dependency
# code and nothing on stdout. The plain listing never needed it and still
# answers, because a caller cannot install an interpreter to read a folder.
NO_PYTHON_BIN="$TEST_ROOT/no-python-bin"
mkdir -p "$NO_PYTHON_BIN"
for tool in /usr/bin/* /bin/*; do
    case "${tool##*/}" in python|python3*) continue ;; esac
    ln -sf "$tool" "$NO_PYTHON_BIN/${tool##*/}" 2> /dev/null || true
done
test ! -e "$NO_PYTHON_BIN/python3"
test -x "$NO_PYTHON_BIN/find"

no_python_json_status=0
timeout 60 env PATH="$NO_PYTHON_BIN" "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" --list --json \
    > "$EVIDENCE_DIR/no-python-list.json" \
    2> "$EVIDENCE_DIR/no-python-list-json.txt" || no_python_json_status=$?
test "$no_python_json_status" -eq 6
test ! -s "$EVIDENCE_DIR/no-python-list.json"
test "$(grep -c 'python3 is required' "$EVIDENCE_DIR/no-python-list-json.txt")" -eq 1

"$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" --list \
    > "$EVIDENCE_DIR/with-python-list.txt"
no_python_list_status=0
timeout 60 env PATH="$NO_PYTHON_BIN" "$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" --list \
    > "$EVIDENCE_DIR/no-python-list.txt" \
    2> "$EVIDENCE_DIR/no-python-list-stderr.txt" || no_python_list_status=$?
test "$no_python_list_status" -eq 0
test -s "$EVIDENCE_DIR/no-python-list.txt"
diff -u "$EVIDENCE_DIR/with-python-list.txt" "$EVIDENCE_DIR/no-python-list.txt"

# --check answers what this machine can do without installing anything, which
# is the point of it: a caller has to be able to ask before deciding to.
CHECK_TOOLS="$TEST_ROOT/check-tools-dir"
CHECK_XDG="$TEST_ROOT/check-xdg-data"
check_status=0
IPOD_TOOLS_DIR="$CHECK_TOOLS" XDG_DATA_HOME="$CHECK_XDG" \
    "$ROOT/install.sh" --check \
    > "$EVIDENCE_DIR/install-check.txt" 2>&1 || check_status=$?
test ! -e "$CHECK_TOOLS"
test ! -e "$CHECK_XDG"

check_json_status=0
IPOD_TOOLS_DIR="$CHECK_TOOLS" XDG_DATA_HOME="$CHECK_XDG" \
    "$ROOT/install.sh" --check --json \
    > "$EVIDENCE_DIR/install-check.json" \
    2> "$EVIDENCE_DIR/install-check-json-stderr.txt" || check_json_status=$?
test ! -e "$CHECK_TOOLS"
test ! -e "$CHECK_XDG"
test "$check_json_status" -eq "$check_status"

# The exit code is the answer and the document is the detail, so they have to
# agree - on any machine, whatever happens to be installed on it. Nothing but
# the document may reach stdout either, or a caller parsing it would trip over
# the installer's own prose.
/usr/bin/python3 - \
    "$EVIDENCE_DIR/install-check.json" \
    "$check_json_status" \
    "$EVIDENCE_DIR/install-check.txt" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
status = int(sys.argv[2])
prose = Path(sys.argv[3]).read_text(encoding="utf-8")

assert report["schema"] == 1, report["schema"]
assert report["capabilities"], "the check reported no capabilities at all"

available = [entry["available"] for entry in report["capabilities"]]
assert report["satisfied"] == all(available), report["satisfied"]
# 0 when there is nothing to install and 6 when something is missing, which is
# the whole contract: a caller branches on the number and reads the document
# only when it wants to know which thing.
assert status == (0 if report["satisfied"] else 6), status

# What is missing, as one apt line rather than one per capability, and never
# anything a capability that is present would have needed.
expected = []
for entry in report["capabilities"]:
    if not entry["available"]:
        expected += [name for name in entry["packages"] if name not in expected]
assert report["missing_packages"] == expected, report["missing_packages"]

# The JavaScript runtime is the one capability that names no package, because
# a distribution nodejs can be older than yt-dlp accepts and installing it
# would spend a privileged transaction on a runtime the probe then rejects.
runtime = [e for e in report["capabilities"] if e["id"] == "javascript-runtime"]
assert len(runtime) == 1, report["capabilities"]
assert runtime[0]["packages"] == [], runtime[0]

# The prose form is the same reading in another shape, so it has to describe
# the same number of capabilities with the same verdicts.
assert prose.count(" ok") + prose.count(" unavailable (") == len(available), prose
assert prose.count(" unavailable (") == available.count(False), prose
PY
test ! -s "$EVIDENCE_DIR/install-check-json-stderr.txt"

# The last thing an install does is print that same report, now that the
# install has had its turn, so a person watching one finish and a caller that
# asked --check are told the same thing. Every other installer run here stops
# early on purpose and never reaches it, so this one has to finish: local
# stand-ins take the place of the network for both the database repository and
# uv, and the shipped script does the rest.
FULL_TOOLS="$TEST_ROOT/full-install-tools"
FULL_XDG="$TEST_ROOT/full-install-xdg"
UPSTREAM_STUB="$TEST_ROOT/upstream-db-tool"

mkdir -p "$UPSTREAM_STUB"
printf '%s\n' '"""Stand-in for the upstream database builder."""' \
    > "$UPSTREAM_STUB/ipod-shuffle-4g.py"
git -c init.defaultBranch=main -C "$UPSTREAM_STUB" init --quiet
git -C "$UPSTREAM_STUB" add ipod-shuffle-4g.py
git -C "$UPSTREAM_STUB" \
    -c user.name=Tests -c user.email=tests@example.invalid \
    commit --quiet -m 'Stand-in database builder'
# Cloned here rather than by the installer, which would have to reach GitHub
# for it; the update path it takes instead is the same git and the same
# working tree afterwards.
git clone --quiet "$UPSTREAM_STUB" "$FULL_TOOLS/IPod-Shuffle-4g"

FULL_BIN="$TEST_ROOT/full-install-bin"
mkdir -p "$FULL_BIN"
# The environment itself is built by the real uv, which reaches nothing over
# the network and is what the installer's own contract check has to read: an
# environment faked out of a shell script would be rebuilt on every run, which
# is the behaviour being checked here. Only the package steps are stood in
# for, those being the ones that would go to an index.
export IPOD_TEST_REAL_UV="$REAL_UV"
cat > "$FULL_BIN/uv" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail

write_yt_dlp() {
    cat > "$1" <<YTDLP
#!/bin/sh
case "\$1" in
    --version) printf '%s\n' '$2' ;;
    --help)    printf '%s\n' '  --js-runtimes RUNTIMES  Runtimes to use' ;;
esac
YTDLP
    chmod +x "$1"
}

case "${1:-}" in
    venv)
        "$IPOD_TEST_REAL_UV" "$@"
        target="${!#}"
        printf '%s\n' "$*" > "$target/uv-venv-invocation.txt"
        ;;
    pip)
        subcommand="${2:-}"
        shift 2
        python=""
        packages=()
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --python)
                    python="$2"
                    shift 2
                    ;;
                --quiet|--upgrade)
                    shift
                    ;;
                *)
                    packages+=("$1")
                    shift
                    ;;
            esac
        done
        [[ -n "$python" && ${#packages[@]} -eq 1 ]]
        environment="$(dirname "$(dirname "$python")")"
        case "$subcommand" in
            sync)
                if [[ -n "${IPOD_TEST_UV_SYNC_FAILS:-}" ]]; then
                    echo "stand-in uv: no index to reach" >&2
                    exit 1
                fi
                # A synchronization interrupted rather than failed: the
                # installer waiting on it is signalled the way Ctrl-C signals
                # it, and this leaves without having put anything back.
                if [[ -n "${IPOD_TEST_UV_SYNC_SIGNALS:-}" ]]; then
                    kill -TERM "$PPID"
                    exit 0
                fi
                printf '%s\n' "${packages[0]}" > "$environment/uv-sync-source.txt"
                printf '%s\n' 'version_string = "1.47.0-stand-in"' \
                    > "$("$python" -c \
                        'import sysconfig; print(sysconfig.get_path("purelib"))')/mutagen.py"
                write_yt_dlp "$environment/bin/yt-dlp" '2025.11.12'
                ;;
            install)
                [[ "${packages[0]}" == yt-dlp ]]
                write_yt_dlp "$environment/bin/yt-dlp" '2026.02.04'
                ;;
            *)
                exit 2
                ;;
        esac
        ;;
    *)
        exit 2
        ;;
esac
STUB
chmod +x "$FULL_BIN/uv"

# The suite's own builder and interpreter are dropped for these runs, so the
# installer works on the tools directory it was pointed at, the way it does on
# a real machine.
full_install() {
    env -u IPOD_DB_TOOL -u IPOD_VENV_PYTHON -u IPOD_VENV_YT_DLP \
        PATH="$FULL_BIN:$BASE_PATH" \
        IPOD_TOOLS_DIR="$FULL_TOOLS" XDG_DATA_HOME="$FULL_XDG" \
        "$ROOT/install.sh" "$@"
}

full_before_status=0
full_install --check \
    > "$EVIDENCE_DIR/install-complete-before.txt" 2>&1 || full_before_status=$?
test "$full_before_status" -eq 6
test ! -e "$FULL_TOOLS/venv"

full_install --no-system > "$EVIDENCE_DIR/install-complete.txt" 2>&1
grep -Fq -- "--system-site-packages $FULL_TOOLS/venv" \
    "$FULL_TOOLS/venv/uv-venv-invocation.txt"
grep -Fxq "$ROOT_REAL/requirements.txt" "$FULL_TOOLS/venv/uv-sync-source.txt"
grep -Fq 'mutagen 1.47.0-stand-in' "$EVIDENCE_DIR/install-complete.txt"
grep -Fq 'yt-dlp 2025.11.12' "$EVIDENCE_DIR/install-complete.txt"
grep -Fq 'Done.' "$EVIDENCE_DIR/install-complete.txt"
grep -Fq "graphical interface  ok ($FULL_TOOLS/venv/bin/python)" \
    "$EVIDENCE_DIR/install-complete.txt"

full_after_status=0
full_install --check \
    > "$EVIDENCE_DIR/install-complete-after.txt" 2>&1 || full_after_status=$?

/usr/bin/python3 - \
    "$EVIDENCE_DIR/install-complete-before.txt" \
    "$EVIDENCE_DIR/install-complete.txt" \
    "$EVIDENCE_DIR/install-complete-after.txt" \
    "$full_after_status" <<'PY'
import re
import sys
from pathlib import Path

CAPABILITY = re.compile(
    r"^(?:==>|warning:)   (\S.*?)  +(ok|unavailable)(?: \((.*)\))?$"
)


def capabilities(path, heading):
    """The capability lines a report printed, as (label, verdict, detail)."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", Path(path).read_text(encoding="utf-8"))
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.endswith(heading):
            lines = lines[index + 1:]
            break
    else:
        raise AssertionError(f"no {heading} section in {path}")
    found = [match.groups() for match in map(CAPABILITY.match, lines) if match]
    assert found, f"no capability lines under {heading} in {path}"
    return found


def verdict(entries, label):
    matches = [entry[1] for entry in entries if entry[0] == label]
    assert len(matches) == 1, (label, entries)
    return matches[0]


before = capabilities(sys.argv[1], "Dependencies")
verifying = capabilities(sys.argv[2], "Verifying")
after = capabilities(sys.argv[3], "Dependencies")
status = int(sys.argv[4])

# One probe pass over the nine capabilities, rendered where the install ends,
# rather than a verification section written in its own words: the same
# capabilities with the same verdicts and the same details --check reports of
# the machine the install left behind.
assert len(verifying) == 9, verifying
assert verifying == after, (verifying, after)
assert status == (0 if all(e[1] == "ok" for e in after) else 6), status

# And taken after the install rather than before it. mutagen reaches the
# virtualenv during the run, so a report assembled any earlier would still be
# calling metadata support missing here - which is exactly what the same
# command said before the run.
assert verdict(before, "metadata support") == "unavailable", before
assert verdict(verifying, "metadata support") == "ok", verifying
for installed in ("python virtualenv", "database builder"):
    assert verdict(verifying, installed) == "ok", verifying
PY

# Re-running the installer is how this project is updated and how a missing
# mutagen is repaired, so a synchronization that cannot reach an index has to
# leave the machine with what it already had. An environment cleared before
# that step would take mutagen and yt-dlp with it and leave nothing.
sync_failure_status=0
export IPOD_TEST_UV_SYNC_FAILS=1
full_install --no-system \
    > "$EVIDENCE_DIR/install-sync-failure.txt" 2>&1 || sync_failure_status=$?
unset IPOD_TEST_UV_SYNC_FAILS
test "$sync_failure_status" -ne 0
grep -Fq 'Failed to synchronize Python dependencies' \
    "$EVIDENCE_DIR/install-sync-failure.txt"
test -x "$FULL_TOOLS/venv/bin/yt-dlp"
"$FULL_TOOLS/venv/bin/python" -c \
    'import mutagen; assert mutagen.version_string == "1.47.0-stand-in"'

# The same guarantee through the rebuild, which is the harder half of it and
# the one run every machine installed before this contract has to make: the
# environment being replaced is gone from its path while the replacement is
# built, so a synchronization that cannot reach an index has to put back what
# it took rather than leaving the machine with an empty environment.
migration_venv() {
    rm -rf "$FULL_TOOLS/venv" "$FULL_TOOLS/venv.previous"
    uv venv --quiet --no-managed-python --python /usr/bin/python3 \
        "$FULL_TOOLS/venv"
    printf 'from before the contract\n' > "$FULL_TOOLS/venv/stale-environment.txt"
    # What such a machine has that it would miss: the metadata reader whose
    # absence is silent, and the downloader.
    printf '%s\n' 'version_string = "1.46.0-stand-in"' \
        > "$("$FULL_TOOLS/venv/bin/python" -c \
            'import sysconfig; print(sysconfig.get_path("purelib"))')/mutagen.py"
    printf '#!/bin/sh\nprintf "%%s\\n" 2025.01.01\n' > "$FULL_TOOLS/venv/bin/yt-dlp"
    chmod +x "$FULL_TOOLS/venv/bin/yt-dlp"
    if "$FULL_TOOLS/venv/bin/python" -c 'import gi' 2>/dev/null; then
        echo "the stand-in for an old environment could already see the distro" >&2
        exit 1
    fi
}

# Back at the path it was taken from, because the console scripts uv writes
# beside the interpreter carry that path inside them, and with everything it
# held.
assert_previous_environment_restored() {
    test -f "$FULL_TOOLS/venv/stale-environment.txt"
    test "$("$FULL_TOOLS/venv/bin/yt-dlp" --version)" = 2025.01.01
    "$FULL_TOOLS/venv/bin/python" -c \
        'import mutagen; assert mutagen.version_string == "1.46.0-stand-in"'
    test ! -e "$FULL_TOOLS/venv.previous"
}

migration_venv
migration_failure_status=0
export IPOD_TEST_UV_SYNC_FAILS=1
full_install --no-system \
    > "$EVIDENCE_DIR/install-migration-failure.txt" 2>&1 \
    || migration_failure_status=$?
unset IPOD_TEST_UV_SYNC_FAILS
test "$migration_failure_status" -ne 0
grep -Fq 'Failed to synchronize Python dependencies' \
    "$EVIDENCE_DIR/install-migration-failure.txt"
assert_previous_environment_restored
echo "PASS: a failed migration put the previous environment back"

# The same window, left by the other way out of it. Synchronizing is the only
# step that reaches an index, so it is the slow one and the one a person
# interrupts; a rebuild that stopped there rather than failing there would
# otherwise leave the same machine with nothing, and say nothing about where
# what it had went.
migration_venv
signalled_status=0
export IPOD_TEST_UV_SYNC_SIGNALS=1
full_install --no-system \
    > "$EVIDENCE_DIR/install-migration-signalled.txt" 2>&1 \
    || signalled_status=$?
unset IPOD_TEST_UV_SYNC_SIGNALS
test "$signalled_status" -eq 143
assert_previous_environment_restored
grep -Fq "Kept the environment already at $FULL_TOOLS/venv" \
    "$EVIDENCE_DIR/install-migration-signalled.txt"
echo "PASS: an interrupted migration put the previous environment back"

# An environment from before this contract is rebuilt rather than kept, since
# one without the distro's site packages cannot import the GTK bindings and
# the window would have nothing to run in. The marker goes with it, and so
# does the copy the failed run above put back.
migration_venv
full_install --no-system > "$EVIDENCE_DIR/install-migrated.txt" 2>&1
test ! -e "$FULL_TOOLS/venv/stale-environment.txt"
test ! -e "$FULL_TOOLS/venv.previous"
grep -Fq 'mutagen 1.47.0-stand-in' "$EVIDENCE_DIR/install-migrated.txt"
grep -Fq "graphical interface  ok ($FULL_TOOLS/venv/bin/python)" \
    "$EVIDENCE_DIR/install-migrated.txt"

# yt-dlp breaks whenever YouTube changes something, so --update is what the
# window and the README both point at. It updates the environment install.sh
# built, which has no pip of its own for it to reach through.
env -u IPOD_DB_TOOL -u IPOD_VENV_PYTHON -u IPOD_VENV_YT_DLP \
    PATH="$FULL_BIN:$BASE_PATH" IPOD_TOOLS_DIR="$FULL_TOOLS" \
    "$ROOT/ipod-fetch.sh" --update > "$EVIDENCE_DIR/fetch-update.txt" 2>&1
grep -Fq 'yt-dlp 2026.02.04' "$EVIDENCE_DIR/fetch-update.txt"
test "$("$FULL_TOOLS/venv/bin/yt-dlp" --version)" = 2026.02.04

exit_code_is 1 install-json-without-check.txt "$ROOT/install.sh" --json
grep -Fq -- '--json only reports' "$EVIDENCE_DIR/install-json-without-check.txt"
exit_code_is 1 remove-json-without-list.txt \
    "$ROOT/ipod-remove.sh" --ipod "$PLAYLIST_IPOD" --json 'Beach Boys/Surfin.mp3'
grep -Fq -- '--json only reports' "$EVIDENCE_DIR/remove-json-without-list.txt"

printf '%s\n' \
    "PASS: sync copied supported music while preserving source folders" \
    "PASS: playlist flags used explicit upstream values and persisted across rebuild" \
    "PASS: GUI restored playlist and voiceover choices and mapped them to CLI flags" \
    "PASS: missing playlist voiceover produced a screenless-device warning" \
    "PASS: saved directory and tag playlist options produced the voiceover warning" \
    "PASS: sync copied a symlinked library, through links and out of the tree" \
    "PASS: sync named a broken link, walked a loop once, and matched the GUI's count" \
    "PASS: sync --clear confirmed track and playlist deletion, including playlist-only devices" \
    "PASS: a dry run of each script printed its plan and left the device byte for byte" \
    "PASS: a guessed token, another plan's token and a changed plan were each refused" \
    "PASS: every device-changing script refused an iPod swapped in behind the plan" \
    "PASS: every device-changing script refused a swap at its prompt, and --yes without a plan token" \
    "PASS: --yes answered the Speakable prompt too, in all three scripts" \
    "PASS: wipe backed up music/database and preserved Speakable plus Device state" \
    "PASS: JSON mount detection retained a mount path containing spaces" \
    "PASS: raw findmnt output stayed rejected, so \\x20 escaping cannot return" \
    "PASS: GUI refused to choose between two connected iPods" \
    "PASS: unpersistable options failed loudly instead of reporting success" \
    "PASS: unmount fell back to the UDisks2 gdbus Filesystem.Unmount method" \
    "PASS: removal checked its database builder before deleting tracks" \
    "PASS: unreadable saved options stopped removal and sync before changes" \
    "PASS: removal listed the tracks and kept them when the prompt was declined" \
    "PASS: removal deleted only the named track and rebuilt with saved options" \
    "PASS: removal pruned emptied folders, which playlists would otherwise keep" \
    "PASS: removal refused paths outside the music folder and the folder itself" \
    "PASS: a single file synced into a folder named after the one it came from" \
    "PASS: a track name starting with a dash stayed a path rather than a flag" \
    "PASS: a playlist file synced its tracks and left a rewritten list at the volume root" \
    "PASS: playlist entries kept their order, decoded file URIs, and dropped streams and misses" \
    "PASS: a pls playlist reached the device as m3u, ordered by its numbered entries" \
    "PASS: PLS replacement removed stale PLS spellings for valid and empty updates" \
    "PASS: the voiceover warning followed the effective options, not the command line alone" \
    "PASS: a FAT-hostile playlist name was adjusted and the change announced" \
    "PASS: the window read back the spoken names the real builder wrote" \
    "PASS: control characters in playlist names became FAT-safe underscores" \
    "PASS: trailing FAT-rejected playlist characters became underscores" \
    "PASS: colliding sanitized playlist names kept the first list and skipped the second" \
    "PASS: case-only playlist collisions kept the first list and its casing" \
    "PASS: playlist case folding stayed stable across locale and Unicode casing" \
    "PASS: one-to-one case folding preserved expansion-distinct playlist names" \
    "PASS: long playlist names used bounded atomic temporary files" \
    "PASS: the real database builder resolved every rewritten playlist entry" \
    "PASS: colliding playlist tracks kept distinct content and destinations" \
    "PASS: replacing a playlist with no playable tracks removed its stale device list" \
    "PASS: parser failures preserved playlists and copied no partial results" \
    "PASS: failed sync and removal rewrites preserved complete playlist files" \
    "PASS: removal pruned playlists and deleted one that lost every track" \
    "PASS: wipe backed up the playlists and cleared them from the volume root" \
    "PASS: sync --clear removed the playlists with the tracks they referenced" \
    "PASS: uppercase playlist extensions were listed, pruned, backed up, and cleared" \
    "PASS: a playlist was deleted by name, keeping every song it listed" \
    "PASS: double-dot, extension-like M3U, and root PLS names deleted safely" \
    "PASS: a playlist the GUI store wrote synced in the order it was dragged into" \
    "PASS: emptying a GUI playlist removed it from the device and kept its songs" \
    "PASS: GUI playlists were made, named, edited, moved between and deleted" \
    "PASS: GUI playlist queueing staged the list itself alongside its tracks" \
    "PASS: GUI playlist rows parsed the device lists and removal mapped to the script" \
    "PASS: GUI removal and YouTube commands named the device, options and paths" \
    "PASS: fetch requested playable MP3 with tags and vfat-safe filenames" \
    "PASS: fetch copied only this run's downloads, not the whole output folder" \
    "PASS: fetch reported new tracks for the GUI, and deleted a list it could not fill" \
    "PASS: fetch handed artist folders to the sync when it could not name files" \
    "PASS: fetch passed a JavaScript runtime, without which downloads 403" \
    "PASS: runtime probe reported absence instead of naming an uninstalled one" \
    "PASS: installer reported a missing runtime instead of offering a stale nodejs" \
    "PASS: installer generated the app-grid entry pointing at this checkout" \
    "PASS: installer removed a stale launcher when GUI dependencies disappeared" \
    "PASS: installer offered the GStreamer working set only when playback was missing" \
    "PASS: desktop entry paths preserved reserved characters through Gio parsing" \
    "PASS: runtime probe rejected unsupported versions and accepted supported boundaries" \
    "PASS: old PATH yt-dlp fallback omitted its unsupported runtime option" \
    "PASS: GStreamer probe answered per interpreter and its program still parses" \
    "PASS: GUI preview player tracked state, queue, seeking and decode failures" \
    "PASS: GUI preview downloaded into the cache, played it, and kept it out of the library" \
    "PASS: the device report agreed with the window's own reading, field by field" \
    "PASS: the report's tracks matched the plain listing of the same device" \
    "PASS: an unreadable options file and an unreadable album produced no report at all" \
    "PASS: no iPod, several iPods, a vanished one, a missing dependency and a refusal each got their own code" \
    "PASS: a builder that failed with the iPod still connected stayed a plain failure" \
    "PASS: the report writer answered a vanished device with the scripts' own code" \
    "PASS: a machine without python3 got the dependency code once, and still listed" \
    "PASS: --check reported what is installed without installing, and its code matched its document" \
    "PASS: an install that finished ended with the report --check prints, taken after it" \
    "PASS: a failed dependency synchronization left the installed environment intact" \
    "PASS: an environment predating the distro-Python contract was rebuilt from it" \
    "PASS: --update upgraded yt-dlp in an environment that holds no pip" \
    "PASS: a sync reported every file, playlist and stage as JSON while it ran" \
    "PASS: names holding a quote, a backslash and a newline survived the stream" \
    "PASS: the same run without the stream printed exactly the same output" \
    "PASS: removal and wipe reported themselves on the same protocol" \
    "PASS: the count reached the total, and the total followed unplanned work" \
    "PASS: a run with no iPod still said so on the stream, with the code it exited" \
    "PASS: a run that found nothing mounted reported once, from the shell that owned the stream" \
    "PASS: a caller that stopped reading mid-copy did not take the copy with it" \
    "PASS: a descriptor nobody opened was refused instead of quietly reporting nowhere" \
    "PASS: an empty descriptor was refused by every script that takes the flag" \
    "PASS: a run refused after the stream opened still ended with a result" \
    "PASS: a unit separator in a track name reached the stream without ending it" \
    > "$EVIDENCE_DIR/product-e2e-summary.txt"

cat "$EVIDENCE_DIR/product-e2e-summary.txt"
