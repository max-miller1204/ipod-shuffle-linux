#!/usr/bin/env bash
# What the two permanent checks catch, asked by breaking the fix on purpose.
#
#   run-mutations.sh <repo>
#
# Each round rewrites one line of ipod_gui/playlist_view.py, runs the two
# checks the change touches, and puts the file back. The first round is the
# whole pre-change file, which is the regression the checks exist for: they
# have to fail against the code that stranded the playlist and pass against
# the code that refuses to.
set -uo pipefail

REPO="$(cd "$1" && pwd)"
cd "$REPO"
TARGET=ipod_gui/playlist_view.py
BASE_COMMIT=b48928c72713d0fc1dc3cee7be17ada1e3b24859

restore() { git checkout -- "$TARGET"; }
trap restore EXIT

checks() {
    local playlists window
    if /usr/bin/python3 tests/gui-playlists.py >/tmp/mutation-playlists.txt 2>&1; then
        playlists=pass
    else
        playlists=FAIL
    fi
    if GDK_BACKEND=x11 GSK_RENDERER=cairo /usr/bin/python3 \
        tests/gui-window-build.py >/tmp/mutation-window.txt 2>&1; then
        window=pass
    else
        window=FAIL
    fi
    printf '  tests/gui-playlists.py:    %s\n' "$playlists"
    if [ "$playlists" = FAIL ]; then
        sed -n 's/^\(AssertionError\|.*Error\): /    said: /p' \
            /tmp/mutation-playlists.txt | head -3
        tail -3 /tmp/mutation-playlists.txt | sed 's/^/    /'
    fi
    printf '  tests/gui-window-build.py: %s\n' "$window"
    if [ "$window" = FAIL ]; then
        grep -v '^ *$' /tmp/mutation-window.txt | tail -6 | sed 's/^/    /'
    fi
    [ "$playlists" = pass ] && [ "$window" = pass ] && return 0
    return 1
}

python_replace() {
    /usr/bin/python3 - "$TARGET" "$1" "$2" <<'PY'
import sys
from pathlib import Path
path, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
if text.count(old) != 1:
    raise SystemExit(f"{old!r} appears {text.count(old)} times, not once")
path.write_text(text.replace(old, new), encoding="utf-8")
PY
}

echo "== the shipped code =="
checks && echo "  (both pass, as they must)"
echo

echo "== round 1: the whole pre-change file ($BASE_COMMIT) =="
echo "   the rename that cannot restage the new name is confirmed and run"
git show "$BASE_COMMIT:$TARGET" > "$TARGET"
checks || echo "  caught"
restore
echo

mutate() {
    local title="$1" old="$2" new="$3"
    echo "== $title =="
    if ! python_replace "$old" "$new"; then
        echo "  could not apply this mutation"
        restore
        return
    fi
    checks || echo "  caught"
    restore
    echo
}

mutate "round 2: no refusal before the dialog opens" \
    '        refused = self._rename_refusal(playlist)
        if refused:
            self._toast(refused)
            return None' \
    '        refused = None'

mutate "round 3: no refusal before the file moves" \
    '        refused = self._rename_refusal(playlist)
        if refused:
            self._toast(refused)
            return
        if rename_local_playlist(playlist.path, new_name) is None:' \
    '        if rename_local_playlist(playlist.path, new_name) is None:'

mutate "round 4: the refusal asks about the old name instead of the new one" \
    '        if not self._staging_wanted(playlist, on_device=False):
            return None' \
    '        if not self._staging_wanted(playlist):
            return None'

mutate "round 5: the confirmation promises staging whenever it can speak" \
    '            if self._staging_wanted(playlist, on_device=False):
                asked += " and stages the new name for the next sync."' \
    '            if self.speech_engine_available:
                asked += " and stages the new name for the next sync."'

mutate "round 6: nothing is unstaged when nothing is wanted" \
    '            if self._staging_wanted(playlist):
                wanted.append(playlist)
            else:
                self.unqueue_source(playlist.path)' \
    '            wanted.append(playlist)'

echo "the working tree is back to the shipped code:"
git status --short "$TARGET" | sed 's/^/  /'
git diff --stat "$TARGET" | sed 's/^/  /'
echo "  (no output above means unmodified)"
