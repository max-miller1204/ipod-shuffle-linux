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

usage() {
    cat <<'EOF'
Usage: ./ipod-wipe.sh [options]

Removes all tracks and stale iTunes state, then writes a fresh empty database.
Preserves Apple's Speakable system prompts and the Device directory.

Options:
  -i, --ipod PATH     iPod mount point (default: autodetect)
  -b, --backup DIR    Copy existing music and databases to DIR first
  -y, --yes           Skip the confirmation prompt
  -h, --help          Show this message

Example:
  ./ipod-wipe.sh --backup ~/ipod-backup
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--ipod)   IPOD="$2"; shift 2 ;;
        -b|--backup) BACKUP_DIR="$2"; shift 2 ;;
        -y|--yes)    ASSUME_YES=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "Unknown option: $1 (try --help)" ;;
    esac
done

IPOD="$(find_ipod "$IPOD")"
assert_shuffle "$IPOD"

MUSIC_DIR="$IPOD/iPod_Control/Music"
ITUNES_DIR="$IPOD/iPod_Control/iTunes"

track_count="$(find "$MUSIC_DIR" -type f 2>/dev/null | wc -l)"
info "iPod: $IPOD"
info "Tracks currently on device: $track_count"

if [[ -n "$BACKUP_DIR" ]]; then
    info "Backing up to $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    [[ -d "$MUSIC_DIR" ]]  && cp -a "$MUSIC_DIR"  "$BACKUP_DIR/"
    [[ -d "$ITUNES_DIR" ]] && cp -a "$ITUNES_DIR" "$BACKUP_DIR/"

    # Verify before anything irreversible happens. The iTunesDB copy matters as
    # much as the audio: iPod filenames are scrambled four-character codes, and
    # that database is what maps them back to real artist and title metadata.
    backed_up="$(find "$BACKUP_DIR/Music" -type f 2>/dev/null | wc -l)"
    (( backed_up == track_count )) \
        || die "Backup verification failed: $backed_up of $track_count files copied."
    info "Backup verified: $backed_up track(s) plus databases"
fi

if (( ! ASSUME_YES )); then
    if (( track_count > 0 )) && [[ -z "$BACKUP_DIR" ]]; then
        warn "No backup requested. $track_count track(s) will be lost permanently."
    fi
    confirm "Wipe this iPod?" || die "Aborted."
fi

if [[ -d "$MUSIC_DIR" ]]; then
    rm -rf "${MUSIC_DIR:?}"/*
    info "Removed $track_count track(s)"
fi
mkdir -p "$MUSIC_DIR"

for f in "${STALE_STATE[@]}"; do
    rm -f "$ITUNES_DIR/$f"
done
rm -f "$(sync_options_file "$IPOD")"
info "Cleared stale iTunes state and previous-owner library binding"

rebuild_database "$IPOD"

info "Preserved: $(find "$IPOD/iPod_Control/Speakable" -type f 2>/dev/null | wc -l) Speakable prompt file(s)"
info "Wipe complete. The iPod is empty and ready for ./ipod-sync.sh"

warn "Unmount before unplugging:  ./ipod-sync.sh --rebuild-only --eject"
