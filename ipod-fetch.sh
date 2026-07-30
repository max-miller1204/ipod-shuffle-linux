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
# almost always Opus, so one re-encode is unavoidable.
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
  -s, --sync          Sync the downloaded music onto the iPod afterwards
  -u, --update        Update yt-dlp and exit; the fix when downloads start
                      failing, which happens whenever YouTube changes
  -h, --help          Show this message

One folder is created per artist, so the result is ready for playlists:

  ./ipod-fetch.sh 'https://www.youtube.com/watch?v=...'
  ./ipod-fetch.sh --single --sync 'https://www.youtube.com/watch?v=...'
  ./ipod-fetch.sh -o ~/Music/mixtape 'https://www.youtube.com/playlist?list=...'
  ./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music/youtube

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
    --format 'bestaudio[audio_channels<=2]/bestaudio'
    --extract-audio
    --audio-format m4a
    --audio-quality "$BITRATE"

    # Downmix anything multichannel that still reaches the encoder, for the
    # case where a stereo stream was not on offer at all.
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

    --output "$OUTPUT/%(artist,uploader)s/%(track,title)s.%(ext)s"
    --download-archive "$OUTPUT/.fetched"
)

(( SINGLE )) && YTDLP_ARGS+=(--no-playlist)

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
fetched=$(( after - before ))

if (( fetched > 0 )); then
    info "Downloaded $fetched track(s)"
else
    info "Nothing new; every track was already in $OUTPUT"
fi
info "$OUTPUT now holds $after track(s)"

if (( SYNC )); then
    # Pass the artist folders rather than $OUTPUT itself. ipod-sync.sh mirrors
    # each source under a folder named after it, so handing it the parent
    # would bury everything one level deeper under "youtube" and shift what
    # --dir-playlists=1 considers the artist level.
    shopt -s nullglob
    artist_dirs=("$OUTPUT"/*/)
    shopt -u nullglob

    if (( ${#artist_dirs[@]} == 0 )); then
        warn "Nothing in $OUTPUT to sync."
        exit 0
    fi

    "$(dirname "$(readlink -f "$0")")/ipod-sync.sh" "${artist_dirs[@]}"
else
    info "Next: ./ipod-sync.sh --dir-playlists=1 --playlist-voiceover $OUTPUT"
fi
