#!/usr/bin/env bash
#
# Install everything needed to manage an iPod shuffle 4G.
#
# Python dependencies go into a private virtualenv. The few pieces that cannot
# live there, because they are C libraries or plain binaries, are installed
# through the system package manager, which is the only step needing
# privileges. Run with --no-system to skip that part entirely.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

readonly UPSTREAM_REPO="https://github.com/nims11/IPod-Shuffle-4g.git"
REQUIREMENTS="$(dirname "$(readlink -f "$0")")/requirements.txt"
readonly REQUIREMENTS

SKIP_SYSTEM=0
ASSUME_YES=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Installs the database builder, a virtualenv with its Python dependencies, and
any missing system packages.

Options:
  -n, --no-system   Do not install system packages, only report them
  -y, --yes         Do not ask before installing system packages
  -h, --help        Show this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--no-system) SKIP_SYSTEM=1; shift ;;
        -y|--yes)       ASSUME_YES=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "Unknown option: $1 (try --help)" ;;
    esac
done

# ---------------------------------------------------------------- prerequisites

command -v python3 >/dev/null || die "python3 is required but not installed."
command -v git >/dev/null     || die "git is required but not installed."
info "python3: $(python3 --version)"

# ------------------------------------------------------------------ privileged

# Probe for capabilities rather than package names, so the check itself works
# on any distribution even though the install command below assumes apt.
declare -a needed=()

if [[ ! -x "$VENV_PYTHON" ]] \
    && ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    needed+=("python3-venv")
fi

if find_gui_python >/dev/null 2>&1; then
    info "GUI: GTK4 bindings present"
else
    needed+=("python3-gi" "gir1.2-gtk-4.0" "gir1.2-adw-1")
fi

if command -v pico2wave >/dev/null || command -v espeak >/dev/null; then
    info "Voiceover: speech engine present"
else
    needed+=("libttspico-utils")
fi

if command -v ffmpeg >/dev/null; then
    info "Conversion: ffmpeg present"
else
    needed+=("ffmpeg")
fi

# YouTube hides most media URLs behind a signature challenge that has to be
# solved in JavaScript. Without a runtime yt-dlp still extracts titles and
# formats perfectly and then fails every download with HTTP 403, which reads
# as a broken tool rather than a missing dependency.
if runtime="$(js_runtime)"; then
    info "YouTube downloads: $runtime present"
else
    needed+=("nodejs")
    warn "YouTube downloads need Deno >= 2.3, Node >= 22, or Bun 1.2.11-1.3.14."
    warn "Your distribution's JavaScript runtime package may be too old."
    warn "Install a supported runtime: https://github.com/yt-dlp/yt-dlp/wiki/EJS"
fi

if (( ${#needed[@]} == 0 )); then
    info "All system dependencies satisfied"
else
    printf '\n'
    info "These need the system package manager:"
    printf '  %s\n' "${needed[@]}"
    printf '\n'

    if (( SKIP_SYSTEM )); then
        info "Skipping (--no-system). To install them yourself:"
        printf '  sudo apt install %s\n' "${needed[*]}"
    elif ! command -v apt-get >/dev/null; then
        warn "Not an apt-based system. Install the equivalents with your package manager:"
        printf '  %s\n' "${needed[*]}"
    else
        proceed=1
        if (( ! ASSUME_YES )); then
            confirm "Install them now?" || proceed=0
        fi

        if (( proceed )); then
            # pkexec asks through the desktop's polkit agent, which works when
            # this script is run from a launcher with no controlling terminal.
            # sudo is the fallback for a plain text session.
            if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v pkexec >/dev/null; then
                info "Requesting privileges (a password dialog will appear)"
                pkexec apt-get install -y "${needed[@]}"
            else
                info "Requesting privileges"
                sudo apt-get install -y "${needed[@]}"
            fi
            info "System packages installed"
        else
            info "Skipped. To install them later:"
            printf '  sudo apt install %s\n' "${needed[*]}"
        fi
    fi
fi

# ---------------------------------------------------------------- unprivileged

info "Installing database builder into $TOOLS_DIR"
mkdir -p "$TOOLS_DIR"
if [[ -d "$TOOLS_DIR/IPod-Shuffle-4g/.git" ]]; then
    git -C "$TOOLS_DIR/IPod-Shuffle-4g" pull --ff-only --quiet
    info "Database builder updated"
else
    git clone --depth 1 --quiet "$UPSTREAM_REPO" "$TOOLS_DIR/IPod-Shuffle-4g"
    info "Database builder cloned"
fi

python3 -m py_compile "$DB_TOOL" \
    || die "Database builder failed to compile under this Python version."

if [[ ! -x "$VENV_PYTHON" ]]; then
    info "Creating virtualenv at $TOOLS_DIR/venv"
    python3 -m venv "$TOOLS_DIR/venv" \
        || die "Could not create virtualenv. Re-run without --no-system to install python3-venv."
fi

info "Installing Python dependencies"
"$TOOLS_DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$REQUIREMENTS" \
    || die "Failed to install Python dependencies."
info "  $("$VENV_PYTHON" -c 'import mutagen; print("mutagen", mutagen.version_string)')"
info "  yt-dlp $("$VENV_YT_DLP" --version 2>/dev/null || echo "unavailable")"

# ---------------------------------------------------------------- verification

printf '\n'
info "Verifying"

if "$VENV_PYTHON" -c 'import mutagen' 2>/dev/null; then
    info "  metadata support   ok"
else
    warn "  metadata support   missing"
fi

if [[ -x "$VENV_YT_DLP" ]]; then
    verification_ytdlp="$VENV_YT_DLP"
elif command -v yt-dlp >/dev/null 2>&1; then
    verification_ytdlp="$(command -v yt-dlp)"
else
    warn "  youtube downloads unavailable (no yt-dlp)"
    verification_ytdlp=
fi

if [[ -n "$verification_ytdlp" ]] && ! yt_dlp_supports_js_runtimes "$verification_ytdlp"; then
    warn "  youtube downloads limited (yt-dlp lacks --js-runtimes)"
elif [[ -n "$verification_ytdlp" ]] && ! js_runtime >/dev/null; then
    # Reporting "ok" on yt-dlp alone would promise a feature that then fails
    # with HTTP 403 on everything except the oldest unrestricted videos.
    warn "  youtube downloads unavailable (no supported JavaScript runtime)"
elif [[ -n "$verification_ytdlp" ]]; then
    info "  youtube downloads ok"
fi

if gui_python="$(find_gui_python 2>/dev/null)"; then
    info "  graphical interface ok ($gui_python)"
else
    warn "  graphical interface unavailable"
fi

printf '\n'
info "Done."
info "  GUI:  ./ipod-gui.sh"
info "  CLI:  ./ipod-sync.sh ~/Music/somefolder"
