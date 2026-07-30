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

# The native installer uses the ordinary nodejs distribution package as its
# fallback only when no supported runtime exists. Stop it immediately after
# the dependency report so this check cannot install or download anything.
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
grep -Eq 'sudo apt install .*nodejs' \
    "$EVIDENCE_DIR/install-no-runtime.txt"

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

# Downloading has to produce something the firmware can decode, which is the
# whole point of the flags rather than an incidental detail, so the arguments
# are asserted rather than just the exit status.
FETCH_OUT="$TEST_ROOT/youtube"
FETCH_RECORD="$EVIDENCE_DIR/yt-dlp-invocation.json"
(
    # tests/bin supplies the yt-dlp and ffmpeg doubles, and the findmnt double
    # that lets --sync autodetect the fake iPod the way a real one is found.
    # Confined to this subshell.
    # shellcheck disable=SC2031
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

test -f "$FETCH_OUT/Test Artist/Test Track [testvideo].m4a"
test -s "$FETCH_OUT/.fetched"

# Artist folders are handed to the sync, not their parent. Passing $FETCH_OUT
# itself would bury every track under an extra "youtube" directory and shift
# what --dir-playlists=1 treats as the artist level.
test -f "$IPOD/iPod_Control/Music/Test Artist/Test Track [testvideo].m4a"
test ! -e "$IPOD/iPod_Control/Music/youtube"

/usr/bin/python3 - "$FETCH_RECORD" <<'PY'
import json
import sys
from pathlib import Path

args = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))


def value_of(flag):
    return args[args.index(flag) + 1]


# Opus is what YouTube serves and the one thing the shuffle cannot play, so a
# conversion to AAC at the documented bitrate is not optional.
assert value_of("--audio-format") == "m4a", args
assert value_of("--audio-quality") == "256k", args
assert "--extract-audio" in args, args

# Regression: plain "bestaudio" selects YouTube's 5.1 AAC stream, which is
# already m4a, so yt-dlp skips the conversion and --audio-quality with it. The
# device then gets a 30MB six-channel file it cannot decode.
assert "audio_channels<=2" in value_of("--format"), args
assert "/bestaudio" not in value_of("--format"), args
assert value_of("--postprocessor-args") == "ExtractAudio:-ac 2", args

# Regression: --trim-filenames limits the whole path, not the filename, so a
# long --output truncated the song title itself and collided tracks.
assert "--trim-filenames" not in args, args

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

OLD_FETCH_OUT="$TEST_ROOT/old-yt-dlp"
OLD_FETCH_RECORD="$EVIDENCE_DIR/old-yt-dlp-invocation.json"
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
    "PASS: fetch requested playable AAC with tags and vfat-safe filenames" \
    "PASS: fetch handed artist folders to the sync, not their parent" \
    "PASS: fetch passed a JavaScript runtime, without which downloads 403" \
    "PASS: runtime probe reported absence instead of naming an uninstalled one" \
    "PASS: installer offered nodejs when no supported runtime was present" \
    "PASS: runtime probe rejected unsupported versions and accepted supported boundaries" \
    "PASS: old PATH yt-dlp fallback omitted its unsupported runtime option" \
    > "$EVIDENCE_DIR/product-e2e-summary.txt"

cat "$EVIDENCE_DIR/product-e2e-summary.txt"
