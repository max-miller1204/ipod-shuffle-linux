#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_PATH="$PATH"

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

# --clear is the one destructive thing sync does, so it asks first, and with no
# terminal attached that question answers itself as no. Without --yes the only
# way to drive it from a script was to pipe a "y" into stdin.
#
# stdin is closed deliberately in both halves: a regression that reinstated the
# prompt would then abort or hang here rather than quietly reading a stray
# newline and looking like it passed.
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
printf '%s\n' '#EXTM3U' > "$YES_IPOD/Existing.m3u"

if "$ROOT/ipod-sync.sh" \
    --ipod "$YES_IPOD" \
    --clear \
    "$YES_SOURCE" < /dev/null > "$EVIDENCE_DIR/clear-without-yes.txt" 2>&1; then
    echo "--clear cleared the iPod without anyone confirming it" >&2
    exit 1
fi
grep -Fq 'Aborted.' "$EVIDENCE_DIR/clear-without-yes.txt"
test -s "$YES_IPOD/iPod_Control/Music/Existing/old.mp3"
test -f "$YES_IPOD/Existing.m3u"

"$ROOT/ipod-sync.sh" \
    --ipod "$YES_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null > "$EVIDENCE_DIR/clear-with-yes.txt" 2>&1
grep -Fq 'Delete 1 existing track(s) and 1 playlist(s) from the iPod?' \
    "$EVIDENCE_DIR/clear-with-yes.txt"
test -f "$YES_IPOD/iPod_Control/Music/yes-source/track.mp3"
test ! -e "$YES_IPOD/iPod_Control/Music/Existing/old.mp3"
test ! -e "$YES_IPOD/Existing.m3u"

PLAYLIST_ONLY_IPOD="$TEST_ROOT/playlist-only-clear-target"
mkdir -p \
    "$PLAYLIST_ONLY_IPOD/iPod_Control/iTunes" \
    "$PLAYLIST_ONLY_IPOD/iPod_Control/Music" \
    "$PLAYLIST_ONLY_IPOD/iPod_Control/Speakable"
printf '%s\n' '#EXTM3U' > "$PLAYLIST_ONLY_IPOD/Only List.m3u"
if "$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_ONLY_IPOD" \
    --clear \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/clear-playlist-only-without-yes.txt" 2>&1; then
    echo "--clear removed a playlist without confirmation" >&2
    exit 1
fi
grep -Fq 'Aborted.' "$EVIDENCE_DIR/clear-playlist-only-without-yes.txt"
test -f "$PLAYLIST_ONLY_IPOD/Only List.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_ONLY_IPOD" \
    --clear \
    --yes \
    "$YES_SOURCE" < /dev/null \
    > "$EVIDENCE_DIR/clear-playlist-only-with-yes.txt" 2>&1
grep -Fq 'Delete 1 playlist(s) from the iPod?' \
    "$EVIDENCE_DIR/clear-playlist-only-with-yes.txt"
test ! -e "$PLAYLIST_ONLY_IPOD/Only List.m3u"

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
    "$ROOT/$script" --ipod "$NOT_A_SHUFFLE" "${args[@]}" \
        < /dev/null \
        > "$EVIDENCE_DIR/no-speakable-$script.stdout.txt" \
        2> "$EVIDENCE_DIR/no-speakable-$script.stderr.txt" \
        || { echo "$script --yes stopped at the Speakable prompt" >&2; exit 1; }
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

"$ROOT/ipod-wipe.sh" \
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
for command in bash cp dirname git mkdir python3 readlink touch; do
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
    "$ROOT/ipod-remove.sh" \
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
"$ROOT/ipod-remove.sh" \
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

# Without --yes it asks first, listing what it is about to delete. Answering
# no has to leave the device exactly as it was, since this prompt is the only
# thing between a mistyped path and the one copy of a song.
declined=0
printf 'n\n' | "$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    'Mixtape/Side A/02 - Delete.mp3' > "$EVIDENCE_DIR/remove-declined.txt" 2>&1 \
    || declined=1
test "$declined" -eq 1
test -s "$IPOD/iPod_Control/Music/Mixtape/Side A/02 - Delete.mp3"
grep -Fq 'Mixtape/Side A/02 - Delete.mp3' "$EVIDENCE_DIR/remove-declined.txt"

"$ROOT/ipod-remove.sh" \
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
"$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    'Mixtape/Side B' > "$EVIDENCE_DIR/remove-folder.txt" 2>&1
test ! -e "$IPOD/iPod_Control/Music/Mixtape/Side B"
test -d "$IPOD/iPod_Control/Music/Mixtape/Side A"

# Same reason for the folder a last remaining track leaves behind, all the way
# up to the music root.
"$ROOT/ipod-remove.sh" \
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

"$ROOT/ipod-remove.sh" \
    --ipod "$IPOD" \
    --yes \
    -- 'Dashes/-1 Countdown.mp3' >> "$EVIDENCE_DIR/dash-name.txt" 2>&1
test ! -e "$IPOD/iPod_Control/Music/Dashes"

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
    /usr/bin/python3 "$REAL_DB_TOOL" --verbose "$PLAYLIST_IPOD" \
        > "$EVIDENCE_DIR/real-builder.txt" 2>&1
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
else
    printf 'skipped: real database builder not installed at %s\n' "$REAL_DB_TOOL" \
        > "$EVIDENCE_DIR/real-builder.txt"
    echo "NOTICE: real-builder playlist check skipped; run ./install.sh to enable" >&2
fi

control_playlist="$PLAYLIST_LIB/Control"$'\t'"Name"$'\177'".m3u"
printf '%s\n' "$PLAYLIST_LIB/Neil Young/Heart of Gold.mp3" > "$control_playlist"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$control_playlist" > "$EVIDENCE_DIR/playlist-control-name.txt" 2>&1
test -f "$PLAYLIST_IPOD/Control_Name_.m3u"
grep -Fq 'characters FAT rejects' "$EVIDENCE_DIR/playlist-control-name.txt"

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
test -z "$(find "$PLAYLIST_IPOD" -maxdepth 1 -name 'Atomic.m3u.tmp.*' -print -quit)"

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
if env PATH="$FAILING_MV_PATH:$BASE_PATH" \
    FAIL_MOVE_TARGET="$ATOMIC_REMOVE_IPOD/Atomic Remove.m3u" \
    REAL_MV="$REAL_MV" \
    "$ROOT/ipod-remove.sh" \
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
test -z "$(find "$ATOMIC_REMOVE_IPOD" -maxdepth 1 -name 'Atomic Remove.m3u.tmp.*' -print -quit)"

# A removed track leaves every list that names it, and a list that loses its
# last track disappears rather than survive as a playlist that plays nothing.
"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    'Neil Young/Harvest Moon.mp3' > "$EVIDENCE_DIR/playlist-prune.txt" 2>&1
diff -u <(printf '%s\n' \
    '#EXTM3U' \
    'iPod_Control/Music/Beach Boys/Surfin.mp3' \
    'iPod_Control/Music/Neil Young/Heart of Gold.mp3') \
    "$PLAYLIST_IPOD/Summer Mix.m3u"
grep -Fq "Playlist 'Summer Mix': dropped 1 removed track(s)" \
    "$EVIDENCE_DIR/playlist-prune.txt"

"$ROOT/ipod-remove.sh" \
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
"$ROOT/ipod-wipe.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --backup "$TEST_ROOT/playlist-backup" \
    --yes > "$EVIDENCE_DIR/playlist-wipe.txt" 2>&1
test -s "$TEST_ROOT/playlist-backup/Playlists/Summer Mix.m3u"
test -s "$TEST_ROOT/playlist-backup/Playlists/Party.m3u"
test -z "$(find "$PLAYLIST_IPOD" -maxdepth 1 -name '*.m3u' -print -quit)"

# --clear starts the device over, so the playlists referencing the deleted
# tracks go too.
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Summer Mix.m3u" > "$EVIDENCE_DIR/playlist-clear-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/Summer Mix.m3u"
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --clear \
    --yes \
    "$PLAYLIST_LIB/Beach Boys" < /dev/null \
    > "$EVIDENCE_DIR/playlist-clear.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Summer Mix.m3u"
grep -Fq 'Removed 1 playlist(s)' "$EVIDENCE_DIR/playlist-clear.txt"

# A playlist can be deleted by name, which removes only the list itself. The
# GUI's per-playlist trash button drives exactly this.
"$ROOT/ipod-sync.sh" \
    --ipod "$PLAYLIST_IPOD" \
    "$PLAYLIST_LIB/Summer Mix.m3u" > "$EVIDENCE_DIR/playlist-byname-setup.txt" 2>&1
test -f "$PLAYLIST_IPOD/Summer Mix.m3u"
printf '%s\n' 'File1=iPod_Control/Music/Beach Boys/Surfin.mp3' \
    > "$PLAYLIST_IPOD/Legacy.pls"

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
"$ROOT/ipod-remove.sh" \
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
"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'mix.m3u' > "$EVIDENCE_DIR/playlist-extension-stem-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/mix.m3u.m3u"

"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Legacy' > "$EVIDENCE_DIR/playlist-pls-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Legacy.pls"

printf '%s\n' '#EXTM3U' > "$PLAYLIST_IPOD/Priority.m3u"
printf '%s\n' '[playlist]' > "$PLAYLIST_IPOD/Priority.pls"
"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Priority.m3u' > "$EVIDENCE_DIR/playlist-format-priority.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Priority.m3u"
test -f "$PLAYLIST_IPOD/Priority.pls"
"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Priority.pls' >> "$EVIDENCE_DIR/playlist-format-priority.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Priority.pls"

"$ROOT/ipod-remove.sh" \
    --ipod "$PLAYLIST_IPOD" \
    --yes \
    --playlist 'Summer Mix' > "$EVIDENCE_DIR/playlist-byname-delete.txt" 2>&1
test ! -e "$PLAYLIST_IPOD/Summer Mix.m3u"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Beach Boys/Surfin.mp3"
test -s "$PLAYLIST_IPOD/iPod_Control/Music/Neil Young/Heart of Gold.mp3"
grep -Fq 'the songs they listed stay' "$EVIDENCE_DIR/playlist-byname-delete.txt"
grep -Fq 'Rebuilding iTunesSD database' "$EVIDENCE_DIR/playlist-byname-delete.txt"

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

printf '%s\n' \
    "PASS: sync copied supported music while preserving source folders" \
    "PASS: playlist flags used explicit upstream values and persisted across rebuild" \
    "PASS: GUI restored playlist and voiceover choices and mapped them to CLI flags" \
    "PASS: missing playlist voiceover produced a screenless-device warning" \
    "PASS: saved directory and tag playlist options produced the voiceover warning" \
    "PASS: sync --clear confirmed track and playlist deletion, including playlist-only devices" \
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
    "PASS: control characters in playlist names became FAT-safe underscores" \
    "PASS: colliding sanitized playlist names kept the first list and skipped the second" \
    "PASS: the real database builder resolved every rewritten playlist entry" \
    "PASS: colliding playlist tracks kept distinct content and destinations" \
    "PASS: replacing a playlist with no playable tracks removed its stale device list" \
    "PASS: parser failures preserved playlists and copied no partial results" \
    "PASS: failed sync and removal rewrites preserved complete playlist files" \
    "PASS: removal pruned playlists and deleted one that lost every track" \
    "PASS: wipe backed up the playlists and cleared them from the volume root" \
    "PASS: sync --clear removed the playlists with the tracks they referenced" \
    "PASS: a playlist was deleted by name, keeping every song it listed" \
    "PASS: double-dot, extension-like M3U, and root PLS names deleted safely" \
    "PASS: GUI add-playlist named the device, options and file, and switched spoken names on" \
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
    "PASS: runtime probe rejected unsupported versions and accepted supported boundaries" \
    "PASS: old PATH yt-dlp fallback omitted its unsupported runtime option" \
    > "$EVIDENCE_DIR/product-e2e-summary.txt"

cat "$EVIDENCE_DIR/product-e2e-summary.txt"
