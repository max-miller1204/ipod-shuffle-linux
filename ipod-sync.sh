#!/usr/bin/env bash
#
# Copy music or playlists onto an iPod shuffle 4G and rebuild its database.
#
# Usage: ./ipod-sync.sh [options] <music-dir-file-or-playlist> [more...]
#
# See README.md for the full workflow.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

IPOD=""
EJECT=0
CLEAR=0
REBUILD_ONLY=0
PROGRESS_TARGET=""
DRY_RUN=0
EXPECT_DEVICE=""
CONFIRM_TOKEN=""
declare -a DB_ARGS=()

# Declared here rather than beside the copy that keeps them, because the
# result event is written from wherever the run happens to end - including
# from a failure long before the copying starts.
copied=0
skipped=0
duplicates=0
broken=0

usage() {
    cat <<'EOF'
Usage: ./ipod-sync.sh [options] <music-dir-file-or-playlist> [more...]

Copies audio into iPod_Control/Music/ and rebuilds the iTunesSD database.

A folder is mirrored under a folder of the same name. A single file is copied
into a folder named after the one it came from, so syncing an album and
syncing one track out of it put that track in the same place.

A .m3u or .pls argument becomes a playlist on the device: the tracks it
references are copied as above, and a rewritten copy of the list is stored at
the top of the iPod, where every later rebuild picks it up automatically. The
filename becomes the playlist's spoken name.

Options:
  -i, --ipod PATH        iPod mount point (default: autodetect)
  -c, --clear            Remove existing tracks before copying
  -e, --eject            Unmount the iPod when finished
  -r, --rebuild-only     Rebuild the database without copying anything
  -n, --forget-options   Ignore the saved playlist and voiceover options,
                         building a plain database with neither
  -y, --yes              Answer yes to every prompt
      --dry-run          Print the exact operation plan as JSON and change nothing
      --expect-device ID
                         Refuse unless the mounted iPod has this identity
      --confirm-token TOKEN
                         Approve the exact non-interactive plan from --dry-run
      --progress-json[=FD]
                         Report progress as one JSON object per line on
                         descriptor FD (default 3), which the caller opens.
                         The output below is unchanged.
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
  ./ipod-sync.sh ~/Music/roadtrip/01-highway.mp3
  ./ipod-sync.sh --clear --eject ~/Music/albums/*/
  ./ipod-sync.sh --rebuild-only
  ./ipod-sync.sh --progress-json ~/Music/roadtrip 3>progress.ndjson
  ./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music
  ./ipod-sync.sh --id3-playlists='{genre}' --playlist-voiceover ~/Music
  ./ipod-sync.sh --playlist-voiceover ~/Music/mixtape.m3u

Exit codes: 3 no iPod, 4 several iPods, 5 the iPod stopped answering, 6 a
missing dependency, 7 a declined prompt. Anything else that failed is 1.
EOF
}

PLAYLISTS=0
FORGET_OPTIONS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod)         IPOD="$2"; shift 2 ;;
        -c|--clear)        CLEAR=1; shift ;;
        -e|--eject)        EJECT=1; shift ;;
        -r|--rebuild-only) REBUILD_ONLY=1; shift ;;
        -n|--forget-options) FORGET_OPTIONS=1; shift ;;
        -y|--yes)          ASSUME_YES=1; shift ;;
        --dry-run)          DRY_RUN=1; shift ;;
        --expect-device)    EXPECT_DEVICE="$2"; shift 2 ;;
        --confirm-token)    CONFIRM_TOKEN="$2"; shift 2 ;;
        --progress-json|--progress-json=*)
                           progress_flag "$1"; shift ;;
        -t|--voiceover)    DB_ARGS+=("--track-voiceover"); shift ;;
        -p|--playlist-voiceover)
                           DB_ARGS+=("--playlist-voiceover"); shift ;;
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
        # Everything after this is a path, however much it looks like an
        # option. Track names come from tags and from YouTube titles, and a
        # song called "-1" would otherwise be rejected as a bad flag.
        --)                shift; break ;;
        -*)                die "Unknown option: $1 (try --help)" ;;
        *)                 break ;;
    esac
done

# Playlist files among the sources count as asking for playlists, for the
# voiceover warning below.
for arg in "$@"; do
    if [[ "${arg,,}" =~ \.(m3u|pls)$ ]]; then
        PLAYLISTS=1
        break
    fi
done

if (( REBUILD_ONLY )); then
    (( $# == 0 )) || die "--rebuild-only takes no source directories."
else
    [[ $# -gt 0 ]] || { usage; exit 1; }
fi

# What the run did, for the last event on the progress stream. The track total
# is only known once the copy has finished, so it is reported when there is
# one rather than as a zero that would read as an empty iPod.
progress_result_fields() {
    printf '%s\n' \
        copied "$copied" \
        duplicates "$duplicates" \
        unsupported "$skipped" \
        broken "$broken"
    [[ -z "${total:-}" ]] || printf '%s\n' tracks "$total"
}

# Opened before the iPod is looked for, so that a run with no iPod to work on
# still reports why on the stream the caller is reading.
progress_open sync "$PROGRESS_TARGET"
# From here on every failure leaves through leave(), which is what reports
# the run's last event: the search below runs in a command substitution and
# can fail before there is a device to watch.
watch_failures

IPOD="$(find_ipod "$IPOD")"
assert_shuffle "$IPOD"
watch_device "$IPOD"
progress_event device ipod "$IPOD"

MUSIC_DIR="$IPOD/iPod_Control/Music"
OPTIONS_FILE="$(sync_options_file "$IPOD")"

if (( ${#DB_ARGS[@]} == 0 && ! FORGET_OPTIONS )); then
    read_sync_options "$IPOD" DB_ARGS
    if (( ${#DB_ARGS[@]} > 0 )); then
        info "Reusing saved options: ${DB_ARGS[*]}"
    fi
fi

# A playlist you cannot hear the name of is a playlist you cannot choose,
# because the device has no display to show you which one you landed on.
# Judged against the effective options rather than this command line alone: a
# playlist file synced while the saved options already speak playlist names
# needs no warning.
PLAYLIST_VOICEOVER=0
for arg in "${DB_ARGS[@]+"${DB_ARGS[@]}"}"; do
    case "$arg" in
        --playlist-voiceover) PLAYLIST_VOICEOVER=1 ;;
        --auto-dir-playlists|--auto-id3-playlists) PLAYLISTS=1 ;;
    esac
done
if (( PLAYLISTS && ! PLAYLIST_VOICEOVER )); then
    warn "Playlists without --playlist-voiceover will be unnamed on the device."
    warn "With no screen, there is no way to tell them apart. Consider adding -p."
fi

plan_existing=0
plan_playlists=0
if (( CLEAR )); then
    plan_existing="$(count_files_present "$MUSIC_DIR")"
    mapfile -d '' -t plan_root_playlists < <(root_playlist_files "$IPOD")
    plan_playlists=${#plan_root_playlists[@]}
fi
prepare_operation sync "$IPOD" "$DEVICE_WATCH_IDENTITY" "$CLEAR" \
    "$EXPECT_DEVICE" "$CONFIRM_TOKEN" "$DRY_RUN" \
    "clear=$CLEAR" \
    "eject=$EJECT" \
    "rebuild-only=$REBUILD_ONLY" \
    "forget-options=$FORGET_OPTIONS" \
    "existing-tracks=$plan_existing" \
    "existing-playlists=$plan_playlists" \
    "${DB_ARGS[@]}" \
    "$@"
info "iPod: $IPOD"

assert_watched_device
mkdir -p "$MUSIC_DIR"

if (( CLEAR )); then
    progress_stage clear start
    existing="$(count_files "$MUSIC_DIR")"
    mapfile -d '' -t stale_playlists < <(root_playlist_files "$IPOD")
    playlist_count=${#stale_playlists[@]}
    if (( existing > 0 || playlist_count > 0 )); then
        if (( existing > 0 && playlist_count > 0 )); then
            clear_prompt="Delete $existing existing track(s) and $playlist_count playlist(s) from the iPod?"
        elif (( existing > 0 )); then
            clear_prompt="Delete $existing existing track(s) from the iPod?"
        else
            clear_prompt="Delete $playlist_count playlist(s) from the iPod?"
        fi
        confirm "$clear_prompt" || die_with "$EXIT_DECLINED" "Aborted."
    fi
    assert_watched_device
    if (( existing > 0 )); then
        rm -rf "${MUSIC_DIR:?}"/*
        info "Removed $existing track(s)"
    fi
    # The playlists at the volume root reference the tracks just deleted, so
    # leaving them behind would rebuild playlists full of dead entries.
    if (( playlist_count > 0 )); then
        rm -f -- "${stale_playlists[@]}"
        info "Removed $playlist_count playlist(s)"
    fi
    progress_stage clear 'done'
fi

COPY_TARGET=""
declare -A PLAYLIST_TARGET_SOURCES=()
declare -A PLAYLIST_TARGET_NAMES=()

# Copy one track, unless it is a format the firmware cannot decode or is
# already on the device. The counters belong to the enclosing script.
copy_track() {
    local source="$1" target="$2"
    local checksum_line checksum size candidate
    local collision_index=2

    COPY_TARGET="$target"

    # Reported on the progress stream by everything below this line and by
    # nothing above it: a file the firmware cannot play was never counted into
    # what the run said it would do, so saying it was skipped would be counting
    # work nobody planned.
    if ! playable_name "$source"; then
        skipped=$((skipped + 1))
        return 0
    fi
    if [[ -e "$COPY_TARGET" ]] && cmp -s -- "$source" "$COPY_TARGET"; then
        duplicates=$((duplicates + 1))
        progress_file duplicate "$(basename -- "$source")"
        return 0
    fi
    if [[ -e "$COPY_TARGET" ]]; then
        checksum_line="$(cksum -- "$source")" \
            || die "Could not checksum track for a distinct destination: $source"
        checksum="${checksum_line%% *}"
        checksum_line="${checksum_line#* }"
        size="${checksum_line%% *}"
        candidate="$MUSIC_DIR/Collision-$checksum-$size/$(basename -- "$target")"
        while [[ -e "$candidate" ]] && ! cmp -s -- "$source" "$candidate"; do
            candidate="$MUSIC_DIR/Collision-$checksum-$size-$collision_index/$(basename -- "$target")"
            collision_index=$((collision_index + 1))
        done
        COPY_TARGET="$candidate"
        if [[ -e "$COPY_TARGET" ]]; then
            duplicates=$((duplicates + 1))
            progress_file duplicate "$(basename -- "$source")"
            return 0
        fi
        warn "Destination already holds a different track; copying this one as ${COPY_TARGET#"$MUSIC_DIR"/}."
    fi

    assert_watched_device
    mkdir -p "$(dirname "$COPY_TARGET")"
    cp "$source" "$COPY_TARGET"
    copied=$((copied + 1))
    # One line per file. A copy onto a shuffle runs at USB 2.0 speeds and can
    # take minutes, and until now neither the terminal nor the GUI had
    # anything to report until the whole thing had finished.
    printf '  + %s -> %s\n' \
        "$(basename -- "$source")" "${COPY_TARGET#"$MUSIC_DIR"/}"
    progress_file copied "$(basename -- "$source")" "${COPY_TARGET#"$MUSIC_DIR"/}"
}

# The folder a lone file argument lands in: the one it came from. Syncing an
# album and syncing a single track out of that album then put the track in the
# same place, and a file dropped straight at the music root would be invisible
# to --dir-playlists, which has only folders to group by.
file_dest_dir() {
    local parent
    parent="$(basename "$(dirname "$(readlink -f -- "$1")")")"
    if [[ -z "$parent" || "$parent" == "/" || "$parent" == "." ]]; then
        printf '%s' "$MUSIC_DIR"
    else
        printf '%s/%s' "$MUSIC_DIR" "$parent"
    fi
}

# Resolve a playlist file to the local files it references, NUL-separated and
# in playlist order. Relative entries are anchored at the playlist's folder,
# file:// URIs are decoded, stream URLs are dropped with a warning, and an
# entry written with Windows separators is retried with slashes before being
# passed through for the caller's missing-file warning.
playlist_entries() {
    command -v python3 >/dev/null || die "python3 is required but not installed."
    python3 - "$1" <<'PY'
import os
import re
import sys
import urllib.parse

playlist = sys.argv[1]
base = os.path.dirname(os.path.abspath(playlist))

with open(playlist, encoding="utf-8", errors="replace") as handle:
    lines = handle.read().splitlines()

if os.path.splitext(playlist)[1].lower() == ".pls":
    numbered = []
    for line in lines:
        key, separator, value = line.partition("=")
        match = re.fullmatch(r"[Ff]ile([0-9]+)", key.strip())
        if separator and match:
            numbered.append((int(match.group(1)), value.strip()))
    entries = [value for _, value in sorted(numbered)]
else:
    entries = [line.strip() for line in lines
               if line.strip() and not line.strip().startswith("#")]

for entry in entries:
    if entry.lower().startswith("file:"):
        parts = urllib.parse.urlparse(entry)
        if parts.netloc not in ("", "localhost"):
            print("warning: playlist entry is on another computer, skipped:",
                  entry, file=sys.stderr)
            continue
        entry = urllib.parse.unquote(parts.path)
    elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", entry):
        print("warning: playlist entry is a stream, skipped:",
              entry, file=sys.stderr)
        continue
    path = entry if os.path.isabs(entry) else os.path.join(base, entry)
    if not os.path.exists(path) and "\\" in entry:
        slashed = entry.replace("\\", "/")
        candidate = slashed if os.path.isabs(slashed) \
            else os.path.join(base, slashed)
        if os.path.exists(candidate):
            path = candidate
    sys.stdout.write(os.path.abspath(path) + "\0")
PY
}

# Copy a playlist's tracks and store a rewritten copy of the list at the
# volume root, where the database builder discovers playlist files on every
# rebuild. Entries are written relative to that root, so they survive the
# device mounting somewhere else next time.
sync_playlist() {
    local list="$1"
    local stem device_stem reservation_key trailing_underscores=""
    local target pls_target entry dest entries_file existing_target
    local added=0 unplayable=0 removed=0
    local -a lines=() entries=()

    stem="$(basename "$list")"
    stem="${stem%.*}"

    # The filename is also the spoken playlist name, so mangle it as little
    # as possible: only the characters FAT refuses outright.
    device_stem="$(printf '%s' "$stem" | LC_ALL=C tr '\001-\037\177' '_')"
    device_stem="${device_stem//[\\\/:*?\"<>|]/_}"
    while [[ "$device_stem" == *[.\ ] ]]; do
        device_stem="${device_stem%?}"
        trailing_underscores+="_"
    done
    device_stem+="$trailing_underscores"
    [[ -n "$device_stem" ]] || die "Playlist file has no usable name: $list"
    if [[ "$device_stem" != "$stem" ]]; then
        warn "Playlist name contains characters FAT rejects;" \
            "it will be called '$device_stem' on the device."
    fi
    target="$IPOD/$device_stem.m3u"
    pls_target="$IPOD/$device_stem.pls"
    reservation_key="$(python3 -c \
        'import sys
def simple_fold(char):
    folded = char.casefold()
    if len(folded) == 1:
        return folded
    lowered = char.lower()
    return lowered if len(lowered) == 1 else char
sys.stdout.write("".join(simple_fold(char) for char in sys.argv[1]))' \
        "$device_stem")" \
        || die "Could not case-fold playlist name: $device_stem"
    if [[ -n "${PLAYLIST_TARGET_SOURCES[$reservation_key]+present}" ]]; then
        warn "Playlist files '${PLAYLIST_TARGET_SOURCES[$reservation_key]}' and '$list' both become '${PLAYLIST_TARGET_NAMES[$reservation_key]}' on the device; skipped '$list'."
        progress_playlist skipped "$device_stem" 0
        return 0
    fi

    entries_file="$(mktemp -t ipod-playlist-entries.XXXXXX)" \
        || die "Could not create temporary storage for playlist entries."
    if ! playlist_entries "$list" > "$entries_file"; then
        rm -f -- "$entries_file"
        die "Could not parse playlist: $list"
    fi
    if ! mapfile -d '' -t entries < "$entries_file"; then
        rm -f -- "$entries_file"
        die "Could not read parsed playlist entries: $list"
    fi
    rm -f -- "$entries_file"

    for entry in "${entries[@]}"; do
        if [[ ! -f "$entry" ]]; then
            warn "Playlist '$stem': not found on this computer, skipped: $entry"
            # Only for an entry the run counted, which is one the firmware
            # could have played had it been there.
            if playable_name "$entry"; then
                progress_file missing "$(basename -- "$entry")"
            fi
            continue
        fi
        if ! playable_name "$entry"; then
            unplayable=$((unplayable + 1))
            continue
        fi
        dest="$(file_dest_dir "$entry")/$(basename "$entry")"
        copy_track "$entry" "$dest"
        dest="$COPY_TARGET"
        lines+=("iPod_Control/Music${dest#"$MUSIC_DIR"}")
        added=$((added + 1))
    done

    if (( unplayable > 0 )); then
        warn "Playlist '$stem': skipped $unplayable file(s) the firmware cannot play. Convert them first, for example:"
        warn "  ffmpeg -i input.flac -c:a libmp3lame -b:a 256k output.mp3"
    fi
    if (( added == 0 )); then
        assert_watched_device
        for existing_target in "$target" "$pls_target"; do
            if [[ -f "$existing_target" ]]; then
                rm -f -- "$existing_target"
                removed=1
            fi
        done
        if (( removed )); then
            warn "Playlist '$device_stem' references no playable local files; removed from the device."
            progress_playlist removed "$device_stem" 0
        else
            warn "Playlist '$stem' references no playable local files; not created."
            progress_playlist skipped "$device_stem" 0
        fi
        return 0
    fi
    assert_watched_device
    atomic_replace_lines "$target" "#EXTM3U" "${lines[@]}"
    rm -f -- "$pls_target"
    PLAYLIST_TARGET_SOURCES["$reservation_key"]="$list"
    PLAYLIST_TARGET_NAMES["$reservation_key"]="$device_stem.m3u"
    info "Playlist '$device_stem': $added track(s)"
    progress_playlist written "$device_stem" "$added"
}

# How many items the copy below will report on, counted before it starts.
#
# A bar with no denominator cannot say how far along it is, and the only
# denominator that reaches its end is the one this walk produces: the same
# sources, the same playlist parse and the same playable_name test the copy
# itself uses. It says nothing while counting - every warning about what it
# finds belongs to the pass that acts on it, and saying them twice would read
# as the run having hit the same problem twice.
plan_total() {
    local source entry count=0
    for source in "$@"; do
        [[ -e "$source" ]] || continue
        source="${source%/}"
        if [[ -f "$source" && "${source,,}" =~ \.(m3u|pls)$ ]]; then
            while IFS= read -r -d '' entry; do
                if playable_name "$entry"; then count=$((count + 1)); fi
            done < <(playlist_entries "$source" 2>/dev/null || true)
            # And the playlist itself, which is written once its tracks are
            # copied and is a piece of work with its own line in the log.
            count=$((count + 1))
            continue
        fi
        if [[ -f "$source" ]]; then
            if playable_name "$source"; then count=$((count + 1)); fi
            continue
        fi
        while IFS= read -r -d '' entry; do
            if playable_name "$entry"; then count=$((count + 1)); fi
        done < <(find -L "$source" \( -type f -o -type l \) -print0 2>/dev/null)
    done
    printf '%s' "$count"
}

# One listing buffer for the whole run, removed however the script leaves:
# a copy that dies part way through should not leave it behind in /tmp.
enum_errors="$(mktemp -t ipod-sync-sources.XXXXXX)" \
    || die "Could not create temporary storage for the source listing."
trap 'rm -f -- "${enum_errors:-}"' EXIT

# Only when somebody is listening: the count is a second walk of the sources,
# and a run that will not report it should not pay for it.
if progress_enabled; then
    progress_plan "$(plan_total "$@")"
fi
progress_stage copy start

for src in "$@"; do
    [[ -e "$src" ]] || { warn "No such path, skipping: $src"; continue; }
    src="${src%/}"

    if [[ -f "$src" && "${src,,}" =~ \.(m3u|pls)$ ]]; then
        sync_playlist "$src"
        continue
    fi

    if [[ -f "$src" ]]; then
        copy_track "$src" "$(file_dest_dir "$src")/$(basename "$src")"
        continue
    fi

    # Mirror the source tree under a folder named after it, rather than
    # flattening. Two albums each containing a track called 01.mp3 would
    # otherwise overwrite one another, and --playlists needs the structure
    # to have something to group by.
    dest="$MUSIC_DIR/$(basename "$src")"

    # -L, so that a symlinked track is copied and a symlinked folder is
    # descended. Without it a link is -type l, matches neither, and a library
    # assembled out of links syncs as an empty folder with nothing said about
    # it. Linked layouts are common enough that this was a real limitation
    # rather than a policy.
    #
    # A link is followed wherever it points, including outside the folder
    # being synced, because that is what a linked layout is for and the link
    # is only ever read. Where the copy lands comes from where the link sits
    # inside "$src" and never from its target, so following one cannot write
    # outside "$MUSIC_DIR". (Contrast ipod-remove.sh, which resolves before
    # checking containment because it deletes.)
    #
    # -type l still matches under -L, but only for a link that cannot be
    # resolved, so one walk finds the tracks and the broken links both.
    : > "$enum_errors"
    while IFS= read -r -d '' f; do
        if [[ ! -e "$f" ]]; then
            # Only for a name the firmware could have played. A dangling link
            # to a cover image was never going to be copied, so reporting it
            # would be noise about something the user did not ask for.
            if playable_name "$f"; then
                broken=$((broken + 1))
                warn "Broken symlink, skipped: ${f#"$src"/}"
                progress_file broken "${f#"$src"/}"
            fi
            continue
        fi
        copy_track "$f" "$dest/${f#"$src"/}"
    done < <(find -L "$src" \( -type f -o -type l \) -print0 2>"$enum_errors")
    # find walks a folder that links back into itself once and then refuses to
    # go round again, reporting that on stderr and exiting 1. Left alone it
    # arrives as raw find output in the middle of the copy log, so it is
    # repeated here in the script's own voice instead. Anything else find
    # could not read - an unreadable folder, most likely - reads the same way.
    if [[ -s "$enum_errors" ]]; then
        warn "Part of '$(basename -- "$src")' could not be searched:"
        while IFS= read -r line; do
            warn "  ${line#*: }"
        done < "$enum_errors"
    fi
done

rm -f -- "$enum_errors"
enum_errors=""
trap - EXIT

progress_stage copy 'done'

if (( ! REBUILD_ONLY )); then
    info "Copied $copied file(s)"
    if (( duplicates > 0 )); then
        info "Skipped $duplicates file(s) already on the iPod"
    fi
    if (( skipped > 0 )); then
        # MP3 rather than AAC deliberately: the firmware's AAC decoder crackles
        # on the dense frames a 256k encode of real music produces.
        warn "Skipped $skipped unsupported file(s). Convert them first, for example:"
        warn "  ffmpeg -i input.flac -c:a libmp3lame -b:a 256k output.mp3"
    fi
    if (( broken > 0 )); then
        warn "Skipped $broken symlink(s) pointing at a file that is not there."
    fi
    if (( copied == 0 && CLEAR == 0 )); then
        warn "Nothing new copied; rebuilding the database anyway."
    fi
fi

# The database is regenerated from scratch on every run, so a rebuild that
# omits the playlist and voiceover flags silently discards whatever the last
# run created. Remembering them on the device makes a bare rebuild safe, which
# matters most for the GUI's Rebuild button after the app has been restarted.
rebuild_database "$IPOD" "${DB_ARGS[@]+"${DB_ARGS[@]}"}"

if (( ${#DB_ARGS[@]} > 0 )); then
    assert_watched_device
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
    assert_watched_device
    rm -f -- "$OPTIONS_FILE" \
        || die "Could not clear playlist and voiceover options from the iPod."
fi

total="$(count_files "$MUSIC_DIR")"
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

# Out through the same door as every failure, so that the run reports how it
# ended and the caller reading the stream has it before the script returns.
leave 0
