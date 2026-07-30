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
    # shellcheck disable=SC2123
    PATH="$TEST_ROOT/no-udisks"
    gdbus() {
        printf '%s\n' "$*" > "$EVIDENCE_DIR/gdbus-call.txt"
    }
    ipod_unmount /dev/sdz
) > "$EVIDENCE_DIR/gdbus-fallback.txt"
grep -Fq \
    'org.freedesktop.UDisks2.Filesystem.Unmount {}' \
    "$EVIDENCE_DIR/gdbus-call.txt"

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
    > "$EVIDENCE_DIR/product-e2e-summary.txt"

cat "$EVIDENCE_DIR/product-e2e-summary.txt"
