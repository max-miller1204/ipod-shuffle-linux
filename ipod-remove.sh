#!/usr/bin/env bash
#
# Remove tracks or playlists from an iPod shuffle 4G and rebuild its database.
#
# Usage: ./ipod-remove.sh [options] <track> [more-tracks...]
#        ./ipod-remove.sh --playlist <name> [more-names...]
#
# See README.md for the full workflow.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

IPOD=""
EJECT=0
LIST=0
JSON=0

usage() {
    cat <<'EOF'
Usage: ./ipod-remove.sh [options] <track> [more-tracks...]
       ./ipod-remove.sh --playlist <name> [more-names...]

Deletes tracks from iPod_Control/Music/ and rebuilds the iTunesSD database, so
the player forgets them rather than listing songs it can no longer play.

Tracks are named by their path under iPod_Control/Music, which --list prints.
A folder may be given instead of a file, which removes everything in it.

With --playlist the arguments are playlist names instead: the named playlist
files are deleted from the top of the iPod and the database is rebuilt. The
songs they listed stay on the device.

Options:
  -i, --ipod PATH   iPod mount point (default: autodetect)
  -l, --list        Print what is on the iPod and exit
  -j, --json        With --list, report the whole device as JSON instead of
                    track paths: where it is mounted, what it calls itself,
                    its storage, its tracks, its playlists and which of them
                    it can say out loud, and the options the last sync saved
  -P, --playlist    Treat the arguments as playlist names, not tracks
  -y, --yes         Answer yes to every prompt
  -e, --eject       Unmount the iPod when finished
  -h, --help        Show this message

Examples:
  ./ipod-remove.sh --list
  ./ipod-remove.sh --list --json
  ./ipod-remove.sh 'Road Trip/Disc 1/01 - Highway.mp3'
  ./ipod-remove.sh --eject 'Road Trip'
  ./ipod-remove.sh --playlist twizzy

The playlist and voiceover options saved by the last sync are reused, so the
rebuild keeps the playlists already on the device. Playlist files stored at
the top of the iPod are updated to drop the removed tracks, and one that
loses every track is removed with them.

To empty the iPod completely, use ./ipod-wipe.sh instead: it clears the stale
iTunes state as well, and can back the music up first.

Exit codes: 3 no iPod, 4 several iPods, 5 the iPod stopped answering, 6 a
missing dependency, 7 a declined prompt. Anything else that failed is 1.
EOF
}

PLAYLIST_MODE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod) IPOD="$2"; shift 2 ;;
        -l|--list) LIST=1; shift ;;
        -j|--json) JSON=1; shift ;;
        -P|--playlist) PLAYLIST_MODE=1; shift ;;
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

(( JSON == 0 || LIST )) || die "--json only reports; add --list."

IPOD="$(find_ipod "$IPOD")"
assert_shuffle "$IPOD"
watch_device "$IPOD"

MUSIC_DIR="$IPOD/iPod_Control/Music"
[[ -d "$MUSIC_DIR" ]] || die "No iPod_Control/Music in $IPOD - nothing to remove."

# Resolved once, because containment is checked against it and a symlinked or
# relative mount point would otherwise let a path escape the comparison.
MUSIC_REAL="$(readlink -f -- "$MUSIC_DIR")"
readonly MUSIC_REAL
mapfile -d '' -t ROOT_PLAYLISTS < <(root_playlist_files "$IPOD")

resolve_root_playlist() {
    local requested="$1" wanted_extension list filename extension stem

    RESOLVED_PLAYLIST=""
    for wanted_extension in m3u pls; do
        for list in "${ROOT_PLAYLISTS[@]}"; do
            filename="${list##*/}"
            case "$filename" in
                *.[mM]3[uU]) extension="m3u" ;;
                *.[pP][lL][sS]) extension="pls" ;;
                *) continue ;;
            esac
            [[ "$extension" == "$wanted_extension" ]] || continue
            stem="${filename%.*}"
            if [[ "$stem" == "$requested" ]]; then
                RESOLVED_PLAYLIST="$list"
                return 0
            fi
        done
    done
    return 1
}

# Nothing but the track paths on stdout, so the output can be fed straight
# back in as arguments. --json widens that to everything the device says about
# itself, and keeps the same promise: one document or none, never a partial
# one that reads as a definite answer.
if (( LIST )); then
    (( PLAYLIST_MODE == 0 )) || die "--list and --playlist cannot be combined."
    (( $# == 0 )) || die "--list takes no track paths."
    if (( JSON )); then
        device_report "$IPOD"
    else
        find "$MUSIC_REAL" -type f -printf '%P\n' | sort
    fi
    exit 0
fi

[[ $# -gt 0 ]] || { usage; exit 1; }

info "iPod: $IPOD"

# Resolve every argument before deleting anything, so a typo in the last one
# does not leave the first half of the request already carried out.
declare -a TARGETS=()
if (( PLAYLIST_MODE )); then
    # Playlists live at the volume root, so a name is a bare
    # filename by construction; anything with a separator is refused before
    # it can point somewhere else.
    for arg in "$@"; do
        [[ -n "$arg" ]] || die "Empty playlist name."
        name="$arg"
        [[ "$name" != */* && "$name" != *\\* ]] \
            || die "Not a playlist name: $arg"
        if resolve_root_playlist "$name"; then
            target="$RESOLVED_PLAYLIST"
        else
            fallback_name="$name"
            case "$fallback_name" in
                *.[mM]3[uU]|*.[pP][lL][sS]) fallback_name="${fallback_name%????}" ;;
            esac
            if [[ "$fallback_name" != "$name" ]]; then
                if resolve_root_playlist "$fallback_name"; then
                    target="$RESOLVED_PLAYLIST"
                    name="$fallback_name"
                else
                    target=""
                fi
            else
                target=""
            fi
        fi
        if [[ -z "$target" ]]; then
            err "No playlist called '$name' on this iPod."
            if (( ${#ROOT_PLAYLISTS[@]} > 0 )); then
                err "It has:"
                for list in "${ROOT_PLAYLISTS[@]}"; do
                    available_name="$(basename -- "$list")"
                    printf '  %s\n' "${available_name%.*}" >&2
                done
            else
                err "It has no playlists."
            fi
            exit 1
        fi
        TARGETS+=("$target")
    done
else
    for arg in "$@"; do
        [[ -n "$arg" ]] || die "Empty track path."

        if [[ "$arg" == /* ]]; then
            target="$arg"
        else
            target="$MUSIC_REAL/$arg"
        fi
        # -m rather than -e: a path that does not exist has to be normalised
        # too, or "../../../etc" would be rejected only by luck.
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
fi

removing=0
if (( ! PLAYLIST_MODE )); then
    for target in "${TARGETS[@]}"; do
        removing=$(( removing + $(find "$target" -type f | wc -l) ))
    done
fi

require_db_tool
db_python >/dev/null

declare -a DB_ARGS=()
read_sync_options "$IPOD" DB_ARGS
if (( ${#DB_ARGS[@]} > 0 )); then
    info "Reusing saved options: ${DB_ARGS[*]}"
fi

if (( ! ASSUME_YES )); then
    if (( PLAYLIST_MODE )); then
        info "About to remove ${#TARGETS[@]} playlist(s):"
        for target in "${TARGETS[@]}"; do
            playlist_name="$(basename -- "$target")"
            printf '  %s\n' "${playlist_name%.*}"
        done
        info "The songs they list stay on the iPod."
    else
        info "About to remove $removing track(s):"
        printf '  %s\n' "${TARGETS[@]#"$MUSIC_REAL"/}"
        warn "The iPod holds the only copy unless you have these elsewhere."
    fi
    confirm "Remove them?" || die_with "$EXIT_DECLINED" "Aborted."
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

if (( PLAYLIST_MODE )); then
    rm -f -- "${TARGETS[@]}"
    info "Removed ${#TARGETS[@]} playlist(s); the songs they listed stay on the iPod."
else
    for target in "${TARGETS[@]}"; do
        rm -rf -- "$target"
        prune_empty_dirs "$(dirname -- "$target")"
    done
    info "Removed $removing track(s)"
fi

# A removed track must also leave the playlists that name it, or they keep
# offering a song the player can no longer find. Only the playlist files at
# the volume root are rewritten, and within them only the entries ipod-sync.sh
# writes; lines kept by hand in some other form pass through untouched.
prune_playlists() {
    local list line dropped kept_tracks playlist_name
    local -a lists=() kept=()

    lists=("${ROOT_PLAYLISTS[@]}")

    for list in "${lists[@]}"; do
        kept=()
        kept_tracks=0
        dropped=0
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line%$'\r'}"
            if [[ "$line" != iPod_Control/Music/* ]]; then
                kept+=("$line")
                [[ -z "$line" || "$line" == \#* ]] || kept_tracks=$((kept_tracks + 1))
                continue
            fi
            if [[ -e "$IPOD/$line" ]]; then
                kept+=("$line")
                kept_tracks=$((kept_tracks + 1))
            else
                dropped=$((dropped + 1))
            fi
        done < "$list"

        (( dropped > 0 )) || continue
        playlist_name="$(basename -- "$list")"
        playlist_name="${playlist_name%.*}"
        if (( kept_tracks == 0 )); then
            rm -f -- "$list"
            info "Removed playlist '$playlist_name': every track it listed is gone"
        else
            atomic_replace_lines "$list" "${kept[@]}"
            info "Playlist '$playlist_name': dropped $dropped removed track(s)"
        fi
    done
}
(( PLAYLIST_MODE )) || prune_playlists

# The device only forgets a track or playlist once the database no longer
# lists it. Until then the player still offers it and stops dead when it
# tries to play it.
rebuild_database "$IPOD" "${DB_ARGS[@]+"${DB_ARGS[@]}"}"

if (( ! PLAYLIST_MODE )); then
    total="$(find "$MUSIC_REAL" -type f | wc -l)"
    info "iPod now holds $total track(s)"
fi

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
