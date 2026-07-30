#!/usr/bin/env bash
# Shared helpers for the iPod shuffle 4G scripts.
# Sourced by ipod-sync.sh and ipod-wipe.sh; not meant to be run directly.

set -euo pipefail

# USB product ID for the iPod shuffle 4th generation.
readonly SHUFFLE_USB_ID="05ac:1303"

# Upstream database builder, installed by install.sh.
#
# All three paths are overridable so the same scripts work unmodified inside
# a Flatpak, where the builder is baked into /app and mutagen belongs to the
# runtime interpreter rather than a virtualenv.
readonly TOOLS_DIR="${IPOD_TOOLS_DIR:-${HOME}/ipod-tools}"
readonly DB_TOOL="${IPOD_DB_TOOL:-${TOOLS_DIR}/IPod-Shuffle-4g/ipod-shuffle-4g.py}"

# Dedicated virtualenv holding mutagen.
#
# Distros increasingly ship an externally managed Python (PEP 668), and the
# interpreter first on PATH is not necessarily the one apt installs into. A
# uv- or pyenv-managed python3, for example, cannot see /usr/lib/python3/
# dist-packages at all, so "apt install python3-mutagen" would appear to
# succeed while the builder still reported no metadata support. Owning a venv
# sidesteps the question entirely.
readonly VENV_PYTHON="${IPOD_VENV_PYTHON:-${TOOLS_DIR}/venv/bin/python}"

# yt-dlp, used by ipod-fetch.sh. Same virtualenv, for the same reasons.
readonly VENV_YT_DLP="${IPOD_VENV_YT_DLP:-${TOOLS_DIR}/venv/bin/yt-dlp}"

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

# UDisks2 D-Bus object path for a block device, e.g. /dev/sda -> .../sda.
udisks_path() {
    printf '/org/freedesktop/UDisks2/block_devices/%s' "${1##*/}"
}

# Mount and unmount through UDisks2.
#
# udisksctl is the friendlier front end and is used when present, but the
# Flatpak runtime ships gdbus without it, so both paths have to work. Either
# way the request reaches the same daemon and the same polkit check, which
# grants removable media to the logged-in user without a password.
udisks_method() {
    local dev="$1" method="$2"
    gdbus call --system --dest org.freedesktop.UDisks2 \
        --object-path "$(udisks_path "$dev")" \
        --method "org.freedesktop.UDisks2.Filesystem.$method" "{}"
}

ipod_unmount() {
    local dev="$1"
    if command -v udisksctl >/dev/null 2>&1; then
        udisksctl unmount -b "$dev"
    elif command -v gdbus >/dev/null 2>&1; then
        udisks_method "$dev" Unmount >/dev/null && echo "Unmounted $dev."
    else
        die "Neither udisksctl nor gdbus found; cannot unmount."
    fi
}

ipod_mount() {
    local dev="$1"
    if command -v udisksctl >/dev/null 2>&1; then
        udisksctl mount -b "$dev"
    elif command -v gdbus >/dev/null 2>&1; then
        udisks_method "$dev" Mount
    else
        die "Neither udisksctl nor gdbus found; cannot mount."
    fi
}

confirm() {
    local prompt="$1" reply
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# Interpreter used to run the database builder.
#
# Prefers install.sh's venv, which is the only one guaranteed to have mutagen.
# Falls back to whatever python3 is on PATH so the scripts still work without
# the venv, just without artist and album metadata in the database.
db_python() {
    if [[ -x "$VENV_PYTHON" ]]; then
        printf '%s' "$VENV_PYTHON"
    else
        command -v python3 >/dev/null || die "python3 not found."
        printf 'python3'
    fi
}

require_db_tool() {
    [[ -f "$DB_TOOL" ]] || die "Database tool missing at $DB_TOOL - run ./install.sh first."
}

# Locate yt-dlp, preferring install.sh's virtualenv over PATH.
#
# The venv copy is the one this project keeps current, but a system or pipx
# install is just as good and is often newer, so PATH is a fallback rather
# than an error. Prints the executable, or dies if neither exists.
yt_dlp_bin() {
    if [[ -x "$VENV_YT_DLP" ]]; then
        printf '%s' "$VENV_YT_DLP"
    elif command -v yt-dlp >/dev/null 2>&1; then
        command -v yt-dlp
    else
        die "yt-dlp not found - run ./install.sh, or install it with 'pipx install yt-dlp'."
    fi
}

# Name a JavaScript runtime yt-dlp can use, or return 1 if none is installed.
#
# YouTube protects most media URLs behind a signature challenge that has to be
# solved in JavaScript. yt-dlp enables only deno by default, so on a machine
# with node or bun but no deno it extracts metadata perfectly, hands back an
# undeciphered URL, and every download fails with HTTP 403. Old unrestricted
# uploads still work, which makes the failure look video-specific rather than
# environmental and sends you looking in the wrong place.
#
# Probing for whichever runtime exists follows find_gui_python: ask what the
# machine actually has rather than hardcode one distribution's answer. deno is
# first because it is what yt-dlp itself defaults to and tests against.
js_runtime() {
    local candidate
    for candidate in deno node bun; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

# Interpreter capable of running the GTK4 GUI.
#
# PyGObject comes from the distro, so it belongs to the system interpreter,
# which is often not the python3 first on PATH. Rather than hardcode a path
# that only holds on Debian derivatives, try the plausible ones and let the
# import decide. Prints the interpreter and returns 0, or returns 1 if none
# can drive GTK4.
find_gui_python() {
    local candidate
    for candidate in python3 /usr/bin/python3 /usr/bin/python3.12 /usr/bin/python3.13; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" - <<'PROBE' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
PROBE
        then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# Rebuild iTunesSD, the only database the shuffle firmware actually reads.
# Everything on the device is invisible to the player until this runs.
rebuild_database() {
    local ipod="$1"
    shift
    require_db_tool
    info "Rebuilding iTunesSD database"
    "$(db_python)" "$DB_TOOL" "$@" "$ipod"
}
