#!/usr/bin/env bash
#
# Download audio from YouTube in a format the iPod shuffle can play.
#
# Usage: ./ipod-fetch.sh [options] <url> [more-urls...]
#
# See README.md for the full workflow.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

# The shuffle decodes AAC but not Opus, and YouTube's best audio stream is
# almost always Opus, so one re-encode is usually needed.
#
# 256k is deliberate headroom over that ~160k source rather than a claim about
# it: encoding lossy to lossy loses a little every time, and the cheapest way
# to keep that loss inaudible is to give the second encoder room. Where yt-dlp
# does find a native AAC stream it is remuxed rather than re-encoded, and this
# bitrate does not apply.
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

Downloads audio as ${DEFAULT_BITRATE} AAC in an .m4a container, which is the best
quality the shuffle firmware can actually decode, tagged so that artist and
title survive onto the device.

Options:
  -o, --output DIR    Where to save (default: ~/Music/youtube)
  -b, --bitrate RATE  AAC bitrate, e.g. 128k, 192k (default: ${DEFAULT_BITRATE})
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
# copy this project owns is a flag rather than a documented pip incantation.
if (( UPDATE )); then
    (( $# == 0 )) || die "--update takes no URLs."
    [[ -x "$VENV_PYTHON" ]] \
        || die "No virtualenv at $VENV_PYTHON - run ./install.sh first."
    info "Updating yt-dlp"
    "$VENV_PYTHON" -m pip install -q --disable-pip-version-check --upgrade yt-dlp \
        || die "Failed to update yt-dlp."
    info "yt-dlp $("$(yt_dlp_bin)" --version)"
    exit 0
fi

[[ $# -gt 0 ]] || { usage; exit 1; }

[[ "$BITRATE" =~ ^[0-9]+[kK]?$ ]] \
    || die "Invalid bitrate: $BITRATE (expected something like 256k)"

YT_DLP="$(yt_dlp_bin)"

# The AAC encoder and the tag writer are both ffmpeg, so without it yt-dlp
# would hand back the Opus file it downloaded and the shuffle would ignore it.
command -v ffmpeg >/dev/null \
    || die "ffmpeg is required to produce AAC - run ./install.sh."

mkdir -p "$OUTPUT"

declare -a YTDLP_ARGS=(
    # Stereo only, and not merely because the shuffle has two channels.
    #
    # YouTube offers 5.1 AAC (itag 258, 388k) and plain bestaudio ranks it top
    # on bitrate. It arrives already in an m4a container, so yt-dlp reports
    # "already in target format" and skips the conversion entirely, which
    # silently discards --audio-quality as well. The result is a 30MB
    # six-channel file, 1.5% of the device, that its decoder cannot play.
    # Excluding multichannel at selection time leaves the stereo Opus stream,
    # which is both smaller and the one that actually gets converted.
    --format 'bestaudio[audio_channels<=2]'
    --extract-audio
    --audio-format m4a
    --audio-quality "$BITRATE"

    # Pin encoded output to the shuffle's two supported channels.
    --postprocessor-args 'ExtractAudio:-ac 2'

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

count_tracks() { find "$OUTPUT" -type f -name '*.m4a' | wc -l; }

before="$(count_tracks)"

info "Downloading as $BITRATE AAC into $OUTPUT"
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
