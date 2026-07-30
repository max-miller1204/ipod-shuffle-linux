#!/usr/bin/env bash
# Shared helpers for the iPod shuffle 4G scripts.
# Sourced by ipod-sync.sh and ipod-wipe.sh; not meant to be run directly.

set -euo pipefail

# USB product ID for the iPod shuffle 4th generation.
readonly SHUFFLE_USB_ID="05ac:1303"

# Upstream database builder, installed by setup.sh.
readonly DB_TOOL="${HOME}/ipod-tools/IPod-Shuffle-4g/ipod-shuffle-4g.py"

err()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }

die() { err "$*"; exit 1; }

# List mount points of every mounted vfat filesystem, one per line.
#
# findmnt's raw output escapes spaces as \x20, and iPod names very often contain
# one, so this parses the JSON output instead. Column mode is not an option
# either, since it can truncate long paths to the terminal width.
list_vfat_mounts() {
    command -v python3 >/dev/null || die "python3 is required but not installed."
    findmnt -no TARGET -t vfat --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for fs in data.get("filesystems", []):
    if fs.get("target"):
        print(fs["target"])
' 2>/dev/null || true
}

# Locate the mounted iPod.
#
# Accepts an explicit path as $1, otherwise scans mounted vfat filesystems for
# one containing an iPod_Control directory. Refuses to guess when more than one
# candidate is present, because the caller may be about to delete files.
find_ipod() {
    local explicit="${1:-}"
    if [[ -n "$explicit" ]]; then
        printf '%s' "${explicit%/}"
        return 0
    fi

    local -a candidates=()
    local mp
    while IFS= read -r mp; do
        [[ -d "$mp/iPod_Control" ]] && candidates+=("$mp")
    done < <(list_vfat_mounts)

    case "${#candidates[@]}" in
        0) die "No mounted iPod found. Plug it in, or pass the mount point explicitly." ;;
        1) printf '%s' "${candidates[0]}" ;;
        *) err "Multiple iPods found; pass one explicitly:"
           printf '  %s\n' "${candidates[@]}" >&2
           exit 1 ;;
    esac
}

# Refuse to operate on anything that is not recognisably an iPod shuffle.
#
# This is the guard that stands between a typo and an rm -rf on the wrong
# volume, so it checks structure rather than trusting the path it was handed.
assert_shuffle() {
    local ipod="$1"

    [[ -d "$ipod" ]]                      || die "Not a directory: $ipod"
    [[ -d "$ipod/iPod_Control" ]]         || die "No iPod_Control in $ipod - that is not an iPod."
    [[ -d "$ipod/iPod_Control/iTunes" ]]  || die "No iPod_Control/iTunes in $ipod - unexpected layout."

    # A shuffle has no display, so it ships the Speakable prompts that the
    # screen-based iPods do not. Absence means this is probably a nano/classic,
    # where these scripts would build a database the firmware cannot read.
    if [[ ! -d "$ipod/iPod_Control/Speakable" ]]; then
        warn "No iPod_Control/Speakable directory found."
        warn "This may not be a shuffle. These scripts only support the shuffle 3G/4G."
        confirm "Continue anyway?" || exit 1
    fi

    if ! lsusb 2>/dev/null | grep -q "$SHUFFLE_USB_ID"; then
        warn "No shuffle 4G ($SHUFFLE_USB_ID) on USB; continuing based on directory layout."
    fi
}

# Resolve the backing block device for a mount point, for unmount messages.
ipod_device() {
    findmnt -rno SOURCE --target "$1" 2>/dev/null || true
}

confirm() {
    local prompt="$1" reply
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

require_db_tool() {
    [[ -f "$DB_TOOL" ]] || die "Database tool missing at $DB_TOOL - run ./setup.sh first."
    command -v python3 >/dev/null || die "python3 not found."
}

# Rebuild iTunesSD, the only database the shuffle firmware actually reads.
# Everything on the device is invisible to the player until this runs.
rebuild_database() {
    local ipod="$1"
    shift
    require_db_tool
    info "Rebuilding iTunesSD database"
    python3 "$DB_TOOL" "$@" "$ipod"
}
