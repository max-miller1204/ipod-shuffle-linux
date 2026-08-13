#!/usr/bin/env bash
# Drive the product scripts the way an automated caller does and record what a
# person sees: human output, exit codes, the device afterwards, the database
# builder's record, and the NDJSON progress stream.
#
# Usage: cli-token-evidence.sh REPO_ROOT
set -u

ROOT="$(cd "$1" && pwd)"
WORK="$(mktemp -d /tmp/cli-token-evidence-XXXXXX)"
export HOME="$WORK/home"
export XDG_CONFIG_HOME="$HOME/config"
export XDG_CACHE_HOME="$HOME/cache"
export IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py"
export IPOD_VENV_PYTHON=/usr/bin/python3
export FAKE_DB_RECORD="$WORK/database-invocations.jsonl"
mkdir -p "$HOME/Music"

SOURCE="$WORK/New Album"
mkdir -p "$SOURCE"
printf 'new song\n' > "$SOURCE/01 - New.mp3"

make_mount() {
    local mount="$WORK/$1"
    mkdir -p "$mount/iPod_Control/iTunes" "$mount/iPod_Control/Music/Album" \
        "$mount/iPod_Control/Device" "$mount/iPod_Control/Speakable/System"
    printf 'identity of %s\n' "$1" > "$mount/iPod_Control/Device/SysInfo"
    printf 'keep me\n' > "$mount/iPod_Control/Music/Album/01 - Keep.mp3"
    printf 'prompt\n' > "$mount/iPod_Control/Speakable/System/battery.wav"
    printf '%s' "$mount"
}

plain() { sed 's/\x1b\[[0-9;]*m//g'; }

report() {  # report MOUNT
    printf '    [tracks on device] %s\n' \
        "$(cd "$1/iPod_Control/Music" && find . -name '*.mp3' | sort | tr '\n' ' ')"
    printf '    [database builder invocations] %s\n\n' \
        "$( [ -f "$FAKE_DB_RECORD" ] && wc -l < "$FAKE_DB_RECORD" || echo 0)"
}

echo "=== product scripts: token, exit codes, progress, human output ==="
echo "repo under test: $ROOT"
echo

MOUNT="$(make_mount token)"
echo "--- 1. dry run: the plan, on stdout, changing nothing ---"
PLAN="$("$ROOT/ipod-sync.sh" --ipod "$MOUNT" --dry-run -- "$SOURCE" < /dev/null)"
echo "\$ ipod-sync.sh --ipod MOUNT --dry-run -- 'New Album'   (exit $?)"
echo "$PLAN"
IDENTITY="$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["device"]["identity"])')"
TOKEN="$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["confirmationToken"])')"
report "$MOUNT"

echo "--- 2. a token that is not this plan's: refused with exit 7 ---"
status=0
"$ROOT/ipod-sync.sh" --ipod "$MOUNT" --yes --expect-device "$IDENTITY" \
    --confirm-token deadbeef -- "$SOURCE" < /dev/null 2>&1 | plain || true
status=${PIPESTATUS[0]}
echo "\$ ipod-sync.sh --ipod MOUNT --yes --expect-device ID --confirm-token deadbeef -- 'New Album'"
echo "  exit $status"
report "$MOUNT"

echo "--- 3. a device that is not the planned one: refused with exit 1 ---"
"$ROOT/ipod-sync.sh" --ipod "$MOUNT" --yes --expect-device "sysinfo:another-ipod" \
    --confirm-token "$TOKEN" -- "$SOURCE" < /dev/null 2>&1 | plain || true
echo "  exit ${PIPESTATUS[0]}"
report "$MOUNT"

echo "--- 4. this plan's own token: the sync runs ---"
"$ROOT/ipod-sync.sh" --ipod "$MOUNT" --yes --expect-device "$IDENTITY" \
    --confirm-token "$TOKEN" -- "$SOURCE" < /dev/null 2>&1 | plain || true
echo "  exit ${PIPESTATUS[0]}"
report "$MOUNT"

echo "--- 5. an ordinary non-destructive sync with no token at all still runs ---"
PLAIN_MOUNT="$(make_mount ordinary)"
"$ROOT/ipod-sync.sh" --ipod "$PLAIN_MOUNT" -- "$SOURCE" < /dev/null 2>&1 | plain || true
echo "  exit ${PIPESTATUS[0]}  (no --confirm-token, stdin not a terminal)"
report "$PLAIN_MOUNT"

echo "--- 6. a destructive removal with no token: refused with exit 7 ---"
"$ROOT/ipod-remove.sh" --ipod "$PLAIN_MOUNT" --yes -- "Album/01 - Keep.mp3" < /dev/null 2>&1 | plain || true
echo "  exit ${PIPESTATUS[0]}"
report "$PLAIN_MOUNT"

echo "--- 7. structured progress on the descriptor the caller opened ---"
PROGRESS_MOUNT="$(make_mount progress)"
PROGRESS_PLAN="$("$ROOT/ipod-sync.sh" --ipod "$PROGRESS_MOUNT" --dry-run -- "$SOURCE" < /dev/null)"
PROGRESS_TOKEN="$(printf '%s' "$PROGRESS_PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["confirmationToken"])')"
PROGRESS_IDENTITY="$(printf '%s' "$PROGRESS_PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["device"]["identity"])')"
progress_status=0
"$ROOT/ipod-sync.sh" --ipod "$PROGRESS_MOUNT" --progress-json --yes \
    --expect-device "$PROGRESS_IDENTITY" --confirm-token "$PROGRESS_TOKEN" -- "$SOURCE" \
    < /dev/null 3> "$WORK/events.ndjson" > "$WORK/human.txt" 2>&1 || progress_status=$?
echo "  exit $progress_status; human output on stdout:"
plain < "$WORK/human.txt" | sed 's/^/    /'
echo "  NDJSON on descriptor 3:"
sed 's/^/    /' "$WORK/events.ndjson"
report "$PROGRESS_MOUNT"

echo "--- 8. descriptor 3 closed: the run refuses rather than reporting nowhere ---"
closed_status=0
"$ROOT/ipod-sync.sh" --ipod "$PROGRESS_MOUNT" --progress-json --rebuild-only \
    < /dev/null > "$WORK/closed.txt" 2>&1 3>&- || closed_status=$?
echo "\$ ipod-sync.sh --ipod MOUNT --progress-json --rebuild-only  3>&-"
echo "  exit $closed_status"
plain < "$WORK/closed.txt" | sed 's/^/    /'
echo

echo "--- 9. the same command with descriptor 3 inherited open: it proceeds ---"
echo "    (this is why the end-to-end case closes fd 3 explicitly: without 3>&-"
echo "     a harness that leaks a descriptor turns the check above into a sync)"
inherited_status=0
"$ROOT/ipod-sync.sh" --ipod "$PROGRESS_MOUNT" --progress-json --rebuild-only \
    < /dev/null > "$WORK/inherited.txt" 2>&1 3> "$WORK/inherited.ndjson" || inherited_status=$?
echo "\$ ipod-sync.sh --ipod MOUNT --progress-json --rebuild-only  3>inherited.ndjson"
echo "  exit $inherited_status"
plain < "$WORK/inherited.txt" | sed 's/^/    /'
echo "  NDJSON written: $(wc -l < "$WORK/inherited.ndjson") line(s), last event:"
tail -1 "$WORK/inherited.ndjson" | sed 's/^/    /'
echo

rm -rf "$WORK"
