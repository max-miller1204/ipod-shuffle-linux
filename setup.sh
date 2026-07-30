#!/usr/bin/env bash
#
# One-time setup: fetch the database builder, create its virtualenv, and
# report anything optional that is missing.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

readonly UPSTREAM_REPO="https://github.com/nims11/IPod-Shuffle-4g.git"

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

# mutagen goes in a virtualenv rather than via the system package manager.
# See the comment on VENV_PYTHON in lib.sh for why apt is not reliable here.
if [[ ! -x "$VENV_PYTHON" ]]; then
    info "Creating virtualenv at $TOOLS_DIR/venv"
    python3 -m venv "$TOOLS_DIR/venv" \
        || die "Could not create virtualenv. On Debian and Ubuntu, install python3-venv."
fi

if "$VENV_PYTHON" -c 'import mutagen' 2>/dev/null; then
    info "mutagen: present"
else
    info "Installing mutagen into the virtualenv"
    "$TOOLS_DIR/venv/bin/pip" install -q --disable-pip-version-check mutagen \
        || warn "mutagen install failed; the database will have no artist or album info"
fi

"$VENV_PYTHON" -c 'import mutagen' 2>/dev/null \
    && info "Metadata support ready ($("$VENV_PYTHON" -c 'import mutagen; print("mutagen", mutagen.version_string)'))"

# Optional system packages. Reported rather than installed, since installing
# needs root and that is the user's call.
declare -a missing=()

if command -v pico2wave >/dev/null; then
    info "pico2wave: present (voiceover available)"
elif command -v espeak >/dev/null; then
    info "espeak: present (voiceover available, robotic voice)"
else
    warn "No TTS engine - the --voiceover flag will not work"
    missing+=("libttspico-utils")
fi

if command -v ffmpeg >/dev/null; then
    info "ffmpeg: present"
else
    warn "ffmpeg missing - you will not be able to convert unsupported formats"
    missing+=("ffmpeg")
fi

# The GUI needs GTK4 and libadwaita through PyGObject. These come from the
# distro rather than pip, because building PyGObject from source pulls in
# gobject-introspection and cairo development headers.
if gui_python="$(find_gui_python 2>/dev/null)"; then
    info "GUI ready (GTK4 via $gui_python)"
else
    warn "GUI unavailable - no python with GTK4 bindings found"
    missing+=("python3-gi" "gir1.2-gtk-4.0" "gir1.2-adw-1")
fi

if (( ${#missing[@]} > 0 )); then
    printf '\n'
    info "To install the optional extras:"
    printf '  sudo apt install %s\n' "${missing[*]}"
fi

printf '\n'
info "Setup complete."
info "  GUI:  ./ipod-gui.sh"
info "  CLI:  ./ipod-sync.sh ~/Music/somefolder"
