#!/usr/bin/env bash
# End-user walkthrough of "JSON output and stable exit codes for the shell
# scripts". Builds a device the way a user builds one (ipod-sync.sh), then
# drives only the surfaces this change added, printing every command, its
# output and the code it left with.
set -uo pipefail

ROOT="$1"
EV="$2"
WORK="$(mktemp -d)"
cp "$(dirname "$0")/driver-consume.py" "$WORK/consume.py"
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

# Names the state the next command is being run in, so the codes can be
# collected into a table at the end rather than being read out of the log.
case_label() { CASE="$2"; printf '\n# %s - %s\n' "$1" "$2"; }
record() { printf '%s\t%s\n' "$1" "$2" >> "$TABLE"; }

# Prints the command, runs it, prints what it wrote and the code it left with.
run() {
    local status=0
    printf '\n$ %s\n' "$*"
    "$@" 2>&1 || status=$?
    printf '[exit %d]\n' "$status"
    LAST=$status
    if [[ -n "$CASE" ]]; then
        record "$status" "$CASE"
        CASE=""
    fi
    return 0
}

scene "SETUP: a real sync onto a fake iPod, so there is something to report on"
mkdir -p "$IPOD/iPod_Control/iTunes" "$IPOD/iPod_Control/Music" \
    "$IPOD/iPod_Control/Speakable" "$LIB/Beach Boys" "$LIB/Neil Young"
printf 'surf\n'    > "$LIB/Beach Boys/Surfin'.mp3"
printf 'harvest\n' > "$LIB/Neil Young/Harvest Moon.mp3"
printf 'gold\n'    > "$LIB/Neil Young/Heart of Gold.mp3"
{
    printf '#EXTM3U\r\n'
    printf "Beach Boys/Surfin'.mp3\r\n"
    printf '%s\r\n' "$LIB/Neil Young/Harvest Moon.mp3"
} > "$LIB/Summer Mix.m3u"
printf '%s\n' "$LIB/Neil Young/Heart of Gold.mp3" > "$LIB/Quiet \"one\".m3u"

run "$ROOT/ipod-sync.sh" --ipod "$IPOD" --playlist-voiceover \
    "$LIB/Summer Mix.m3u" "$LIB/Quiet \"one\".m3u"

# The upstream builder writes a recording per playlist it could announce, named
# after the digest the firmware files it under. Only one is written here, so the
# report has both answers to give.
/usr/bin/python3 - "$IPOD" 'Summer Mix' <<'PY'
import hashlib
import sys
from pathlib import Path

name = sys.argv[2]
digest = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).digest()[:8]
stem = "".join(f"{b:02x}" for b in reversed(digest))
folder = Path(sys.argv[1], "iPod_Control", "Speakable", "Playlists")
folder.mkdir(parents=True, exist_ok=True)
(folder / f"{stem}.wav").write_bytes(b"RIFF....WAVE spoken name")
print(f"wrote the spoken recording for {name!r} as {stem}.wav")
PY

scene "1. THE READ-ONLY SURFACE: what --list said before, and what --json says now"
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" --list
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json
"$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json > "$EV/02-device-report.json"

scene "2. A CALLER USING IT: no prose parsed, no 'iPod now holds 42 track(s)'"
run /usr/bin/python3 "$WORK/consume.py" "$EV/02-device-report.json"

scene "3. install.sh --check: what is installed, without installing anything"
# Asked the way a caller asks it, with none of the overrides the device above
# was built with: this is what the machine itself can do.
unset IPOD_DB_TOOL IPOD_VENV_PYTHON
export IPOD_TOOLS_DIR="$WORK/tools" XDG_DATA_HOME="$WORK/xdg"
run "$ROOT/install.sh" --check
check_status=$LAST
run "$ROOT/install.sh" --check --json
json_status=$LAST
"$ROOT/install.sh" --check --json > "$EV/03-install-check.json" 2>/dev/null
printf '\nthe prose and the document left with the same code: %s and %s\n' \
    "$check_status" "$json_status"
record "$json_status" "install.sh --check with a capability missing"
printf 'nothing was installed and nothing was written:\n'
run test ! -e "$WORK/tools"
run test ! -e "$WORK/xdg"

# What is missing, as the apt names that would provide it: the same table the
# installer itself acts on, so a caller can ask before deciding to install.
printf '\n# the same probe on a machine with no ffmpeg and no speech engine\n'
NOFF="$WORK/no-ffmpeg-bin"
mkdir -p "$NOFF"
for tool in /usr/bin/* /bin/*; do
    case "${tool##*/}" in ffmpeg|ffprobe|espeak*|pico2wave) continue ;; esac
    ln -sf "$tool" "$NOFF/${tool##*/}" 2>/dev/null || true
done
cat > "$WORK/read-check.py" <<'PY'
#!/usr/bin/env python3
"""What a caller asking "can this machine do it, and what is missing?" does."""

import json
import subprocess
import sys

done = subprocess.run(
    [sys.argv[1], "--check", "--json"], capture_output=True, text=True
)
report = json.loads(done.stdout)
print("exit", done.returncode, "- satisfied:", report["satisfied"])
for entry in report["capabilities"]:
    if not entry["available"]:
        print("  missing:", entry["label"].ljust(20), entry["detail"])
print("  apt install", " ".join(report["missing_packages"]) or "(nothing apt provides)")
PY
run env PATH="$NOFF" IPOD_TOOLS_DIR="$WORK/tools" XDG_DATA_HOME="$WORK/xdg" \
    /usr/bin/python3 "$WORK/read-check.py" "$ROOT/install.sh"
unset IPOD_TOOLS_DIR XDG_DATA_HOME
export IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py"
export IPOD_VENV_PYTHON="/usr/bin/python3"

scene "4. THE FIVE STATES, AS CODES A CALLER CAN BRANCH ON"

case_label 3 "no iPod: nothing plugged in"
run bash -c '
    source "$1"; list_vfat_mounts() { :; }; find_ipod ""' _ "$ROOT/lib.sh"

case_label 3 "no iPod: a path named explicitly with nothing at it"
run "$ROOT/ipod-remove.sh" --ipod "$WORK/nothing-mounted-here" --list

case_label 4 "several iPods, none of them named"
mkdir -p "$WORK/two/one/iPod_Control" "$WORK/two/two/iPod_Control"
run bash -c '
    source "$1"; root="$2"
    list_vfat_mounts() { printf "%s\n" "$root/one" "$root/two"; }
    find_ipod ""' _ "$ROOT/lib.sh" "$WORK/two"

# Unplugged during the database write, which is the last thing every sync does.
case_label 5 "the device stopped answering mid-operation"
cat > "$WORK/vanishing-builder.py" <<'PY'
#!/usr/bin/env python3
"""Stands in for an iPod unplugged while the database is being written."""
import shutil
import sys
shutil.rmtree(sys.argv[-1])
sys.exit(1)
PY
VANISH="$WORK/vanishing-ipod"
mkdir -p "$VANISH/iPod_Control/iTunes" "$VANISH/iPod_Control/Music" \
    "$VANISH/iPod_Control/Speakable"
run env IPOD_DB_TOOL="$WORK/vanishing-builder.py" \
    "$ROOT/ipod-sync.sh" --ipod "$VANISH" --yes "$LIB/Neil Young"

case_label 1 "a builder that failed with the iPod still sitting there"
cat > "$WORK/failing-builder.py" <<'PY'
#!/usr/bin/env python3
"""A database builder that fails with the device still connected."""
import sys
sys.exit(1)
PY
mkdir -p "$VANISH/iPod_Control/iTunes" "$VANISH/iPod_Control/Music" \
    "$VANISH/iPod_Control/Speakable"
run env IPOD_DB_TOOL="$WORK/failing-builder.py" \
    "$ROOT/ipod-sync.sh" --ipod "$VANISH" --yes "$LIB/Neil Young"

case_label 6 "a missing dependency: the database builder is not installed"
run env IPOD_DB_TOOL="$WORK/no-such-builder.py" \
    "$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes "Beach Boys/Surfin'.mp3"
printf 'the track is still on the device: '
test -s "$IPOD/iPod_Control/Music/Beach Boys/Surfin'.mp3" && printf 'yes\n'

case_label 7 "a declined prompt: unattended, so the answer is end of input"
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" "Beach Boys/Surfin'.mp3"
printf 'the track is still on the device: '
test -s "$IPOD/iPod_Control/Music/Beach Boys/Surfin'.mp3" && printf 'yes\n'

case_label 7 "the other prompt: a volume that may not be a shuffle"
mkdir -p "$WORK/not-a-shuffle/iPod_Control/iTunes" \
    "$WORK/not-a-shuffle/iPod_Control/Music"
run "$ROOT/ipod-wipe.sh" --ipod "$WORK/not-a-shuffle"

# Everything a caller cannot act on differently stays 1.
case_label 1 "an unknown flag"
run "$ROOT/ipod-remove.sh" --no-such-flag
case_label 1 "a path that is not on the device"
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" --yes '../escape.mp3'
case_label 1 "--json asked for on a path that acts rather than reports"
run "$ROOT/ipod-remove.sh" --ipod "$IPOD" --json "Beach Boys/Surfin'.mp3"
case_label 1 "install.sh --json without --check"
run "$ROOT/install.sh" --json

scene "5. A DEFINITE ANSWER OR NO ANSWER, NEVER A STALE ONE"

printf '\n# an album the walk cannot enter: rglob would have yielded nothing and\n'
printf '#  reported a full iPod as empty. stdout has to stay empty instead.\n'
chmod 000 "$IPOD/iPod_Control/Music/Neil Young"
status=0
"$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json \
    > "$WORK/unreadable-album.json" 2> "$WORK/unreadable-album.err" || status=$?
chmod 755 "$IPOD/iPod_Control/Music/Neil Young"
printf '$ ipod-remove.sh --list --json   # with an unreadable album\n'
printf 'stderr: %s' "$(cat "$WORK/unreadable-album.err")"
printf '\nstdout: %d bytes\n[exit %d]\n' \
    "$(wc -c < "$WORK/unreadable-album.json")" "$status"
record "$status" "a report whose device holds an album it cannot read"

printf '\n# a saved-options file that exists and cannot be read\n'
mv "$IPOD/iPod_Control/.sync-options" "$WORK/saved-options"
mkdir "$IPOD/iPod_Control/.sync-options"
status=0
"$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json \
    > "$WORK/unreadable-options.json" 2> "$WORK/unreadable-options.err" || status=$?
rmdir "$IPOD/iPod_Control/.sync-options"
mv "$WORK/saved-options" "$IPOD/iPod_Control/.sync-options"
printf '$ ipod-remove.sh --list --json   # with an unreadable .sync-options\n'
printf 'stderr: %s' "$(cat "$WORK/unreadable-options.err")"
printf '\nstdout: %d bytes\n[exit %d]\n' \
    "$(wc -c < "$WORK/unreadable-options.json")" "$status"
record "$status" "a report whose device holds saved options it cannot read"

printf '\n# the device pulled out from under the report itself\n'
GONE="$WORK/gone-ipod"
mkdir -p "$GONE/iPod_Control/Music/Beach Boys" "$GONE/iPod_Control/iTunes"
printf 'a song\n' > "$GONE/iPod_Control/Music/Beach Boys/Surfin.mp3"
run /usr/bin/python3 "$ROOT/ipod-report.py" device "$GONE"
rm -rf "$GONE"
status=0
/usr/bin/python3 "$ROOT/ipod-report.py" device "$GONE" \
    > "$WORK/report-gone.json" 2> "$WORK/report-gone.err" || status=$?
printf '\n$ ipod-report.py device %s   # after it was unplugged\n' "$GONE"
printf 'stderr: %s' "$(cat "$WORK/report-gone.err")"
printf '\nstdout: %d bytes\n[exit %d]  <- the same 5 the scripts leave with\n' \
    "$(wc -c < "$WORK/report-gone.json")" "$status"
record "$status" "the report writer alone, on a device that went away"

printf '\n# a machine with no python3 at all: the report says so once, with the\n'
printf '#  dependency code, and the plain listing still answers without it\n'
NOPY="$WORK/no-python-bin"
mkdir -p "$NOPY"
for tool in /usr/bin/* /bin/*; do
    case "${tool##*/}" in python|python3*) continue ;; esac
    ln -sf "$tool" "$NOPY/${tool##*/}" 2>/dev/null || true
done
status=0
timeout 60 env PATH="$NOPY" "$ROOT/ipod-remove.sh" --ipod "$IPOD" --list --json \
    > "$WORK/no-python.json" 2> "$WORK/no-python.err" || status=$?
printf '$ PATH=<no python3> ipod-remove.sh --list --json\n'
printf 'stderr: %s' "$(cat "$WORK/no-python.err")"
printf '\nstdout: %d bytes\n[exit %d]\n' \
    "$(wc -c < "$WORK/no-python.json")" "$status"
record "$status" "--json on a machine with no python3 on it"
status=0
timeout 60 env PATH="$NOPY" "$ROOT/ipod-remove.sh" --ipod "$IPOD" --list \
    > "$WORK/no-python-list.txt" 2>&1 || status=$?
printf '\n$ PATH=<no python3> ipod-remove.sh --list\n'
cat "$WORK/no-python-list.txt"
printf '[exit %d]\n' "$status"
record "$status" "--list on that same machine, which never needed python3"

scene "6. THE TABLE A CALLER BRANCHES ON, AS THE RUNS ABOVE LEFT IT"
printf '\ncode  state\n----  -----------------------------------------------------------\n'
awk -F'\t' '{ printf "%4s  %s\n", $1, $2 }' "$TABLE" | tee "$EV/05-exit-code-table.txt"

hr
printf 'walkthrough finished\n'
