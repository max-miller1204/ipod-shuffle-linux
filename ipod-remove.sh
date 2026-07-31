#!/usr/bin/env bash
#
# Remove individual tracks from an iPod shuffle 4G and rebuild its database.
#
# Usage: ./ipod-remove.sh [options] <track> [more-tracks...]
#
# See README.md for the full workflow.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

IPOD=""
ASSUME_YES=0
EJECT=0
LIST=0

usage() {
    cat <<'EOF'
Usage: ./ipod-remove.sh [options] <track> [more-tracks...]

Deletes tracks from iPod_Control/Music/ and rebuilds the iTunesSD database, so
the player forgets them rather than listing songs it can no longer play.

Tracks are named by their path under iPod_Control/Music, which --list prints.
A folder may be given instead of a file, which removes everything in it.

Options:
  -i, --ipod PATH   iPod mount point (default: autodetect)
  -l, --list        Print what is on the iPod and exit
  -y, --yes         Skip the confirmation prompt
  -e, --eject       Unmount the iPod when finished
  -h, --help        Show this message

Examples:
  ./ipod-remove.sh --list
  ./ipod-remove.sh 'Road Trip/Disc 1/01 - Highway.mp3'
  ./ipod-remove.sh --eject 'Road Trip'

The playlist and voiceover options saved by the last sync are reused, so the
rebuild keeps the playlists already on the device.

To empty the iPod completely, use ./ipod-wipe.sh instead: it clears the stale
iTunes state as well, and can back the music up first.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod) IPOD="$2"; shift 2 ;;
        -l|--list) LIST=1; shift ;;
        -y|--yes)  ASSUME_YES=1; shift ;;
        -e|--eject) EJECT=1; shift ;;
        -h|--help) usage; exit 0 ;;
        # Everything after this is a track path, however much it looks like an
        # option, because track names are whatever the tags happened to say.
        --)        shift; break ;;
        -*)        die "Unknown option: $1 (try --help)" ;;
        *)         break ;;
    esac
done

IPOD="$(find_ipod "$IPOD")"
assert_shuffle "$IPOD"

MUSIC_DIR="$IPOD/iPod_Control/Music"
[[ -d "$MUSIC_DIR" ]] || die "No iPod_Control/Music in $IPOD - nothing to remove."

# Resolved once, because containment is checked against it and a symlinked or
# relative mount point would otherwise let a path escape the comparison.
MUSIC_REAL="$(readlink -f -- "$MUSIC_DIR")"
readonly MUSIC_REAL

# Nothing but the track paths on stdout, so the output can be fed straight
# back in as arguments.
if (( LIST )); then
    (( $# == 0 )) || die "--list takes no track paths."
    find "$MUSIC_REAL" -type f -printf '%P\n' | sort
    exit 0
fi

[[ $# -gt 0 ]] || { usage; exit 1; }

info "iPod: $IPOD"

# Resolve every argument before deleting anything, so a typo in the last one
# does not leave the first half of the request already carried out.
declare -a TARGETS=()
for arg in "$@"; do
    [[ -n "$arg" ]] || die "Empty track path."

    if [[ "$arg" == /* ]]; then
        target="$arg"
    else
        target="$MUSIC_REAL/$arg"
    fi
    # -m rather than -e: a path that does not exist has to be normalised too,
    # or "../../../etc" would be rejected only by luck.
    target="$(readlink -m -- "$target")"

    if [[ "$target" == "$MUSIC_REAL" ]]; then
        die "That is the whole library. Use ./ipod-wipe.sh to empty the iPod."
    fi
    [[ "$target" == "$MUSIC_REAL"/* ]] \
        || die "Not a path on this iPod: $arg"
    [[ -e "$target" ]] \
        || die "No such track on the iPod: $arg (try --list)"

    TARGETS+=("$target")
done

removing=0
for target in "${TARGETS[@]}"; do
    removing=$(( removing + $(find "$target" -type f | wc -l) ))
done

if (( ! ASSUME_YES )); then
    info "About to remove $removing track(s):"
    printf '  %s\n' "${TARGETS[@]#"$MUSIC_REAL"/}"
    warn "The iPod holds the only copy unless you have these elsewhere."
    confirm "Remove them?" || die "Aborted."
fi

# Delete the folders a removal leaves empty, up to but not including the music
# root. They are not harmless: --dir-playlists builds one playlist per folder,
# so an empty one becomes a playlist that plays nothing, on a device with no
# screen to show that it is empty.
prune_empty_dirs() {
    local dir="$1"
    while [[ "$dir" == "$MUSIC_REAL"/* ]]; do
        rmdir -- "$dir" 2>/dev/null || break
        dir="$(dirname -- "$dir")"
    done
}

for target in "${TARGETS[@]}"; do
    rm -rf -- "$target"
    prune_empty_dirs "$(dirname -- "$target")"
done
info "Removed $removing track(s)"

# The device only forgets a track once the database no longer lists it. Until
# then the player still offers it and stops dead when it tries to play it.
declare -a DB_ARGS=()
mapfile -t DB_ARGS < <(read_sync_options "$IPOD")
if (( ${#DB_ARGS[@]} > 0 )); then
    info "Reusing saved options: ${DB_ARGS[*]}"
fi

rebuild_database "$IPOD" "${DB_ARGS[@]+"${DB_ARGS[@]}"}"

total="$(find "$MUSIC_REAL" -type f | wc -l)"
info "iPod now holds $total track(s)"

if (( EJECT )); then
    dev="$(ipod_device "$IPOD")"
    info "Unmounting $dev"
    sync
    ipod_unmount "$dev"
    info "Safe to unplug."
else
    warn "Unmount before unplugging, or the database may be corrupted:"
    warn "  ./ipod-sync.sh --rebuild-only --eject"
fi
