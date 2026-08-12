#!/usr/bin/env bash
# End-user walkthrough of "Rails for destructive operations run
# non-interactively".
#
# Builds a device the way a person builds one (ipod-sync.sh), then drives the
# three destructive scripts the way an automated caller has to drive them now:
# ask for the plan, read it, return its token against the device it named.
# Every command, its output and the code it left with is printed, and the
# volume itself is checksummed either side of the runs that were refused, so
# "changed nothing" is read off the files rather than off a sentence.
#
# The last scene is a person at a terminal, which is the case none of this may
# get in the way of: the same --clear, answered by hand, with no token at all.
set -uo pipefail

ROOT="$1"
EV="$2"
WORK="$(mktemp -d)"
trap 'chmod -R u+rwX "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

export IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py"
export IPOD_VENV_PYTHON="/usr/bin/python3"
export FAKE_DB_RECORD="$WORK/db-invocations.jsonl"

IPOD="$WORK/iPod"
LIB="$WORK/Music"
TABLE="$WORK/table.txt"
: > "$TABLE"
CASE=""

hr() { printf '\n%s\n' "======================================================================"; }
scene() { hr; printf '%s\n' "$*"; hr; }
case_label() { CASE="$2"; printf '\n# %s - %s\n' "$1" "$2"; }
record() { printf '%s\t%s\n' "$1" "$2" >> "$TABLE"; }

run() {
    local status=0
    printf '\n$ %s\n' "$*"
    "$@" < /dev/null 2>&1 || status=$?
    printf '[exit %d]\n' "$status"
    LAST=$status
    if [[ -n "$CASE" ]]; then
        record "$status" "$CASE"
        CASE=""
    fi
    return 0
}

# What is on the volume, by name and by content, so a refusal that deleted
# something anyway cannot read as a refusal.
state() {
    (cd "$IPOD" && find . -mindepth 1 -type f -exec md5sum {} + | sort)
}

tracks() {
    (cd "$IPOD/iPod_Control/Music" 2>/dev/null && find . -type f | sed 's|^\./||' | sort)
}

# One value out of a plan a caller has just read, named the way the document
# spells it. Exactly what the caller in docs/machine-interface.md does with
# jq, in the interpreter this machine is sure to have.
field() {
    /usr/bin/python3 -c '
import json
import sys

plan = json.loads(sys.argv[1])
for key in sys.argv[2].split("."):
    plan = plan[key]
print(plan)
' "$1" "$2"
}

mkdir -p \
    "$IPOD/iPod_Control/iTunes" \
    "$IPOD/iPod_Control/Speakable/System" \
    "$IPOD/iPod_Control/Device" \
    "$LIB/Kite Season" \
    "$LIB/Harbour Tapes"
printf 'spoken battery prompt\n' > "$IPOD/iPod_Control/Speakable/System/battery.wav"
printf 'the iPod on the desk\n' > "$IPOD/iPod_Control/Device/SysInfo"
printf 'a song\n' > "$LIB/Kite Season/01 - Harbour Light.mp3"
printf 'another song\n' > "$LIB/Kite Season/02 - Slow Ferry.mp3"
printf 'a third song\n' > "$LIB/Harbour Tapes/01 - Pier Lights.mp3"

scene "SETUP: a person fills the iPod, which needs no handshake at all
       (copying music deletes nothing, so nothing here is destructive)"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" "$LIB/Kite Season"
printf '\nOn the device now:\n'; tracks

scene "1. THE OLD WAY OUT: a script clears the device with --yes and no
       terminal behind it. --yes records a person's answer; a caller that
       copied the flag never had one to record."
case_label 7 "non-interactive --clear with --yes and no token"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes "$LIB/Harbour Tapes"
printf '\nStill on the device:\n'; tracks

scene "2. THE PLAN: the same command with --dry-run. Everything a person
       would have been told goes to stderr; stdout is one JSON document."
printf '\n$ %s\n' "$ROOT/ipod-sync.sh --ipod \$IPOD --clear --yes --dry-run \$LIB/Harbour\ Tapes  # stdout"
state > "$WORK/before-plan.txt"
PLAN="$("$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes --dry-run \
    "$LIB/Harbour Tapes" < /dev/null 2> "$WORK/plan.stderr")"
printf '%s\n' "$PLAN" | /usr/bin/python3 -m json.tool
printf '%s\n' "$PLAN" > "$EV/02-sync-plan.json"
printf '\n  # the same run, stderr:\n'
sed 's/^/  /' "$WORK/plan.stderr"
state > "$WORK/after-plan.txt"
printf '\n$ diff <(md5sum every file, before the plan) <(the same, after it)\n'
if diff -u "$WORK/before-plan.txt" "$WORK/after-plan.txt"; then
    printf 'the plan wrote nothing: %s file(s), byte for byte identical\n' \
        "$(wc -l < "$WORK/before-plan.txt")"
fi

TOKEN="$(field "$PLAN" confirmationToken)"
IDENTITY="$(field "$PLAN" device.identity)"

scene "3. THE WAYS ROUND IT, AND WHAT EACH ONE GETS"

case_label 7 "a token the caller made up"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes \
    --confirm-token 0000000000000000000000000000000000000000000000000000000000000000 \
    "$LIB/Harbour Tapes"

case_label 7 "this plan's token, on a run that syncs a different folder"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes \
    --confirm-token "$TOKEN" "$LIB/Kite Season"

case_label 7 "this plan's token, on a run that also ejects the device"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes --eject \
    --confirm-token "$TOKEN" "$LIB/Harbour Tapes"

case_label 1 "the iPod that was planned against has been swapped for another"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes \
    --expect-device 'sysinfo:the-other-ipod' \
    --confirm-token "$TOKEN" "$LIB/Harbour Tapes"

printf '\nAfter four refusals, still on the device:\n'; tracks
state > "$WORK/after-refusals.txt"
if diff -q "$WORK/before-plan.txt" "$WORK/after-refusals.txt" > /dev/null; then
    printf 'and byte for byte what it held before the first one\n'
fi

scene "4. THE PLAN, RETURNED AS IT WAS PRINTED, AGAINST THE DEVICE IT NAMED"
case_label 0 "--expect-device and --confirm-token from the plan above"
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --clear --yes \
    --expect-device "$IDENTITY" --confirm-token "$TOKEN" "$LIB/Harbour Tapes"
printf '\nOn the device now:\n'; tracks

scene "5. THE SAME HANDSHAKE FOR A REMOVAL, AND THE PATHS IT STILL REFUSES"
case_label 1 "a track path that climbs out of the music folder"
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes '../../../etc/passwd'

printf '\n$ %s\n' "$ROOT/ipod-remove.sh --ipod \$IPOD --yes --dry-run 'Harbour Tapes/01 - Pier Lights.mp3'"
REMOVE_PLAN="$("$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes --dry-run \
    'Harbour Tapes/01 - Pier Lights.mp3' < /dev/null 2>/dev/null)"
printf '%s\n' "$REMOVE_PLAN" | /usr/bin/python3 -m json.tool
printf '%s\n' "$REMOVE_PLAN" > "$EV/03-remove-plan.json"
REMOVE_TOKEN="$(field "$REMOVE_PLAN" confirmationToken)"
case_label 0 "the removal that plan describes"
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes \
    --expect-device "$IDENTITY" --confirm-token "$REMOVE_TOKEN" \
    'Harbour Tapes/01 - Pier Lights.mp3'
printf '\nOn the device now:\n'; tracks

scene "6. AND FOR A WIPE, WHICH KEEPS THE SPOKEN PROMPTS THE FIRMWARE NEEDS"
printf '\n$ %s\n' "$ROOT/ipod-wipe.sh --ipod \$IPOD --backup \$WORK/backup --yes --dry-run"
WIPE_PLAN="$("$ROOT/ipod-wipe.sh" --ipod "$IPOD" --backup "$WORK/backup" --yes \
    --dry-run < /dev/null 2>/dev/null)"
printf '%s\n' "$WIPE_PLAN" | /usr/bin/python3 -m json.tool
printf '%s\n' "$WIPE_PLAN" > "$EV/04-wipe-plan.json"
WIPE_TOKEN="$(field "$WIPE_PLAN" confirmationToken)"
case_label 0 "the wipe that plan describes"
run "$ROOT/ipod-wipe.sh" --ipod "$IPOD" --backup "$WORK/backup" --yes \
    --expect-device "$IDENTITY" --confirm-token "$WIPE_TOKEN"
printf '\nMusic on the device now: %s file(s)\n' "$(tracks | grep -c . || true)"
printf 'Spoken prompts preserved: %s\n' \
    "$(find "$IPOD/iPod_Control/Speakable" -type f | sed "s|$IPOD/||")"
printf 'Backed up under %s:\n' 'backup/'
(cd "$WORK/backup" && find . -type f | sed 's|^\./|  |' | sort)

scene "7. A PERSON AT A TERMINAL, WHICH IS WHAT NONE OF THIS MAY GET IN THE
       WAY OF: the same --clear, no --yes, no token, answered by hand.
       The device is filled again first, so there is something to delete."
run "$ROOT/ipod-sync.sh" --ipod "$IPOD" "$LIB/Kite Season"
printf '\n$ %s\n' "$ROOT/ipod-sync.sh --ipod \$IPOD --clear \$LIB/Harbour\ Tapes   # at a terminal, typing y"
# script(1) gives the run a pseudo-terminal, which is the whole difference
# between this scene and scene 1: the question below is one there is somebody
# to ask, so it is asked rather than refused.
interactive=0
printf 'y\n' | script -qec \
    "'$ROOT/ipod-sync.sh' --ipod '$IPOD' --clear '$LIB/Harbour Tapes'" \
    "$WORK/interactive.log" > /dev/null || interactive=$?
sed -e 's/\r$//' -e '/^Script started on/d' "$WORK/interactive.log"
printf '[exit %d]\n' "$interactive"
record "$interactive" "interactive --clear, answered y at the prompt"
printf '\nOn the device now:\n'; tracks

scene "8. EVERY STATE ABOVE, AND THE CODE IT LEFT WITH"
printf '%-6s %s\n' 'code' 'state'
printf '%-6s %s\n' '----' '-----'
while IFS=$'\t' read -r code label; do
    printf '%-6s %s\n' "$code" "$label"
done < "$TABLE"
cp "$TABLE" "$EV/05-exit-codes.tsv"

printf '\n(end of walkthrough)\n'
