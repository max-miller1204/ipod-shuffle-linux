#!/usr/bin/env bash
# Drives the three scripts that speak the progress protocol the way a caller
# drives them: a synthetic iPod, a source folder holding the names that arrive
# from tags and YouTube titles, and a descriptor opened for the JSON.
#
# Writes the transcript and the streams into this directory. The upstream
# database builder is not installed on this machine, so the rebuild runs
# through tests/fake-db-builder.py, exactly as the suite does.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
TRANSCRIPT="$HERE/01-cli-walkthrough.txt"

TEST_ROOT="$(mktemp -d -t progress-evidence.XXXXXX)"
trap 'rm -rf "$TEST_ROOT"' EXIT

export IPOD_DB_TOOL="$ROOT/tests/fake-db-builder.py"
export IPOD_VENV_PYTHON="/usr/bin/python3"
export FAKE_DB_RECORD="$TEST_ROOT/database-invocations.jsonl"
export ROOT

: > "$TRANSCRIPT"
say() { printf '%s\n' "$*" >> "$TRANSCRIPT"; }
show() { sed 's/^/    /' "$1" >> "$TRANSCRIPT"; }

# The command as a reader would type it, then the command as it is run, so the
# redirections are visible rather than hidden behind a helper.
run() {
    local status=0
    say ""
    say "\$ $1"
    bash -c "$2" >> "$TRANSCRIPT" 2>&1 || status=$?
    say "[exit $status]"
}

quote_name='02 - Say "hi", it'"'"'s \ fine.mp3'
newline_name=$'03 - Line\nbreak.mp3'

make_ipod() {
    mkdir -p \
        "$1/iPod_Control/iTunes" \
        "$1/iPod_Control/Music" \
        "$1/iPod_Control/Speakable" \
        "$1/iPod_Control/Device"
    printf 'device identity\n' > "$1/iPod_Control/Device/SysInfo"
}

cd "$TEST_ROOT"
mkdir -p "Odd Album"
printf 'first\n' > "Odd Album/01 - Plain.mp3"
printf 'second\n' > "Odd Album/$quote_name"
printf 'third\n' > "Odd Album/$newline_name"
printf 'artwork\n' > "Odd Album/cover.flac"
ln -s /nowhere/at/all.mp3 "Odd Album/04 - Dangling.mp3"
printf '%s\n' "$TEST_ROOT/Odd Album/01 - Plain.mp3" \
    /nowhere/not-on-this-computer.mp3 > Weekend.m3u

for device in iPod iPod-no-stream iPod-spare; do
    make_ipod "$device"
done

say "The scripts, driven from a shell, reporting themselves twice: once to the"
say "terminal for a person and once as JSON on a descriptor for the GUI's sync"
say "bar. Everything below ran in one temporary directory holding a synthetic"
say "iPod and a source album whose names are the awkward ones."
run 'ls -1 "Odd Album"' 'ls -1 "Odd Album"'
say ""
say "(the third name holds a real newline, which is why it reads as two lines)"

say ""
say "=== 1. A sync reporting itself on descriptor 3 =========================="
run 'ipod-sync.sh --ipod ./iPod --playlist-voiceover --progress-json \
      "Odd Album" Weekend.m3u  3> sync.ndjson' \
    'set -o pipefail; "$ROOT/ipod-sync.sh" --ipod ./iPod --playlist-voiceover \
      --progress-json "Odd Album" Weekend.m3u 3> sync.ndjson 2>&1 \
      | tee with-stream.txt'
cp sync.ndjson "$HERE/02-sync-progress.ndjson"
say ""
say "$ cat sync.ndjson          # 02-sync-progress.ndjson"
show sync.ndjson

say ""
say "=== 2. The stream is additional: the terminal output is untouched ======="
say "The same sync onto an identical device with no --progress-json, diffed"
say "against the run above with the two mount points spelt the same."
"$ROOT/ipod-sync.sh" --ipod ./iPod-no-stream --playlist-voiceover \
    "Odd Album" Weekend.m3u > no-stream.txt 2>&1
run 'diff -u <(sed s/iPod-no-stream/IPOD/ no-stream.txt) \
      <(sed s/iPod/IPOD/ with-stream.txt) && echo "byte for byte identical"' \
    'diff -u --label "without --progress-json" --label "with --progress-json" \
      <(sed "s|./iPod-no-stream|IPOD|g" no-stream.txt) \
      <(sed "s|./iPod|IPOD|g" with-stream.txt) \
      && echo "byte for byte identical"'

say ""
say "=== 3. Removal, on whichever descriptor the caller opened =============="
run 'ipod-remove.sh --ipod ./iPod --yes --progress-json=7 \
      -- "Odd Album/01 - Plain.mp3"  7> remove.ndjson' \
    '"$ROOT/ipod-remove.sh" --ipod ./iPod --yes --progress-json=7 \
      -- "Odd Album/01 - Plain.mp3" 7> remove.ndjson'
cp remove.ndjson "$HERE/03-remove-progress.ndjson"
say ""
say "$ cat remove.ndjson        # 03-remove-progress.ndjson"
show remove.ndjson
say ""
say "(the playlist naming that track was rewritten too - work the run could"
say " not have planned, so the total followed the count rather than the count"
say " passing a total that was already announced)"

say ""
say "=== 4. A wipe, which is one bulk delete rather than a file at a time ===="
run 'ipod-wipe.sh --ipod ./iPod --yes --backup ./backup --progress-json \
      3> wipe.ndjson' \
    '"$ROOT/ipod-wipe.sh" --ipod ./iPod --yes --backup ./backup \
      --progress-json 3> wipe.ndjson'
cp wipe.ndjson "$HERE/04-wipe-progress.ndjson"
say ""
say "$ cat wipe.ndjson          # 04-wipe-progress.ndjson"
show wipe.ndjson
say ""
say "(no plan and no per-file events: the stages are what a bar can show for a"
say " bulk delete, and a denominator nothing counts against would sit at zero)"

say ""
say "=== 5. A run that never reaches an iPod still ends with a result ========"
run 'ipod-sync.sh --ipod ./nothing-here --progress-json "Odd Album" \
      3> failed.ndjson' \
    '"$ROOT/ipod-sync.sh" --ipod ./nothing-here --progress-json "Odd Album" \
      3> failed.ndjson'
say "$ cat failed.ndjson"
show failed.ndjson

say ""
say "=== 6. A reader that walks away mid-copy ================================"
say "Closing the window during a sync is this: the reader takes one line and"
say "goes. A shell writing to a pipe nobody holds is killed outright, which"
say "would have left the iPod half written."
run 'ipod-sync.sh --ipod ./iPod-spare --progress-json "Odd Album" \
      3>&1 1> copy.txt | head -1' \
    '"$ROOT/ipod-sync.sh" --ipod ./iPod-spare --progress-json "Odd Album" \
      3>&1 1> copy.txt 2>&1 | head -1'
say ""
say "$ cat copy.txt             # what the run said, having lost its reader"
show copy.txt
say ""
say "$ find ./iPod-spare/iPod_Control/Music -type f   # copied anyway"
find ./iPod-spare/iPod_Control/Music -type f -printf '    %P\n' | sort \
    >> "$TRANSCRIPT"

say ""
say "=== 7. A descriptor nobody opened is refused rather than reported into ="
say "nowhere. Asked of all three scripts, because the flag is theirs jointly."
run 'ipod-sync.sh --ipod ./iPod --progress-json --rebuild-only' \
    '"$ROOT/ipod-sync.sh" --ipod ./iPod --progress-json --rebuild-only'
run 'ipod-sync.sh --ipod ./iPod --progress-json=stdout --rebuild-only' \
    '"$ROOT/ipod-sync.sh" --ipod ./iPod --progress-json=stdout --rebuild-only'
run 'ipod-sync.sh --ipod ./iPod --progress-json= --rebuild-only' \
    '"$ROOT/ipod-sync.sh" --ipod ./iPod --progress-json= --rebuild-only'
run 'ipod-remove.sh --ipod ./iPod --progress-json= --yes --list' \
    '"$ROOT/ipod-remove.sh" --ipod ./iPod --progress-json= --yes --list'
run 'ipod-wipe.sh --ipod ./iPod --progress-json= --yes' \
    '"$ROOT/ipod-wipe.sh" --ipod ./iPod --progress-json= --yes'

say ""
say "=== end of session ====================================================="

# The same session as pictures, because the point of keeping the human output
# untouched is how it reads in a terminal, and a .txt full of escape codes is
# not that. The renderer is the one written for the previous goal's evidence.
RENDER="$HERE/../json-output-and-exit-codes/driver-render-png.py"
/usr/bin/python3 "$RENDER" "$TRANSCRIPT" \
    "=== 1. A sync reporting itself" "=== 2. The stream is additional" \
    "$HERE/06-terminal-sync.png" \
    "ipod-sync.sh --progress-json: the terminal output, and the stream beside it"
/usr/bin/python3 "$RENDER" "$TRANSCRIPT" \
    "=== 6. A reader that walks away" "=== 7. A descriptor nobody opened" \
    "$HERE/07-terminal-reader-left.png" \
    "A caller that stops reading mid-copy does not take the copy with it"
/usr/bin/python3 "$RENDER" "$TRANSCRIPT" \
    "=== 7. A descriptor nobody opened" "=== end of session" \
    "$HERE/08-terminal-refusals.png" \
    "A descriptor nobody opened is refused, in all three scripts"

printf 'wrote %s\n' "$TRANSCRIPT"
