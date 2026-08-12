#!/usr/bin/env bash
# The same two commands, on the same synthetic iPod, run against the commit
# this branch starts from and then against this checkout.
#
# What the goal is about is a difference in behaviour rather than in options,
# so it is shown as one: before, a --clear carrying --yes and nothing else
# deleted the device's music with nobody there to have agreed to it; now it
# stops, and the caller has to read a plan first.
set -uo pipefail

ROOT="$1"
BASE="$2"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git -C "$ROOT" archive "$BASE" | tar -x -C "$WORK"
BEFORE="$WORK"

export IPOD_VENV_PYTHON="/usr/bin/python3"
export FAKE_DB_RECORD="$WORK/db-invocations.jsonl"

device() {
    local ipod="$1"
    rm -rf "$ipod"
    mkdir -p "$ipod/iPod_Control/iTunes" \
        "$ipod/iPod_Control/Music/Kite Season" \
        "$ipod/iPod_Control/Speakable/System" \
        "$ipod/iPod_Control/Device"
    printf 'spoken battery prompt\n' \
        > "$ipod/iPod_Control/Speakable/System/battery.wav"
    printf 'the iPod on the desk\n' > "$ipod/iPod_Control/Device/SysInfo"
    printf 'a song\n' > "$ipod/iPod_Control/Music/Kite Season/01 - Harbour Light.mp3"
    printf 'another song\n' > "$ipod/iPod_Control/Music/Kite Season/02 - Slow Ferry.mp3"
}

mkdir -p "$WORK/Music/Harbour Tapes"
printf 'a third song\n' > "$WORK/Music/Harbour Tapes/01 - Pier Lights.mp3"

show() {
    local label="$1" tree="$2" ipod="$3" status=0
    shift 3
    printf '\n--- %s\n' "$label"
    printf '$ %s\n' "${*/#$tree\//./}"
    IPOD_DB_TOOL="$tree/tests/fake-db-builder.py" "$@" < /dev/null 2>&1 || status=$?
    printf '[exit %d]\n' "$status"
    printf 'music on the device afterwards:\n'
    (cd "$ipod/iPod_Control/Music" && find . -type f | sed 's|^\./|  |' | sort)
}

printf '======================================================================\n'
printf 'BEFORE (%s): a clear with --yes, from a script with no terminal\n' \
    "$(git -C "$ROOT" rev-parse --short "$BASE")"
printf '======================================================================\n'
device "$WORK/iPod-before"
show 'the run' "$BEFORE" "$WORK/iPod-before" \
    "$BEFORE/ipod-sync.sh" --ipod "$WORK/iPod-before" --clear --yes \
    "$WORK/Music/Harbour Tapes"
show 'and there was no plan to ask for' "$BEFORE" "$WORK/iPod-before" \
    "$BEFORE/ipod-sync.sh" --ipod "$WORK/iPod-before" --clear --yes --dry-run \
    "$WORK/Music/Harbour Tapes"

printf '\n======================================================================\n'
printf 'NOW (%s): the same command, on the same device\n' \
    "$(git -C "$ROOT" rev-parse --short HEAD)"
printf '======================================================================\n'
device "$WORK/iPod-now"
show 'the run' "$ROOT" "$WORK/iPod-now" \
    "$ROOT/ipod-sync.sh" --ipod "$WORK/iPod-now" --clear --yes \
    "$WORK/Music/Harbour Tapes"
show 'and the plan it asks the caller to read' "$ROOT" "$WORK/iPod-now" \
    "$ROOT/ipod-sync.sh" --ipod "$WORK/iPod-now" --clear --yes --dry-run \
    "$WORK/Music/Harbour Tapes"
