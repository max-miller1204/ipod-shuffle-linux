#!/usr/bin/env bash
#
# Copy music onto an iPod shuffle 4G and rebuild its database.
#
# Usage: ./ipod-sync.sh [options] <music-dir> [more-dirs...]
#
# See README.md for the full workflow.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

# Formats the shuffle firmware can decode. Anything else is copied by neither
# this script nor the database builder, so it would sit on the device unplayable.
readonly SUPPORTED_EXT="mp3|m4a|m4b|m4p|aa|wav"

IPOD=""
EJECT=0
CLEAR=0
declare -a DB_ARGS=()

usage() {
    cat <<'EOF'
Usage: ./ipod-sync.sh [options] <music-dir> [more-dirs...]

Copies audio into iPod_Control/Music/ and rebuilds the iTunesSD database.

Options:
  -i, --ipod PATH     iPod mount point (default: autodetect)
  -c, --clear         Remove existing tracks before copying
  -e, --eject         Unmount the iPod when finished
  -t, --voiceover     Generate spoken track names (needs a TTS engine)
  -d, --playlists     Generate one playlist per source folder
  -h, --help          Show this message

Examples:
  ./ipod-sync.sh ~/Music/roadtrip
  ./ipod-sync.sh --clear --eject ~/Music/albums/*/
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod)      IPOD="$2"; shift 2 ;;
        -c|--clear)     CLEAR=1; shift ;;
        -e|--eject)     EJECT=1; shift ;;
        -t|--voiceover) DB_ARGS+=("--track-voiceover"); shift ;;
        -d|--playlists) DB_ARGS+=("--auto-dir-playlists"); shift ;;
        -h|--help)      usage; exit 0 ;;
        -*)             die "Unknown option: $1 (try --help)" ;;
        *)              break ;;
    esac
done

[[ $# -gt 0 ]] || { usage; exit 1; }

IPOD="$(find_ipod "$IPOD")"
assert_shuffle "$IPOD"
info "iPod: $IPOD"

MUSIC_DIR="$IPOD/iPod_Control/Music"
mkdir -p "$MUSIC_DIR"

if (( CLEAR )); then
    existing="$(find "$MUSIC_DIR" -type f | wc -l)"
    if (( existing > 0 )); then
        confirm "Delete $existing existing track(s) from the iPod?" \
            || die "Aborted."
        rm -rf "${MUSIC_DIR:?}"/*
        info "Removed $existing track(s)"
    fi
fi

copied=0
skipped=0
duplicates=0
for src in "$@"; do
    [[ -e "$src" ]] || { warn "No such path, skipping: $src"; continue; }
    src="${src%/}"

    # Mirror the source tree under a folder named after it, rather than
    # flattening. Two albums each containing a track called 01.mp3 would
    # otherwise overwrite one another, and --playlists needs the structure
    # to have something to group by.
    dest="$MUSIC_DIR/$(basename "$src")"

    while IFS= read -r -d '' f; do
        if [[ ! "${f,,}" =~ \.(${SUPPORTED_EXT})$ ]]; then
            skipped=$((skipped + 1))
            continue
        fi

        rel="${f#"$src"/}"
        target="$dest/$rel"

        if [[ -e "$target" ]]; then
            duplicates=$((duplicates + 1))
            continue
        fi

        mkdir -p "$(dirname "$target")"
        cp "$f" "$target"
        copied=$((copied + 1))
    done < <(find "$src" -type f -print0)
done

info "Copied $copied file(s)"
if (( duplicates > 0 )); then
    info "Skipped $duplicates file(s) already on the iPod"
fi
if (( skipped > 0 )); then
    warn "Skipped $skipped unsupported file(s). Convert them first, for example:"
    warn "  ffmpeg -i input.flac -c:a aac -b:a 256k output.m4a"
fi

if (( copied == 0 && CLEAR == 0 )); then
    warn "Nothing new copied; rebuilding the database anyway."
fi

rebuild_database "$IPOD" "${DB_ARGS[@]+"${DB_ARGS[@]}"}"

total="$(find "$MUSIC_DIR" -type f | wc -l)"
info "iPod now holds $total track(s)"

if (( EJECT )); then
    dev="$(ipod_device "$IPOD")"
    info "Unmounting $dev"
    sync
    udisksctl unmount -b "$dev"
    info "Safe to unplug."
else
    warn "Unmount before unplugging, or the database may be corrupted:"
    warn "  udisksctl unmount -b $(ipod_device "$IPOD")"
fi
