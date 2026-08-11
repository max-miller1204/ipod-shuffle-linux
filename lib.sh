#!/usr/bin/env bash
# Shared helpers for the iPod shuffle 4G scripts.
# Sourced by the command-line scripts and GUI dependency probes; not run directly.

set -euo pipefail

# USB product ID for the iPod shuffle 4th generation.
readonly SHUFFLE_USB_ID="05ac:1303"

# The states a caller has to tell apart, as numbers rather than as English.
#
# Everything else - a bad flag, a path that is not on the device, a builder
# that failed - stays 1, because a caller cannot do anything different about
# those beyond reporting them. These five it can: plug the iPod in, name which
# one, wait for the volume to come back, run ./install.sh, or ask again.
#
# ipod-report.py repeats EXIT_DEVICE_GONE, since a report run on its own still
# has to be able to say it; tests/product-e2e.sh fails if the two disagree.
readonly EXIT_NO_IPOD=3
readonly EXIT_SEVERAL_IPODS=4
readonly EXIT_DEVICE_GONE=5
readonly EXIT_MISSING_DEPENDENCY=6
readonly EXIT_DECLINED=7

# The JSON writer the scripts report through, beside this file.
REPORT_TOOL="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/ipod-report.py"
readonly REPORT_TOOL

# Upstream database builder, installed by install.sh.
#
# All three paths are overridable so the test suite can substitute its own
# builder and interpreter without touching a real installation.
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

# What the shuffle firmware will actually play, and so what is worth copying.
#
# Shared because ipod-fetch.sh counts these files and ipod-sync.sh filters by
# the same contract; disagreement would silently miscount or skip tracks.
#
# Read by the scripts that source this file, which shellcheck cannot see.
# shellcheck disable=SC2034
readonly SUPPORTED_EXT="mp3|m4a|m4b|m4p|aa|wav"

# How many files are under this path.
#
# Counted a character at a time rather than a line at a time, because find
# prints one path per line and a track called "Line<newline>break.mp3" is then
# two of them. Newlines in filenames are rare and perfectly legal, and every
# count in these scripts is something a person is told or a caller acts on:
# "iPod now holds 4 track(s)" for three tracks, a backup verified against a
# figure that was never the number of files, a progress bar with a denominator
# nothing can reach.
count_files() {
    find "$@" -type f -printf '.' | wc -c
}

# Whether the firmware could play a file with this name.
#
# The one place the question is asked, because the copy decides what to skip by
# it and the count before the copy decides what it is about to report on by it:
# two spellings of the same test would drift, and a progress bar that counted
# what the copy would not report on could never reach its end.
playable_name() {
    [[ "${1,,}" =~ \.(${SUPPORTED_EXT})$ ]]
}

err()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }

die() { die_with 1 "$@"; }

# Die with one of the codes above. The message is still printed, because the
# number is for the caller and the sentence is for the person reading it.
die_with() {
    local code="$1"
    shift
    err "$*"
    leave "$code"
}

# The mount point a script latched onto, and what the volume there called
# itself at the time.
DEVICE_WATCH_MOUNT=""
DEVICE_WATCH_IDENTITY=""

# Ask the volume who it is, or print nothing when it will not say.
#
# A machine with no python3 on it is one more volume that will not say, and
# not a failure of its own: this is what the way out re-reads to decide
# whether the device went away, so a question asked here must always come back
# with an answer. The work that genuinely needs the interpreter - the JSON
# report and the database builder - asks for it by name and leaves with the
# missing-dependency code, while a listing that never needed it still runs.
device_identity() {
    command -v python3 >/dev/null || return 0
    python3 "$REPORT_TOOL" identity "$1" 2>/dev/null || true
}

# Print the JSON report for a mounted device, or print nothing at all.
#
# The writer decides between "the volume stopped answering" and "something
# else could not be read", because it is the one that walked the device and
# re-read its identity afterwards; this passes that answer straight out rather
# than forming a second opinion about a device it did not read.
device_report() {
    local status=0
    command -v python3 >/dev/null \
        || die_with "$EXIT_MISSING_DEPENDENCY" "python3 is required but not installed."
    python3 "$REPORT_TOOL" device "$1" || status=$?
    (( status == 0 )) || leave "$status"
}

# ---------------------------------------------------------------- progress
#
# What a run is doing while it is still doing it, as one JSON object per line
# on a descriptor of the caller's choosing.
#
# A second stream rather than a replacement: the human output is the product,
# and it stays exactly as it reads in a terminal. Before this, the GUI drove
# its sync bar by matching a regex against the copy lines below, so rewording
# one of them broke the bar and nothing failed when it did.
#
# The JSON itself is written by ipod-report.py, one process for the whole run,
# because a track name came from a tag or a YouTube title and a shell has no
# way to say that a quote or a newline in one is part of the name. This end
# writes the plain fields: the unit separator between them, NUL between
# records, that being the one byte a filename cannot hold.
PROGRESS_FD=""
PROGRESS_ENCODER_PID=""
PROGRESS_ENABLED=0

# The shell that opened the stream, which is the only one that may end it.
#
# A command substitution is a subshell holding its own copy of the descriptor:
# find_ipod dies inside one when there is no iPod to find, and left to report
# for itself it would write a result event the main shell then wrote again,
# and wait for an encoder that is not its child. The failure comes back to the
# main shell as a status, and that is where the run ends.
PROGRESS_OWNER=""

# How far along the run is, in items it said it would report on.
PROGRESS_DONE=0
PROGRESS_TOTAL=0

progress_enabled() { (( PROGRESS_ENABLED )); }

# Read the descriptor a --progress-json[=FD] argument names into
# PROGRESS_TARGET, defaulting to 3.
#
# Written once and shared, because an empty value is the one way past the
# refusals below: --progress-json= asks for a stream as plainly as any other
# spelling of the flag, and left as the empty string it would arrive at
# progress_open as "no stream was asked for" and report nowhere in silence.
# Not a command substitution, so that die() here ends the run rather than a
# subshell holding an unopened stream.
progress_flag() {
    case "$1" in
        *=*)
            PROGRESS_TARGET="${1#*=}"
            [[ -n "$PROGRESS_TARGET" ]] \
                || die "--progress-json takes a descriptor number, not an empty one."
            ;;
        *) PROGRESS_TARGET=3 ;;
    esac
}

# Open the stream on the descriptor the caller passed, or do nothing at all.
#
# The encoder is reached through a named pipe that is unlinked the moment both
# ends are open, so there is nothing left to clean up however the script
# leaves - and no exit trap here to be overwritten by the ones the scripts
# already use for their own temporary files.
progress_open() {
    local script="$1" target="${2:-}" directory
    [[ -n "$target" ]] || return 0
    [[ "$target" =~ ^[0-9]+$ ]] \
        || die "--progress-json takes a descriptor number, not '$target'."
    # Named rather than opened here: the caller decides where its own machine
    # stream goes, and a descriptor it never opened is a mistake worth saying
    # out loud rather than a run whose progress goes nowhere.
    # Spelt 1>&"$fd" rather than >&"$fd" throughout: with a variable after it,
    # the short form is the one that redirects both streams at once, and this
    # one is only ever about where a copy of stdout goes.
    { : 1>&"$target"; } 2>/dev/null \
        || die "--progress-json needs descriptor $target open for writing, e.g. ${target}>progress.ndjson."
    command -v python3 >/dev/null \
        || die_with "$EXIT_MISSING_DEPENDENCY" \
            "python3 is required to report progress as JSON."

    directory="$(mktemp -d -t ipod-progress.XXXXXX)" \
        || die "Could not create the progress stream."
    mkfifo "$directory/events" || die "Could not create the progress stream."
    python3 "$REPORT_TOOL" progress < "$directory/events" 1>&"$target" &
    PROGRESS_ENCODER_PID=$!
    # Blocks until the encoder has the reading end, which is what makes the
    # unlink below safe: the pipe outlives its name.
    exec {PROGRESS_FD}> "$directory/events"
    rm -rf -- "$directory"

    PROGRESS_OWNER="$BASHPID"
    PROGRESS_ENABLED=1
    progress_event start script "$script"
}

# Write one event: its name, then alternating field names and values.
#
# A stream nobody is reading any more must not take the run down with it. The
# copy onto the device is the thing being asked for, and a window closed half
# way through it is not a reason to leave an iPod half written. That takes
# ignoring SIGPIPE around the write and not merely discarding the error: a
# write to a pipe whose reader has gone kills the shell outright, so the copy
# would have ended as a signal rather than as a failure anything could catch.
# Scoped to the write, because the human output going nowhere is a different
# matter and still ends the run.
#
# NUL is the byte no filename can hold, so nothing in a name can end a record
# early; the unit separator between the fields of one is not, and a name
# carrying it would split into a field of its own that the encoder refuses -
# taking the encoder down and the rest of the run's reporting with it. It is
# replaced on the way out, in the one place every value passes through, the
# way the device stems already replace the control bytes FAT will not take.
progress_event() {
    local written=0 value
    local -a record=()
    progress_enabled || return 0
    for value in "$@"; do
        record+=("${value//$'\037'/_}")
    done
    trap '' PIPE
    if { printf '%s\037' "${record[@]}" && printf '\000'; } 1>&"$PROGRESS_FD" 2>/dev/null
    then
        written=1
    fi
    trap - PIPE
    (( written == 0 )) || return 0
    PROGRESS_ENABLED=0
    warn "The progress stream stopped; the rest of this run is not reported on it."
}

# Say how many items this run will report on, before it starts on them.
progress_plan() {
    progress_enabled || return 0
    PROGRESS_TOTAL="$1"
    progress_event plan total "$1"
}

# Count one item of the plan, whatever kind it was.
#
# The total follows the count when a run turns out to have more to do than it
# planned - a playlist rewritten because a track left it was never in the
# plan - so that a bar reading done against total can never show more than
# all of it.
progress_item() {
    PROGRESS_DONE=$((PROGRESS_DONE + 1))
    (( PROGRESS_DONE <= PROGRESS_TOTAL )) || PROGRESS_TOTAL="$PROGRESS_DONE"
}

# One file the run has finished with: copied, already there, or not copied.
progress_file() {
    progress_enabled || return 0
    progress_item
    if [[ -n "${3:-}" ]]; then
        progress_event file status "$1" name "$2" dest "$3" \
            'done' "$PROGRESS_DONE" total "$PROGRESS_TOTAL"
    else
        progress_event file status "$1" name "$2" \
            'done' "$PROGRESS_DONE" total "$PROGRESS_TOTAL"
    fi
}

# One playlist the run has finished with, and how many tracks it now holds.
progress_playlist() {
    progress_enabled || return 0
    progress_item
    progress_event playlist status "$1" name "$2" tracks "$3" \
        'done' "$PROGRESS_DONE" total "$PROGRESS_TOTAL"
}

# A phase of the run, for the stretches that are one long wait rather than a
# file at a time: a backup, a bulk delete, the database rebuild.
progress_stage() {
    progress_event stage name "$1" state "$2"
}

# What a script adds to the result event: its own counters, one field name or
# value per line. Overridden by the scripts that have counters to report, so
# that one with none needs no boilerplate.
progress_result_fields() { :; }

# The last thing on the stream: how the run ended, and what it did.
#
# Read from the script at the moment it leaves rather than kept up to date as
# it goes, so a run that fails half way through still reports the half it did.
progress_finish() {
    local code="$1"
    local -a extra=()
    [[ "$BASHPID" == "$PROGRESS_OWNER" ]] || return 0
    if progress_enabled; then
        mapfile -t extra < <(progress_result_fields)
        progress_event result code "$code" ${extra[@]+"${extra[@]}"}
    fi
    progress_close
}

# Close the stream and wait for the encoder to finish writing it.
#
# Waited for rather than left to finish on its own: the caller reads the
# stream to its end, and a script that returned first would have a result
# event still in flight behind it.
progress_close() {
    local status=0 was_open="$PROGRESS_ENABLED"
    [[ -n "$PROGRESS_FD" ]] || return 0
    PROGRESS_ENABLED=0
    exec {PROGRESS_FD}>&-
    PROGRESS_FD=""
    wait "$PROGRESS_ENCODER_PID" || status=$?
    # Said only about a stream that was still working: one that stopped under
    # a write has already reported itself, and the encoder's own exit is the
    # same event seen from the other end rather than a second thing to fix.
    (( status == 0 || was_open == 0 )) \
        || warn "The progress stream ended badly (exit $status); the run itself is unaffected."
}

# Start treating a later failure as a device that went away.
#
# Unplugging an iPod mid-copy is the failure this project sees most, and it
# arrives as whichever command happened to touch the volume next: a cp that
# cannot write, a find that cannot descend, a builder that cannot open its
# database. Naming a code at each of those would be naming the same state in
# a dozen places and still missing the next one, so the diagnosis is made
# where the script leaves instead - by looking at whether the device is still
# there, which is the only thing that actually decides it.
#
# ERR rather than EXIT because the scripts already use EXIT to clean up their
# temporary files, and because a failure is exactly what this is about: an
# ordinary "exit 1" from die_with comes through leave() directly.
watch_device() {
    DEVICE_WATCH_MOUNT="$1"
    DEVICE_WATCH_IDENTITY="$(device_identity "$1")"
    watch_failures
}

# Route every failure through leave(), so that a script has one way out.
#
# Installed before the device is known as well as with it, because leave() is
# what ends the progress stream and the search for the iPod can fail before
# there is one to watch. That search runs in a command substitution, and
# without this the status it hands back would end the script where it stands,
# with the stream unfinished behind it.
#
# -E so the trap is inherited by the functions and subshells the copy and the
# playlist rewrite run in, which is where these failures happen.
watch_failures() {
    set -E
    trap 'leave $?' ERR
}

# Whether the device this script latched onto stopped answering.
#
# Both halves matter: the volume can be unplugged, and it can be unmounted and
# replaced by a different one at the same path while a sync is running.
device_vanished() {
    [[ -n "$DEVICE_WATCH_MOUNT" ]] || return 1
    [[ -d "$DEVICE_WATCH_MOUNT/iPod_Control" ]] || return 0
    [[ "$(device_identity "$DEVICE_WATCH_MOUNT")" == "$DEVICE_WATCH_IDENTITY" ]] \
        || return 0
    return 1
}

# Whether a script is already on its way out.
LEAVING=""

# Leave with a status, re-read as a vanished device when that is what happened.
#
# The look at the device is the last thing a script does, and anything that
# died while taking it would arrive back here: the second pass leaves with the
# status it was given rather than asking again, so the way out is always a way
# out.
leave() {
    local code="$1"
    # First, so that the checks below cannot re-enter this through the trap.
    trap - ERR
    [[ -z "$LEAVING" ]] || exit "$code"
    LEAVING=1
    if [[ "$code" != 0 && "$code" != "$EXIT_DEVICE_GONE" ]] && device_vanished; then
        err "The iPod stopped answering; it was unplugged or replaced mid-operation."
        code="$EXIT_DEVICE_GONE"
    fi
    # After the diagnosis above, so the stream reports the code the caller is
    # about to be given rather than the one the failure arrived as. Every way
    # out of a script comes through here, which is what makes the result event
    # the last one on the stream whatever happened.
    progress_finish "$code"
    exit "$code"
}

root_playlist_files() {
    find "$1" -maxdepth 1 -type f \
        \( -iname '*.m3u' -o -iname '*.pls' \) -print0
}

atomic_replace_lines() {
    local target="$1" directory temporary
    shift

    case "$target" in
        */*)
            directory="${target%/*}"
            [[ -n "$directory" ]] || directory="/"
            ;;
        *) directory="." ;;
    esac
    temporary="$(mktemp "$directory/.ipod-tmp.XXXXXX")" \
        || die "Could not create a temporary file beside $target."
    if ! printf '%s\n' "$@" > "$temporary"; then
        rm -f -- "$temporary"
        die "Could not write $target."
    fi
    if ! mv -f -- "$temporary" "$target"; then
        rm -f -- "$temporary"
        die "Could not replace $target."
    fi
}

# List mount points of every mounted vfat filesystem, one per line.
#
# findmnt's raw output escapes spaces as \x20, and iPod names very often contain
# one, so this parses the JSON output instead. Column mode is not an option
# either, since it can truncate long paths to the terminal width.
list_vfat_mounts() {
    command -v python3 >/dev/null \
        || die_with "$EXIT_MISSING_DEPENDENCY" "python3 is required but not installed."
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
        0) die_with "$EXIT_NO_IPOD" \
               "No mounted iPod found. Plug it in, or pass the mount point explicitly." ;;
        1) printf '%s' "${candidates[0]}" ;;
        *) err "Multiple iPods found; pass one explicitly:"
           printf '  %s\n' "${candidates[@]}" >&2
           leave "$EXIT_SEVERAL_IPODS" ;;
    esac
}

# Refuse to operate on anything that is not recognisably an iPod shuffle.
#
# This is the guard that stands between a typo and an rm -rf on the wrong
# volume, so it checks structure rather than trusting the path it was handed.
assert_shuffle() {
    local ipod="$1"

    # An explicitly named path with no iPod at it is the same answer as
    # autodetection finding none, and a caller reacts to it the same way, so
    # it gets the same code. A volume that is an iPod but laid out wrongly is
    # not: nothing about plugging a different one in would help.
    [[ -d "$ipod" ]] || die_with "$EXIT_NO_IPOD" "Not a directory: $ipod"
    [[ -d "$ipod/iPod_Control" ]] \
        || die_with "$EXIT_NO_IPOD" "No iPod_Control in $ipod - that is not an iPod."
    [[ -d "$ipod/iPod_Control/iTunes" ]]  || die "No iPod_Control/iTunes in $ipod - unexpected layout."

    # A shuffle has no display, so it ships the Speakable prompts that the
    # screen-based iPods do not. Absence means this is probably a nano/classic,
    # where these scripts would build a database the firmware cannot read.
    if [[ ! -d "$ipod/iPod_Control/Speakable" ]]; then
        warn "No iPod_Control/Speakable directory found."
        warn "This may not be a shuffle. These scripts only support the shuffle 3G/4G."
        # Said out loud rather than left as a bare exit: bash prints a read
        # prompt only to a terminal, so an unattended run that declines here
        # by reaching end of input would otherwise stop with nothing but the
        # warnings above to say why.
        confirm "Continue anyway?" || die_with "$EXIT_DECLINED" "Aborted."
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
# udisksctl is the friendlier front end and is used when present, but minimal
# systems ship gdbus without it, so both paths have to work. Either way the
# request reaches the same daemon and the same polkit check, which grants
# removable media to the logged-in user without a password.
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
        die_with "$EXIT_MISSING_DEPENDENCY" \
            "Neither udisksctl nor gdbus found; cannot unmount."
    fi
}

ipod_mount() {
    local dev="$1"
    if command -v udisksctl >/dev/null 2>&1; then
        udisksctl mount -b "$dev"
    elif command -v gdbus >/dev/null 2>&1; then
        udisks_method "$dev" Mount
    else
        die_with "$EXIT_MISSING_DEPENDENCY" \
            "Neither udisksctl nor gdbus found; cannot mount."
    fi
}

# Shared default for prompt handling. Scripts that expose --yes set this after
# parsing; keeping the default beside confirm() also protects callers without
# that flag under set -u.
ASSUME_YES=0

confirm() {
    local prompt="$1" reply

    # --yes covers every prompt, including assert_shuffle()'s device-type
    # warning.
    if (( ASSUME_YES )); then
        info "$prompt yes (--yes)" >&2
        return 0
    fi

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
        command -v python3 >/dev/null \
            || die_with "$EXIT_MISSING_DEPENDENCY" "python3 not found."
        printf 'python3'
    fi
}

require_db_tool() {
    [[ -f "$DB_TOOL" ]] || die_with "$EXIT_MISSING_DEPENDENCY" \
        "Database tool missing at $DB_TOOL - run ./install.sh first."
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
        die_with "$EXIT_MISSING_DEPENDENCY" \
            "yt-dlp not found - run ./install.sh, or install it with 'pipx install yt-dlp'."
    fi
}

# Whether a yt-dlp understands an option.
#
# Distribution packages go stale quickly, and an old one accepts the flags it
# knows and dies on the rest, so anything recent has to be probed for rather
# than assumed. Takes the executable and the option, e.g. --js-runtimes.
yt_dlp_supports() {
    local help
    help="$("$1" --help 2>/dev/null)" || return 1
    [[ "$help" == *"$2"* ]]
}

_version_at_least() {
    local major=$((10#$1)) minor=$((10#$2)) patch=$((10#$3))
    local floor_major=$4 floor_minor=$5 floor_patch=$6
    (( major > floor_major
        || (major == floor_major && minor > floor_minor)
        || (major == floor_major && minor == floor_minor && patch >= floor_patch) ))
}

_version_at_most() {
    local major=$((10#$1)) minor=$((10#$2)) patch=$((10#$3))
    local ceiling_major=$4 ceiling_minor=$5 ceiling_patch=$6
    (( major < ceiling_major
        || (major == ceiling_major && minor < ceiling_minor)
        || (major == ceiling_major && minor == ceiling_minor && patch <= ceiling_patch) ))
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
# Probing for whichever supported runtime exists follows find_gui_python: ask
# what the machine actually has rather than hardcode one distribution's answer.
# deno is first because it is what yt-dlp itself defaults to and tests against.
js_runtime() {
    local candidate version major minor patch
    for candidate in deno node bun; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        version="$("$candidate" --version 2>/dev/null)" || continue
        version="${version%%$'\n'*}"

        case "$candidate" in
            deno)
                [[ "$version" =~ ^deno[[:space:]]+v?([0-9]+)\.([0-9]+)\.([0-9]+)([[:space:]]|$) ]] \
                    || continue
                major="${BASH_REMATCH[1]}"
                minor="${BASH_REMATCH[2]}"
                patch="${BASH_REMATCH[3]}"
                _version_at_least "$major" "$minor" "$patch" 2 3 0 || continue
                ;;
            node)
                [[ "$version" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || continue
                major="${BASH_REMATCH[1]}"
                minor="${BASH_REMATCH[2]}"
                patch="${BASH_REMATCH[3]}"
                _version_at_least "$major" "$minor" "$patch" 22 0 0 || continue
                ;;
            bun)
                [[ "$version" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || continue
                major="${BASH_REMATCH[1]}"
                minor="${BASH_REMATCH[2]}"
                patch="${BASH_REMATCH[3]}"
                _version_at_least "$major" "$minor" "$patch" 1 2 11 || continue
                _version_at_most "$major" "$minor" "$patch" 1 3 14 || continue
                ;;
        esac

        printf '%s' "$candidate"
        return 0
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

# Whether this machine can play audio through GStreamer.
#
# The GUI previews a track on the computer's own speakers with playbin3, which
# needs the GStreamer introspection typelib and the plugins that decode audio
# and reach a sound card. Those are separate packages from the GTK4 bindings,
# so a machine that runs the window perfectly well can still have no playback.
#
# Deliberately a second function rather than another clause inside
# find_gui_python: that one decides whether the GUI can start at all, and
# folding this into it would take the whole window away to withhold one
# optional feature of it. Probed through the interpreter find_gui_python picks,
# because that is the one the GUI will import from.
#
# Elements rather than the module alone: importing Gst succeeds with no plugins
# installed at all, and the failure then arrives as silence on play rather than
# as a missing dependency. Returns 0 when preview playback will work.
gst_available() {
    local python
    python="$(find_gui_python)" || return 1
    "$python" - <<'GST_PROBE' >/dev/null 2>&1
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)
for element in ("playbin3", "autoaudiosink"):
    if Gst.ElementFactory.make(element, None) is None:
        raise SystemExit(1)
GST_PROBE
}

# Where the playlist and voiceover options of the last sync are remembered.
#
# The database is regenerated from scratch every time, so any rebuild that does
# not know about them silently discards the playlists an earlier run created.
# Removing a track rebuilds too, which is why this lives here rather than in
# ipod-sync.sh alone.
sync_options_file() {
    printf '%s/iPod_Control/.sync-options' "${1%/}"
}

# Load the saved options into the named array, or leave it empty when none were
# saved.
read_sync_options() {
    local file
    local -n options="$2"

    options=()
    file="$(sync_options_file "$1")"
    if [[ ! -e "$file" && ! -L "$file" ]]; then
        return 0
    fi
    [[ -f "$file" ]] \
        || die "Could not read saved playlist and voiceover options from the iPod."
    # ShellCheck cannot see that the caller's array is used through a nameref.
    # shellcheck disable=SC2034
    mapfile -t options < "$file" \
        || die "Could not read saved playlist and voiceover options from the iPod."
}

# Rebuild iTunesSD, the only database the shuffle firmware actually reads.
# Everything on the device is invisible to the player until this runs.
rebuild_database() {
    local ipod="$1"
    shift
    require_db_tool
    info "Rebuilding iTunesSD database"
    # Named on the stream from here rather than from each script: all three
    # rebuild, and the rebuild is the part of a short run that takes the time,
    # so a bar with nothing to say during it looks stalled.
    progress_stage rebuild start
    "$(db_python)" "$DB_TOOL" "$@" "$ipod"
    progress_stage rebuild 'done'
}
