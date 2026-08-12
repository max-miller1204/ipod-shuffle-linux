#!/usr/bin/env bash
#
# Wipe an iPod shuffle 4G back to an empty but working state.
#
# This deliberately does not reformat the volume. Reformatting would destroy
# iPod_Control/Speakable, which holds Apple's built-in spoken system prompts
# (battery level, playlist names). Nothing in the open-source toolchain can
# regenerate those, and on a device with no screen they are the only feedback
# the hardware gives you.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

# Stale iTunes state. Safe to delete: the firmware reads iTunesSD, which gets
# rebuilt below, and the iTunesPrefs pair holds the previous owner's library
# binding (username and computer name) that a secondhand device carries over.
readonly STALE_STATE=(
    iTunesDB iTunesSD iTunesStats iTunesPState iTunesPrefs iTunesPrefs.plist
)

IPOD=""
BACKUP_DIR=""
PROGRESS_TARGET=""
DRY_RUN=0
EXPECT_DEVICE=""
CONFIRM_TOKEN=""

# Declared before anything can fail, because the result event is written from
# wherever the run ends rather than from where the device is counted. WIPED is
# what tells a run that got as far as emptying the device from one that died
# before it: the same track count means "taken" in the first and "still there"
# in the second.
track_count=0
playlist_count=0
WIPED=0

usage() {
    cat <<'EOF'
Usage: ./ipod-wipe.sh [options]

Removes all tracks, playlists, and stale iTunes state, then writes a fresh
empty database. Preserves Apple's Speakable prompts and the Device directory.

Options:
  -i, --ipod PATH     iPod mount point (default: autodetect)
  -b, --backup DIR    Copy existing music and databases to DIR first
  -y, --yes           Answer yes to every prompt
      --dry-run       Print the exact operation plan as JSON and change nothing
      --expect-device ID
                      Refuse unless the mounted iPod has this identity
      --confirm-token TOKEN
                      Approve the exact non-interactive plan from --dry-run
      --progress-json[=FD]
                      Report progress as one JSON object per line on
                      descriptor FD (default 3), which the caller opens. The
                      output below is unchanged.
  -h, --help          Show this message

Example:
  ./ipod-wipe.sh --backup ~/ipod-backup

Exit codes: 3 no iPod, 4 several iPods, 5 the iPod stopped answering, 6 a
missing dependency, 7 a declined prompt. Anything else that failed is 1.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod)   IPOD="$2"; shift 2 ;;
        -b|--backup) BACKUP_DIR="$2"; shift 2 ;;
        -y|--yes)    ASSUME_YES=1; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --expect-device) EXPECT_DEVICE="$2"; shift 2 ;;
        --confirm-token) CONFIRM_TOKEN="$2"; shift 2 ;;
        --progress-json|--progress-json=*) progress_flag "$1"; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "Unknown option: $1 (try --help)" ;;
    esac
done

# What the wipe took with it, for the last event on the stream. No plan and no
# per-file events: a wipe is one bulk delete rather than a file at a time, so
# what it has to report is which phase it is in, which the stages below say.
progress_result_fields() {
    if (( WIPED )); then
        printf '%s\n' removed "$track_count" playlists "$playlist_count" tracks 0
    else
        printf '%s\n' removed 0 playlists 0
    fi
}

# Opened before the iPod is looked for, so that a run with no iPod to work on
# still reports why on the stream the caller is reading.
progress_open wipe "$PROGRESS_TARGET"
# From here on every failure leaves through leave(), which is what reports
# the run's last event: the search below runs in a command substitution and
# can fail before there is a device to watch.
watch_failures

IPOD="$(find_ipod "$IPOD")"
assert_shuffle "$IPOD"
watch_device "$IPOD"
progress_event device ipod "$IPOD"

MUSIC_DIR="$IPOD/iPod_Control/Music"
ITUNES_DIR="$IPOD/iPod_Control/iTunes"

# The playlist files ipod-sync.sh stores at the volume root. They are cleared
# with the tracks they reference, and backed up alongside them: each one is
# the only record of which songs made up that playlist.
mapfile -d '' -t ROOT_PLAYLISTS < <(root_playlist_files "$IPOD")

track_count="$(count_files_present "$MUSIC_DIR")"
prepare_operation wipe "$IPOD" "$DEVICE_WATCH_IDENTITY" 1 \
    "$EXPECT_DEVICE" "$CONFIRM_TOKEN" "$DRY_RUN" \
    "backup=$BACKUP_DIR" \
    "tracks=$track_count" \
    "playlists=${#ROOT_PLAYLISTS[@]}" \
    "stale-state=${STALE_STATE[*]}"
info "iPod: $IPOD"
info "Tracks currently on device: $track_count"

if [[ -n "$BACKUP_DIR" ]]; then
    progress_stage backup start
    info "Backing up to $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    [[ -d "$MUSIC_DIR" ]]  && cp -a "$MUSIC_DIR"  "$BACKUP_DIR/"
    [[ -d "$ITUNES_DIR" ]] && cp -a "$ITUNES_DIR" "$BACKUP_DIR/"
    if (( ${#ROOT_PLAYLISTS[@]} > 0 )); then
        mkdir -p "$BACKUP_DIR/Playlists"
        cp -a -- "${ROOT_PLAYLISTS[@]}" "$BACKUP_DIR/Playlists/"
    fi

    # Verify before anything irreversible happens. The iTunesDB copy matters as
    # much as the audio: iPod filenames are scrambled four-character codes, and
    # that database is what maps them back to real artist and title metadata.
    backed_up="$(count_files_present "$BACKUP_DIR/Music")"
    (( backed_up == track_count )) \
        || die "Backup verification failed: $backed_up of $track_count files copied."
    info "Backup verified: $backed_up track(s) plus databases"
    progress_stage backup 'done'
fi

if (( ! ASSUME_YES )); then
    if (( track_count > 0 )) && [[ -z "$BACKUP_DIR" ]]; then
        warn "No backup requested. $track_count track(s) will be lost permanently."
    fi
    confirm "Wipe this iPod?" || die_with "$EXIT_DECLINED" "Aborted."
fi

assert_watched_device
progress_stage clear start
if [[ -d "$MUSIC_DIR" ]]; then
    assert_watched_device
    rm -rf "${MUSIC_DIR:?}"/*
    info "Removed $track_count track(s)"
fi
assert_watched_device
mkdir -p "$MUSIC_DIR"

if (( ${#ROOT_PLAYLISTS[@]} > 0 )); then
    assert_watched_device
    rm -f -- "${ROOT_PLAYLISTS[@]}"
    playlist_count=${#ROOT_PLAYLISTS[@]}
    info "Removed ${#ROOT_PLAYLISTS[@]} playlist(s)"
fi

for f in "${STALE_STATE[@]}"; do
    assert_watched_device
    rm -f "$ITUNES_DIR/$f"
done
assert_watched_device
rm -f "$(sync_options_file "$IPOD")"
info "Cleared stale iTunes state and previous-owner library binding"
WIPED=1
progress_stage clear 'done'

rebuild_database "$IPOD"

info "Preserved: $(count_files_present "$IPOD/iPod_Control/Speakable") Speakable prompt file(s)"
info "Wipe complete. The iPod is empty and ready for ./ipod-sync.sh"

warn "Unmount before unplugging:  ./ipod-sync.sh --rebuild-only --eject"

# Out through the same door as every failure, so that the run reports how it
# ended and the caller reading the stream has it before the script returns.
leave 0
