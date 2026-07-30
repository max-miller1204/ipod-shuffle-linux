#!/usr/bin/env bash
#
# One-time setup: fetch the database builder and report optional dependencies.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

readonly UPSTREAM_REPO="https://github.com/nims11/IPod-Shuffle-4g.git"
readonly TOOLS_DIR="${HOME}/ipod-tools"

# The database builder is GPL-2.0 and lives upstream rather than vendored here,
# so it can be updated independently and keeps its own licence and history.
info "Installing database builder into $TOOLS_DIR"
mkdir -p "$TOOLS_DIR"

if [[ -d "$TOOLS_DIR/IPod-Shuffle-4g/.git" ]]; then
    info "Already present; pulling latest"
    git -C "$TOOLS_DIR/IPod-Shuffle-4g" pull --ff-only
else
    git clone --depth 1 "$UPSTREAM_REPO" "$TOOLS_DIR/IPod-Shuffle-4g"
fi

command -v python3 >/dev/null || die "python3 is required but not installed."
info "python3: $(python3 --version)"

python3 -m py_compile "$DB_TOOL" \
    || die "Database builder failed to compile under this Python version."
info "Database builder compiles cleanly"

# Optional extras. None of these are required to put music on the device, so
# report rather than install; installing needs root and that is the user's call.
declare -a missing=()

if python3 -c 'import mutagen' 2>/dev/null; then
    info "mutagen: present (artist and album metadata will be written)"
else
    warn "mutagen missing - the database will have no artist or album info"
    missing+=("python3-mutagen")
fi

if command -v pico2wave >/dev/null; then
    info "pico2wave: present (voiceover available)"
elif command -v espeak >/dev/null; then
    info "espeak: present (voiceover available, robotic voice)"
else
    warn "No TTS engine - the --voiceover flag will not work"
    missing+=("libttspico-utils")
fi

command -v ffmpeg >/dev/null \
    || { warn "ffmpeg missing - you will not be able to convert unsupported formats"
         missing+=("ffmpeg"); }

if (( ${#missing[@]} > 0 )); then
    printf '\n'
    info "To install the optional extras:"
    printf '  sudo apt install %s\n' "${missing[*]}"
fi

printf '\n'
info "Setup complete. Plug in the iPod and run ./ipod-sync.sh ~/Music/somefolder"
