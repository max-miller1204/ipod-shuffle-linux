#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

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
    # Emptying the search path is the point: it reproduces the Flatpak runtime,
    # which ships gdbus but not udisksctl, so the D-Bus fallback is exercised.
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
for command in bash dirname git mkdir python3 readlink; do
    ln -s "$(command -v "$command")" "$INSTALLER_PATH/$command"
done
INSTALL_BLOCKER="$TEST_ROOT/install-blocker"
: > "$INSTALL_BLOCKER"
if PATH="$INSTALLER_PATH" IPOD_TOOLS_DIR="$INSTALL_BLOCKER" \
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

records = [
    json.loads(line)
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
]
ipod = sys.argv[2]
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
    "PASS: GUI removal and YouTube commands named the device, options and paths" \
    "PASS: fetch requested playable MP3 with tags and vfat-safe filenames" \
    "PASS: fetch copied only this run's downloads, not the whole output folder" \
    "PASS: fetch reported new tracks for the GUI, and deleted a list it could not fill" \
    "PASS: fetch handed artist folders to the sync when it could not name files" \
    "PASS: fetch passed a JavaScript runtime, without which downloads 403" \
    "PASS: runtime probe reported absence instead of naming an uninstalled one" \
    "PASS: installer reported a missing runtime instead of offering a stale nodejs" \
    "PASS: runtime probe rejected unsupported versions and accepted supported boundaries" \
    "PASS: old PATH yt-dlp fallback omitted its unsupported runtime option" \
    > "$EVIDENCE_DIR/product-e2e-summary.txt"

cat "$EVIDENCE_DIR/product-e2e-summary.txt"
