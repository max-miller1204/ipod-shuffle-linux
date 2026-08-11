#!/usr/bin/env bash
# Drives install.sh all the way to "Done." so the Verifying section - the
# second place the capability report is printed - can be seen the way a person
# watching an install finish sees it, and compared against what --check says
# about the same machine before and after.
#
# Two stand-ins take the place of the network: a local git repository for the
# database builder, and a virtualenv whose pip has no index to reach but does
# put mutagen where the interpreter beside it will find it. Everything else is
# the shipped script.
set -uo pipefail

ROOT="$1"
EV="$2"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

TOOLS="$WORK/tools"
XDG="$WORK/xdg"

hr() { printf '\n%s\n' "======================================================================"; }
scene() { hr; printf '%s\n' "$*"; hr; }

# Prints the command, runs it, prints what it wrote and the code it left with.
run() {
    local status=0
    printf '\n$ %s\n' "$*"
    "$@" 2>&1 || status=$?
    printf '[exit %d]\n' "$status"
    return 0
}

scene "SETUP: stand-ins for the network, so a real install can finish here"

mkdir -p "$WORK/upstream"
printf '%s\n' '"""Stand-in for the upstream database builder."""' \
    > "$WORK/upstream/ipod-shuffle-4g.py"
git -c init.defaultBranch=main -C "$WORK/upstream" init --quiet
git -C "$WORK/upstream" add ipod-shuffle-4g.py
git -C "$WORK/upstream" \
    -c user.name=Evidence -c user.email=evidence@example.invalid \
    commit --quiet -m 'Stand-in database builder'
git clone --quiet "$WORK/upstream" "$TOOLS/IPod-Shuffle-4g"

mkdir -p "$TOOLS/venv/bin" "$TOOLS/venv/site"
cat > "$TOOLS/venv/bin/python" <<'STUB'
#!/bin/sh
PYTHONPATH="$(dirname "$0")/../site"
export PYTHONPATH
exec /usr/bin/python3 "$@"
STUB
cat > "$TOOLS/venv/bin/pip" <<'STUB'
#!/bin/sh
# No index to reach, but it does for mutagen what the real one does: puts it
# where the interpreter beside it will find it, and not a moment before it is
# asked to.
here="$(dirname "$0")"
printf '%s\n' 'version_string = "1.47.0-stand-in"' > "$here/../site/mutagen.py"
STUB
cat > "$TOOLS/venv/bin/yt-dlp" <<'STUB'
#!/bin/sh
case "$1" in
    --version) printf '%s\n' '2025.11.12' ;;
    --help)    printf '%s\n' '  --js-runtimes RUNTIMES  Runtimes to use' ;;
esac
STUB
chmod +x "$TOOLS/venv/bin/python" "$TOOLS/venv/bin/pip" "$TOOLS/venv/bin/yt-dlp"

printf 'database builder: cloned, not yet compiled by the installer\n'
printf 'virtualenv: present, and mutagen is not in it yet\n'
test ! -e "$TOOLS/venv/site/mutagen.py" && printf '  (confirmed absent)\n'

install_sh() {
    env -u IPOD_DB_TOOL -u IPOD_VENV_PYTHON -u IPOD_VENV_YT_DLP \
        IPOD_TOOLS_DIR="$TOOLS" XDG_DATA_HOME="$XDG" \
        "$ROOT/install.sh" "$@"
}

scene "BEFORE: --check asks what this machine can do, and installs nothing"
run install_sh --check

scene "THE INSTALL: run to completion, ending in the same report"
run install_sh --no-system

scene "AFTER: --check again, on the machine the install left behind"
run install_sh --check

scene "AFTER: and the same answer as a document, for a caller"
run install_sh --check --json

hr
printf '%s\n' "The Verifying section above is not a summary written in its own"
printf '%s\n' "words: it is the nine capabilities --check reports, probed once"
printf '%s\n' "after the install had its turn. Metadata support is the one that"
printf '%s\n' "moves - unavailable before, ok in Verifying and ok after - which"
printf '%s\n' "is what tells a report taken after an install from one taken"
printf '%s\n' "before it."
hr
