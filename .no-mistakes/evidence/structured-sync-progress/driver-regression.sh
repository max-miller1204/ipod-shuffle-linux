#!/usr/bin/env bash
# The failure this change exists to end, reproduced against the code as it was
# and then asked again of the code as it is.
#
# Two copies of the project are checked out into a scratch directory - the
# commit this branch started from and the branch itself - and the same one-line
# reword is made to ipod-sync.sh's copy line in both. Before, that reword left
# the GUI's sync bar dead and nothing failed. After, the bar is reading JSON on
# a stream of its own and does not notice.
#
# Needs a GDK display for the widgets the bar builds. One is started here if
# there is none, the same way the screenshot driver does it, so this can be run
# on a machine with no X server.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
REPORT="$HERE/05-reword-before-and-after.txt"

SCRATCH="$(mktemp -d -t progress-regression.XXXXXX)"
trap 'rm -rf "$SCRATCH"' EXIT

BASE="$(git -C "$ROOT" rev-parse ade663f42ea1eabe98a1d193c0ce9fa991f1f597)"
mkdir -p "$SCRATCH/before" "$SCRATCH/after"
git -C "$ROOT" archive "$BASE" | tar -x -C "$SCRATCH/before"
git -C "$ROOT" archive HEAD | tar -x -C "$SCRATCH/after"

# The reword: the same sentence, said differently. Nothing about it changes
# what the script does, and a person reading the terminal would not call it a
# regression.
for tree in before after; do
    cp "$SCRATCH/$tree/ipod-sync.sh" "$SCRATCH/$tree/ipod-sync.shipped.sh"
    perl -0pi -e "s/printf '  \+ %s -> %s\\\\n'/printf '  copied %s to %s\\\\n'/" \
        "$SCRATCH/$tree/ipod-sync.sh"
    grep -q "copied %s to %s" "$SCRATCH/$tree/ipod-sync.sh"
done

export IPOD_VENV_PYTHON="/usr/bin/python3"
export FAKE_DB_RECORD="$SCRATCH/database-invocations.jsonl"
export SCRATCH

if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    DISPLAY_NUMBER="${BROADWAY_DISPLAY_NUMBER:-8}"
    gtk4-broadwayd ":$DISPLAY_NUMBER" > /tmp/broadwayd-regression.log 2>&1 &
    BROADWAY_PID=$!
    trap 'rm -rf "$SCRATCH"; kill "$BROADWAY_PID" 2>/dev/null || true' EXIT
    sleep 1
    export GDK_BACKEND=broadway
    export BROADWAY_DISPLAY=":$DISPLAY_NUMBER"
fi

{
    printf '%s\n' \
        "The sync bar, before and after, when one line of ipod-sync.sh is" \
        "reworded:" \
        "" \
        "    -    printf '  + %s -> %s\\n' \\" \
        "    +    printf '  copied %s to %s\\n' \\" \
        "" \
        "Both trees are the project as it was committed, with that one edit." \
        "The album being copied is three tracks, one of them holding a real" \
        "newline in its name."
} > "$REPORT"

/usr/bin/python3 "$HERE/driver-regression.py" before shipped >> "$REPORT"
/usr/bin/python3 "$HERE/driver-regression.py" before reworded >> "$REPORT"
/usr/bin/python3 "$HERE/driver-regression.py" after reworded >> "$REPORT"

printf 'wrote %s\n' "$REPORT"
