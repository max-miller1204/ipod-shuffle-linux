#!/usr/bin/env bash
#
# Download audio from YouTube in a format the iPod shuffle can play.
#
# Usage: ./ipod-fetch.sh [options] <url> [more-urls...]
#
# See README.md for the full workflow.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

# MP3 is deliberate even though AAC looks better on paper: ffmpeg's native AAC
# output crackled on a real shuffle 4G, while this MP3 configuration played
# cleanly. README.md owns the device-bisect results and encoder rationale.
#
# 256k is deliberate headroom over that ~160k source rather than a claim about
# it: encoding lossy to lossy loses a little every time, and the cheapest way
# to keep that loss inaudible is to give the second encoder room. It is also
# exactly the configuration that tested clean, so it is not rounded off.
readonly DEFAULT_BITRATE="256k"
readonly DEFAULT_OUTPUT="${HOME}/Music/youtube"

OUTPUT="$DEFAULT_OUTPUT"
BITRATE="$DEFAULT_BITRATE"
SYNC=0
SINGLE=0
UPDATE=0
NEW_TRACKS=""

usage() {
    cat <<EOF
Usage: ./ipod-fetch.sh [options] <url> [more-urls...]

Downloads audio as ${DEFAULT_BITRATE} MP3, which is what the shuffle firmware decodes
cleanly, tagged so that artist and title survive onto the device.

Options:
  -o, --output DIR    Where to save (default: ~/Music/youtube)
  -b, --bitrate RATE  MP3 bitrate, e.g. 128k, 192k (default: ${DEFAULT_BITRATE})
  -1, --single        Download only the given video, not its whole playlist
  -s, --sync          Copy what this run downloaded onto the iPod afterwards
      --new-tracks FILE
                      Write the path of each newly downloaded track to FILE,
                      one per line, for another tool to act on. The file is
                      deleted rather than left stale when this yt-dlp is too
                      old to report them.
  -u, --update        Update yt-dlp and exit; the fix when downloads start
                      failing, which happens whenever YouTube changes
  -h, --help          Show this message

One folder is created per artist, so the result is ready for playlists:

  ./ipod-fetch.sh 'https://www.youtube.com/watch?v=...'
  ./ipod-fetch.sh --single --sync 'https://www.youtube.com/watch?v=...'
  ./ipod-fetch.sh -o ~/Music/mixtape 'https://www.youtube.com/playlist?list=...'
  ./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music/youtube/*/

Already-downloaded videos are recorded in <output>/.fetched and skipped on
later runs, so re-running a playlist URL collects only what is new.

You are responsible for having the right to download what you point this at.

Exit code 6 means a missing dependency. With --sync the codes of the sync it
runs come through as they are, so 3, 4, 5 and 7 mean what they mean there.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output)  OUTPUT="${2%/}"; shift 2 ;;
        -b|--bitrate) BITRATE="$2"; shift 2 ;;
        -1|--single)  SINGLE=1; shift ;;
        -s|--sync)    SYNC=1; shift ;;
        --new-tracks) NEW_TRACKS="$2"; shift 2 ;;
        -u|--update)  UPDATE=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        -*)           die "Unknown option: $1 (try --help)" ;;
        *)            break ;;
    esac
done

# yt-dlp breaks whenever YouTube changes something, which is often, so keeping
# it current is routine maintenance rather than an unusual event. Updating the
# copy this project owns is a flag rather than a documented incantation.
#
# Through uv, which is what install.sh builds and fills that environment with:
# it holds no pip of its own, and reaching one through the distro's site
# packages would install with a tool that does not own the environment on the
# machines that happen to have it and fail outright on the ones that do not.
if (( UPDATE )); then
    (( $# == 0 )) || die "--update takes no URLs."
    [[ -x "$VENV_PYTHON" ]] \
        || die_with "$EXIT_MISSING_DEPENDENCY" \
            "No virtualenv at $VENV_PYTHON - run ./install.sh first."
    command -v uv >/dev/null 2>&1 \
        || die_with "$EXIT_MISSING_DEPENDENCY" \
            "uv is required to update yt-dlp - install it from https://docs.astral.sh/uv/."
    info "Updating yt-dlp"
    uv pip install --quiet --python "$VENV_PYTHON" --upgrade yt-dlp \
        || die "Failed to update yt-dlp."
    info "yt-dlp $("$(yt_dlp_bin)" --version)"
    exit 0
fi

[[ $# -gt 0 ]] || { usage; exit 1; }

[[ "$BITRATE" =~ ^[0-9]+[kK]?$ ]] \
    || die "Invalid bitrate: $BITRATE (expected something like 256k)"

YT_DLP="$(yt_dlp_bin)"

# The MP3 encoder and the tag writer are both ffmpeg, so without it yt-dlp
# would hand back the Opus file it downloaded and the shuffle would ignore it.
command -v ffmpeg >/dev/null \
    || die_with "$EXIT_MISSING_DEPENDENCY" \
        "ffmpeg is required to produce MP3 - run ./install.sh."

mkdir -p "$OUTPUT"

declare -a YTDLP_ARGS=(
    # Reject multichannel at selection time: plain bestaudio can choose a large
    # 5.1 AAC stream that this stereo device would only downmix.
    --format 'bestaudio[audio_channels<=2]'
    --extract-audio
    --audio-format mp3
    --audio-quality "$BITRATE"

    # Pin output to stereo and give the encoder 4 dB of headroom. Re-encoding
    # brickwalled masters otherwise added clipping, and the unlimited version
    # sounded worse on the device. README.md owns the measurements.
    #
    # level=false stops the limiter handing the gain straight back as makeup,
    # which would put the peaks back exactly where they started. latency=true
    # compensates the lookahead: without it every track is shifted late by the
    # attack time, 240 samples at 48kHz, even when the limiter never engages.
    # With it, a source that stays under the threshold comes out bit-identical.
    #
    # Pin 44.1kHz because it is the shuffle's native rate and the configuration
    # verified by ear. Sample rate was explicitly ruled out as the crackle's
    # cause, but leaving YouTube's 48kHz Opus rate would ship an untested format.
    --postprocessor-args 'ExtractAudio:-ac 2 -af alimiter=limit=0.631:level=false:latency=true,aresample=44100:resampler=soxr'

    # Without tags the device shows scrambled four-character filenames and
    # nothing else, and --id3-playlists has nothing to group by.
    --embed-metadata

    # Cover art is pure waste on a 2GB device with no screen.
    --no-embed-thumbnail

    # These names have to survive a copy onto the iPod's vfat filesystem, and
    # YouTube titles are full of characters vfat rejects outright. Sanitising
    # at download time means the copy cannot fail halfway through a sync.
    #
    # Deliberately no --trim-filenames: it limits the whole path rather than
    # the filename, so a long --output directory eats the budget and truncates
    # the song title itself, silently colliding tracks that then overwrite one
    # another. YouTube titles stop at 100 characters and vfat allows 255, so
    # there is nothing here to protect against anyway.
    --windows-filenames

    # YouTube Music uploads every track under a channel called "<Artist> -
    # Topic". Left alone that suffix ends up as the folder name, and so as the
    # spoken playlist name under --dir-playlists.
    --replace-in-metadata uploader ' - Topic$' ''

    --output "$OUTPUT/%(artist,uploader)s/%(track,title)s [%(id)s].%(ext)s"
    --download-archive "$OUTPUT/.fetched"
)

(( SINGLE )) && YTDLP_ARGS+=(--no-playlist)

# Without this, downloads of most commercial music fail with HTTP 403 while
# metadata extraction still succeeds, because yt-dlp enables only deno by
# default and cannot solve YouTube's signature challenge without a runtime.
if runtime="$(js_runtime)"; then
    if yt_dlp_supports "$YT_DLP" --js-runtimes; then
        YTDLP_ARGS+=(--js-runtimes "$runtime")
    else
        warn "This yt-dlp is too old to select the installed JavaScript runtime."
        warn "Run ./install.sh or ./ipod-fetch.sh --update for full YouTube support."
    fi
else
    warn "No supported JavaScript runtime found."
    warn "Need Deno >= 2.3, Node >= 22, or Bun 1.2.11-1.3.14."
    warn "YouTube downloads will fail with HTTP 403 for all but the oldest videos."
fi

# Have yt-dlp name each file it downloads, which is the difference between
# syncing this run's tracks and syncing the whole library every time. The
# output folder accumulates, so counting files before and after says how many
# arrived but not which, and --sync would then have to copy everything and let
# ipod-sync.sh skip the duplicates.
TRACK_LIST=""
TEMP_LIST=""
if yt_dlp_supports "$YT_DLP" --print-to-file; then
    if [[ -n "$NEW_TRACKS" ]]; then
        TRACK_LIST="$NEW_TRACKS"
    else
        TEMP_LIST="$(mktemp -t ipod-fetch-tracks.XXXXXX)"
        trap 'rm -f -- "${TEMP_LIST:-}"' EXIT
        TRACK_LIST="$TEMP_LIST"
    fi
    # --print-to-file appends, so a file left over from an earlier run would
    # otherwise be reported as freshly downloaded.
    : > "$TRACK_LIST" || die "Cannot write the track list at $TRACK_LIST."
    YTDLP_ARGS+=(--print-to-file "after_move:filepath" "$TRACK_LIST")
else
    warn "This yt-dlp is too old to report which files it downloaded."
    warn "Falling back to counting the output folder."
    # Absent rather than empty, so a caller can tell "cannot say" from
    # "nothing new" instead of concluding the download produced nothing.
    if [[ -n "$NEW_TRACKS" ]]; then
        rm -f -- "$NEW_TRACKS"
    fi
fi

count_tracks() {
    # Counted the way count_files counts, and for the same reason: a YouTube
    # title can hold a newline, and this figure decides whether the download
    # produced anything at all.
    find "$OUTPUT" -type f -regextype posix-extended \
        -iregex ".*\.(${SUPPORTED_EXT})$" -printf '.' | wc -c
}

before="$(count_tracks)"

info "Downloading as $BITRATE MP3 into $OUTPUT"
"$YT_DLP" "${YTDLP_ARGS[@]}" -- "$@" || {
    err "yt-dlp failed."
    err "If this started happening suddenly, YouTube has probably changed:"
    err "  ./ipod-fetch.sh --update"
    exit 1
}

after="$(count_tracks)"

declare -a NEW_FILES=()
if [[ -n "$TRACK_LIST" ]]; then
    while IFS= read -r line; do
        if [[ -n "$line" ]]; then
            NEW_FILES+=("$line")
        fi
    done < "$TRACK_LIST"
    fetched=${#NEW_FILES[@]}
else
    fetched=$(( after - before ))
fi

if (( fetched > 0 )); then
    info "Downloaded $fetched track(s)"
else
    info "Nothing new; every track was already in $OUTPUT"
fi
info "$OUTPUT now holds $after track(s)"

if (( SYNC )); then
    declare -a SOURCES=()
    if [[ -n "$TRACK_LIST" ]]; then
        if (( ${#NEW_FILES[@]} == 0 )); then
            info "Nothing new to copy onto the iPod."
            info "For the whole library: ./ipod-sync.sh \"$OUTPUT\"/*/"
            exit 0
        fi
        # ipod-sync.sh files each track under the folder it came from, so these
        # land in Music/<artist>/ exactly as the folders below would put them.
        SOURCES=("${NEW_FILES[@]}")
    else
        # Pass the artist folders rather than $OUTPUT itself. ipod-sync.sh
        # mirrors each source folder under one named after it, so handing it
        # the parent would bury everything a level deeper under "youtube" and
        # shift what --dir-playlists=1 considers the artist level.
        shopt -s nullglob
        SOURCES=("$OUTPUT"/*/)
        shopt -u nullglob

        if (( ${#SOURCES[@]} == 0 )); then
            warn "Nothing in $OUTPUT to sync."
            exit 0
        fi
    fi

    "$(dirname "$(readlink -f "$0")")/ipod-sync.sh" -- "${SOURCES[@]}"
else
    info "Next: ./ipod-sync.sh --dir-playlists=1 --playlist-voiceover \"$OUTPUT\"/*/"
fi
