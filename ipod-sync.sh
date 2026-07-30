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
REBUILD_ONLY=0
declare -a DB_ARGS=()

usage() {
    cat <<'EOF'
Usage: ./ipod-sync.sh [options] <music-dir> [more-dirs...]

Copies audio into iPod_Control/Music/ and rebuilds the iTunesSD database.

Options:
  -i, --ipod PATH        iPod mount point (default: autodetect)
  -c, --clear            Remove existing tracks before copying
  -e, --eject            Unmount the iPod when finished
  -r, --rebuild-only     Rebuild the database without copying anything
  -n, --forget-options   Ignore the saved playlist and voiceover options,
                         building a plain database with neither
  -h, --help             Show this message

Voiceover (the shuffle has no screen, so this is how you hear what is playing):
  -t, --voiceover        Speak track names
  -p, --playlist-voiceover
                         Speak playlist names

Playlists:
  -d, --dir-playlists[=DEPTH]
                         One playlist per folder. DEPTH limits how deep to
                         go: 1=artist, 2=album, omitted=unlimited.
      --id3-playlists[=TEMPLATE]
                         Group tracks by tag. TEMPLATE defaults to
                         '{artist}'; '{genre}' and '{artist} - {album}'
                         also work. Requires mutagen.

Examples:
  ./ipod-sync.sh ~/Music/roadtrip
  ./ipod-sync.sh --clear --eject ~/Music/albums/*/
  ./ipod-sync.sh --rebuild-only
  ./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music
  ./ipod-sync.sh --id3-playlists='{genre}' --playlist-voiceover ~/Music
EOF
}

PLAYLISTS=0
PLAYLIST_VOICEOVER=0
FORGET_OPTIONS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod)         IPOD="$2"; shift 2 ;;
        -c|--clear)        CLEAR=1; shift ;;
        -e|--eject)        EJECT=1; shift ;;
        -r|--rebuild-only) REBUILD_ONLY=1; shift ;;
        -n|--forget-options) FORGET_OPTIONS=1; shift ;;
        -t|--voiceover)    DB_ARGS+=("--track-voiceover"); shift ;;
        -p|--playlist-voiceover)
                           DB_ARGS+=("--playlist-voiceover")
                           PLAYLIST_VOICEOVER=1; shift ;;
        # These two take an optional value upstream, and argparse will happily
        # swallow the following argument when none is given. Left last on the
        # command line that means eating the iPod path itself, so a value is
        # always supplied explicitly. -1 and {artist} are upstream's defaults.
        -d|--dir-playlists)
                           DB_ARGS+=("--auto-dir-playlists" "-1")
                           PLAYLISTS=1; shift ;;
        --dir-playlists=*)
                           DB_ARGS+=("--auto-dir-playlists" "${1#*=}")
                           PLAYLISTS=1; shift ;;
        --id3-playlists)
                           DB_ARGS+=("--auto-id3-playlists" "{artist}")
                           PLAYLISTS=1; shift ;;
        --id3-playlists=*)
                           DB_ARGS+=("--auto-id3-playlists" "${1#*=}")
                           PLAYLISTS=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        -*)                die "Unknown option: $1 (try --help)" ;;
        *)                 break ;;
    esac
done

# A playlist you cannot hear the name of is a playlist you cannot choose,
# because the device has no display to show you which one you landed on.
if (( PLAYLISTS && ! PLAYLIST_VOICEOVER )); then
    warn "Playlists without --playlist-voiceover will be unnamed on the device."
    warn "With no screen, there is no way to tell them apart. Consider adding -p."
fi

if (( REBUILD_ONLY )); then
    (( $# == 0 )) || die "--rebuild-only takes no source directories."
else
    [[ $# -gt 0 ]] || { usage; exit 1; }
fi

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

if (( ! REBUILD_ONLY )); then
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
fi

# The database is regenerated from scratch on every run, so a rebuild that
# omits the playlist and voiceover flags silently discards whatever the last
# run created. Remembering them on the device makes a bare rebuild safe, which
# matters most for the GUI's Rebuild button after the app has been restarted.
OPTIONS_FILE="$IPOD/iPod_Control/.sync-options"

if (( ${#DB_ARGS[@]} == 0 && ! FORGET_OPTIONS )) && [[ -f "$OPTIONS_FILE" ]]; then
    mapfile -t DB_ARGS < "$OPTIONS_FILE"
    if (( ${#DB_ARGS[@]} > 0 )); then
        info "Reusing saved options: ${DB_ARGS[*]}"
    fi
fi

rebuild_database "$IPOD" "${DB_ARGS[@]+"${DB_ARGS[@]}"}"

if (( ${#DB_ARGS[@]} > 0 )); then
    options_tmp="$(mktemp "${OPTIONS_FILE}.tmp.XXXXXX")" \
        || die "Could not create a temporary options file on the iPod."
    trap 'rm -f -- "${options_tmp:-}"' EXIT
    printf '%s\n' "${DB_ARGS[@]}" > "$options_tmp" \
        || die "Could not write playlist and voiceover options to the iPod."
    mv -f -- "$options_tmp" "$OPTIONS_FILE" \
        || die "Could not save playlist and voiceover options to the iPod."
    options_tmp=""
    trap - EXIT
else
    rm -f -- "$OPTIONS_FILE" \
        || die "Could not clear playlist and voiceover options from the iPod."
fi

total="$(find "$MUSIC_DIR" -type f | wc -l)"
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
