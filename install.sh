#!/usr/bin/env bash
#
# Set up the dependencies needed to manage an iPod shuffle 4G.
#
# Python dependencies go into a private virtualenv. Compatible distribution
# packages are offered through the system package manager, which is the only
# step needing privileges. A missing JavaScript runtime is reported for manual
# installation instead. Run with --no-system to skip the package-manager step.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

readonly UPSTREAM_REPO="https://github.com/nims11/IPod-Shuffle-4g.git"
REQUIREMENTS="$(dirname "$(readlink -f "$0")")/requirements.txt"
readonly REQUIREMENTS

SKIP_SYSTEM=0
ASSUME_YES=0
CHECK=0
JSON=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Installs the database builder and a virtualenv with its Python dependencies,
offers compatible missing system packages, installs the GUI's desktop entry
when its dependencies are available, and reports a missing JavaScript runtime
for manual installation.

Options:
  -c, --check       Report what is installed and exit, changing nothing
  -j, --json        With --check, report it as JSON instead of prose
  -n, --no-system   Do not install system packages, only report them
  -y, --yes         Do not ask before installing system packages
  -h, --help        Show this message

--check exits 0 when every capability is there and 6 when one is missing,
having printed the report either way, so a caller can find out what this
machine can do without installing anything.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--check)     CHECK=1; shift ;;
        -j|--json)      JSON=1; shift ;;
        -n|--no-system) SKIP_SYSTEM=1; shift ;;
        -y|--yes)       ASSUME_YES=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "Unknown option: $1 (try --help)" ;;
    esac
done

(( JSON == 0 || CHECK )) || die "--json only reports; add --check."

# ----------------------------------------------------------------- capabilities

# The apt names behind each capability, named once so the offer the installer
# makes and the report --check prints cannot list different packages for the
# same missing thing.
readonly VENV_PACKAGES=(python3-venv)
readonly GUI_PACKAGES=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1)
# One working set rather than four choices: plugins-base carries the player,
# plugins-good the MP3 and WAV decoders and the connection to the sound card,
# and plugins-bad the AAC decoder .m4a needs.
readonly GST_PACKAGES=(
    gir1.2-gstreamer-1.0
    gstreamer1.0-plugins-base
    gstreamer1.0-plugins-good
    gstreamer1.0-plugins-bad
)
readonly SPEECH_PACKAGES=(libttspico-utils)
readonly CONVERSION_PACKAGES=(ffmpeg)

# What this machine can do, probed once and rendered twice: as prose for the
# person who ran this, and as JSON for whatever ran it. One list rather than
# two, so a report cannot claim something the installer disagrees with.
declare -a CAPABILITIES=()

# Fields are separated by the ASCII unit separator rather than a tab: tab is
# an IFS whitespace character, so bash folds runs of them together and a
# capability with nothing to add would silently lose its empty field.
capability() {
    CAPABILITIES+=("$1"$'\x1f'"$2"$'\x1f'"$3"$'\x1f'"$4"$'\x1f'"$5")
}

probe_capabilities() {
    local runtime gui_python ytdlp
    CAPABILITIES=()

    if [[ -x "$VENV_PYTHON" ]]; then
        capability python-virtualenv 1 "python virtualenv" "$VENV_PYTHON" ""
    elif python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
        capability python-virtualenv 0 "python virtualenv" \
            "not created yet at $VENV_PYTHON" ""
    else
        capability python-virtualenv 0 "python virtualenv" \
            "python3 cannot create one" "${VENV_PACKAGES[*]}"
    fi

    if [[ -f "$DB_TOOL" ]]; then
        capability database-builder 1 "database builder" "$DB_TOOL" ""
    else
        capability database-builder 0 "database builder" \
            "not installed at $DB_TOOL" ""
    fi

    if [[ -x "$VENV_PYTHON" ]] && "$VENV_PYTHON" -c 'import mutagen' 2>/dev/null; then
        capability metadata 1 "metadata support" "" ""
    else
        capability metadata 0 "metadata support" \
            "mutagen is not in the virtualenv" ""
    fi

    if gui_python="$(find_gui_python 2>/dev/null)"; then
        capability graphical-interface 1 "graphical interface" \
            "$gui_python" "${GUI_PACKAGES[*]}"
    else
        capability graphical-interface 0 "graphical interface" \
            "no interpreter here has the GTK4 bindings" "${GUI_PACKAGES[*]}"
    fi

    if gst_available; then
        capability preview-playback 1 "preview playback" "" "${GST_PACKAGES[*]}"
    else
        capability preview-playback 0 "preview playback" \
            "no GStreamer" "${GST_PACKAGES[*]}"
    fi

    if command -v pico2wave >/dev/null || command -v espeak >/dev/null; then
        capability voiceover 1 "voiceover" "" "${SPEECH_PACKAGES[*]}"
    else
        capability voiceover 0 "voiceover" \
            "no speech engine" "${SPEECH_PACKAGES[*]}"
    fi

    if command -v ffmpeg >/dev/null; then
        capability audio-conversion 1 "audio conversion" "" \
            "${CONVERSION_PACKAGES[*]}"
    else
        capability audio-conversion 0 "audio conversion" "no ffmpeg" \
            "${CONVERSION_PACKAGES[*]}"
    fi

    # No packages, deliberately, and the one capability here that names none it
    # could have. Ubuntu can provide nodejs 18, below yt-dlp's floor of 22, so
    # offering it would spend a privileged apt transaction on a runtime
    # js_runtime() then rejects.
    if runtime="$(js_runtime)"; then
        capability javascript-runtime 1 "javascript runtime" "$runtime" ""
    else
        capability javascript-runtime 0 "javascript runtime" \
            "needs Deno >= 2.3, Node >= 22, or Bun 1.2.11-1.3.14" ""
    fi

    # The end-to-end verdict rather than another reading of one part: every
    # one of these failures surfaces late and misleadingly, the missing
    # runtime as HTTP 403 on all but the oldest uploads and the missing ffmpeg
    # as an Opus file the shuffle silently ignores.
    ytdlp="$(yt_dlp_bin 2>/dev/null)" || ytdlp=""
    if [[ -z "$ytdlp" ]]; then
        capability youtube-downloads 0 "youtube downloads" "no yt-dlp" ""
    elif ! yt_dlp_supports "$ytdlp" --js-runtimes; then
        capability youtube-downloads 0 "youtube downloads" \
            "this yt-dlp is too old to select a JavaScript runtime" ""
    elif ! js_runtime >/dev/null; then
        capability youtube-downloads 0 "youtube downloads" \
            "no supported JavaScript runtime" ""
    elif ! command -v ffmpeg >/dev/null; then
        capability youtube-downloads 0 "youtube downloads" \
            "no ffmpeg to produce MP3 with" ""
    else
        capability youtube-downloads 1 "youtube downloads" "$ytdlp" ""
    fi
}

report_capabilities() {
    # The id and the packages are for the JSON reader; a person reading this
    # wants the name and the sentence, and the packages missing ones would
    # need are gathered into one apt line further down anyway.
    local present label detail
    while IFS=$'\x1f' read -r _ present label detail _; do
        if [[ "$present" == 1 ]]; then
            if [[ -n "$detail" ]]; then
                info "$(printf '  %-20s ok (%s)' "$label" "$detail")"
            else
                info "$(printf '  %-20s ok' "$label")"
            fi
        else
            warn "$(printf '  %-20s unavailable (%s)' "$label" "$detail")"
        fi
    done < <(printf '%s\n' "${CAPABILITIES[@]}")
}

capabilities_satisfied() {
    local present
    while IFS=$'\x1f' read -r _ present _ _ _; do
        [[ "$present" == 1 ]] || return 1
    done < <(printf '%s\n' "${CAPABILITIES[@]}")
    return 0
}

# Nothing is installed, nothing is written, and with --json nothing but the
# document reaches stdout - which is why this comes before the prerequisite
# checks below, whose first act is to print the Python version.
if (( CHECK )); then
    probe_capabilities
    if (( JSON )); then
        command -v python3 >/dev/null \
            || die_with "$EXIT_MISSING_DEPENDENCY" \
                "python3 is required but not installed."
        printf '%s\n' "${CAPABILITIES[@]}" | python3 "$REPORT_TOOL" capabilities
    else
        info "Dependencies"
        report_capabilities
    fi
    capabilities_satisfied || leave "$EXIT_MISSING_DEPENDENCY"
    exit 0
fi

# ---------------------------------------------------------------- prerequisites

command -v python3 >/dev/null || die "python3 is required but not installed."
command -v git >/dev/null     || die "git is required but not installed."
info "python3: $(python3 --version)"

readonly APP_ID="io.github.max_miller1204.IpodShuffle"
apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop_file="$apps_dir/$APP_ID.desktop"

# ------------------------------------------------------------------ privileged

# Probe for capabilities rather than package names, so the check itself works
# on any distribution even though the install command below assumes apt.
declare -a needed=()

if [[ ! -x "$VENV_PYTHON" ]] \
    && ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    needed+=("${VENV_PACKAGES[@]}")
fi

if find_gui_python >/dev/null 2>&1; then
    info "GUI: GTK4 bindings present"
else
    if [[ -e "$desktop_file" ]]; then
        rm -f -- "$desktop_file"
        if command -v update-desktop-database >/dev/null; then
            update-desktop-database "$apps_dir" 2>/dev/null || true
        fi
        info "Desktop entry removed because the GUI dependencies are unavailable"
    fi
    needed+=("${GUI_PACKAGES[@]}")
fi

# Preview playback on this computer's speakers. Optional in a way the window is
# built around: without it the GUI still runs and the now-playing bar says what
# is missing, so this is offered like ffmpeg rather than withheld like the
# JavaScript runtime. That exception exists because Ubuntu's nodejs can be older
# than yt-dlp accepts, so installing it would spend a privileged transaction on
# a runtime the probe then rejects; GStreamer's packages have no such hazard.
#
# The probe proves the player and the sink; a machine holding some of the rest
# of the working set and not others is reported by the GUI per file, in
# GStreamer's own words, since those name the plugin to install.
if gst_available; then
    info "Preview playback: GStreamer present"
else
    needed+=("${GST_PACKAGES[@]}")
fi

if command -v pico2wave >/dev/null || command -v espeak >/dev/null; then
    info "Voiceover: speech engine present"
else
    needed+=("${SPEECH_PACKAGES[@]}")
fi

if command -v ffmpeg >/dev/null; then
    info "Conversion: ffmpeg present"
else
    needed+=("${CONVERSION_PACKAGES[@]}")
fi

# YouTube hides most media URLs behind a signature challenge that has to be
# solved in JavaScript. Without a runtime yt-dlp still extracts titles and
# formats perfectly and then fails every download with HTTP 403, which reads
# as a broken tool rather than a missing dependency.
JS_RUNTIME_MISSING=0
if runtime="$(js_runtime)"; then
    info "YouTube downloads: $runtime present"
else
    # Deliberately not added to $needed, unlike every other missing dependency
    # here. Ubuntu can provide nodejs 18, below yt-dlp's floor of 22, so
    # installing it would spend a privileged apt transaction producing a
    # runtime that js_runtime() then rejects, leaving downloads failing with
    # the same HTTP 403 as before while looking like the problem was handled.
    JS_RUNTIME_MISSING=1
    warn "YouTube downloads need Deno >= 2.3, Node >= 22, or Bun 1.2.11-1.3.14."
    warn "Distribution nodejs packages are usually older than that, so this is"
    warn "not installed for you. Install one of them yourself:"
    warn "  https://github.com/yt-dlp/yt-dlp/wiki/EJS"
fi

if (( ${#needed[@]} == 0 )); then
    # A missing runtime is no longer in $needed, so an unqualified "satisfied"
    # here would contradict the warning printed immediately above it.
    if (( JS_RUNTIME_MISSING )); then
        info "All apt-installable dependencies satisfied; a JavaScript runtime is still needed"
    else
        info "All system dependencies satisfied"
    fi
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

# --------------------------------------------------------------- desktop entry

# The GUI's launcher in the desktop's app grid. Generated with this checkout's
# absolute path on every install, so a moved checkout is healed by re-running
# this script, and the fresh mtime nudges a running GNOME Shell to re-read an
# entry it may have cached. Written with shell builtins only, so a failure
# cannot leave a half-written file behind.
#
# Installed only when the GUI can actually run; a menu entry has no terminal
# to explain a missing dependency in, so it should not exist before then.
desktop_string_escape() {
    local input="$1" output="" char
    while [[ -n "$input" ]]; do
        char="${input:0:1}"
        input="${input:1}"
        case "$char" in
            \\) output+="\\\\" ;;
            $'\n') output+='\n' ;;
            $'\t') output+='\t' ;;
            $'\r') output+='\r' ;;
            *) output+="$char" ;;
        esac
    done
    printf '%s' "$output"
}

desktop_exec_escape() {
    local input="$1" output="" char
    while [[ -n "$input" ]]; do
        char="${input:0:1}"
        input="${input:1}"
        case "$char" in
            \\) output+="\\\\\\\\" ;;
            \"|\`|'$') output+="\\\\$char" ;;
            '%') output+='%%' ;;
            $'\n') output+='\n' ;;
            $'\t') output+='\t' ;;
            $'\r') output+='\r' ;;
            *) output+="$char" ;;
        esac
    done
    printf '"%s"' "$output"
}

REPO_DIR="$(dirname "$(readlink -f "$0")")"
readonly REPO_DIR
if find_gui_python >/dev/null 2>&1; then
    icons_root="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
    mkdir -p "$apps_dir" "$icons_root/scalable/apps"
    cp "$REPO_DIR/desktop/$APP_ID.svg" "$icons_root/scalable/apps/$APP_ID.svg"

    # A cache file in the user's icon theme, left behind by some other
    # installer, is trusted over the directory contents whenever it is newer
    # than the top-level directory, so an icon copied in afterwards stays
    # invisible and the app shows a generic gear. Refresh a cache that
    # exists; never create one, since a cacheless directory is always
    # scanned directly and cannot go stale.
    if [[ -f "$icons_root/icon-theme.cache" ]] \
        && command -v gtk-update-icon-cache >/dev/null; then
        gtk-update-icon-cache -f -t "$icons_root" >/dev/null 2>&1 || true
    fi
    touch "$icons_root"
    gui_path="$REPO_DIR/ipod-gui.sh"
    desktop_exec="$(desktop_exec_escape "$gui_path")"
    if [[ "$gui_path" == *%* ]]; then
        desktop_runner="$(command -v env)"
        desktop_exec="$desktop_runner $desktop_exec"
    fi
    desktop_tryexec="$(desktop_string_escape "$gui_path")"
    printf '%s\n' \
        '[Desktop Entry]' \
        'Name=iPod Shuffle' \
        'GenericName=iPod Manager' \
        'Comment=Load music onto an iPod shuffle without iTunes' \
        "Exec=$desktop_exec" \
        "TryExec=$desktop_tryexec" \
        "Icon=$APP_ID" \
        'Terminal=false' \
        'Type=Application' \
        'Categories=AudioVideo;Audio;GTK;' \
        'Keywords=iPod;shuffle;music;player;sync;' \
        'StartupNotify=true' \
        > "$desktop_file"
    if command -v update-desktop-database >/dev/null; then
        update-desktop-database "$apps_dir" 2>/dev/null || true
    fi
    info "Desktop entry installed (look for iPod Shuffle in the app grid)"
else
    info "Desktop entry skipped until the GUI dependencies are installed"
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

# The same probes --check reports, run again now that the install has had its
# turn. One list rather than a verification section written in its own words:
# a caller that asked --check what this machine could do and a person reading
# the end of an install have to be told the same thing.
probe_capabilities
report_capabilities

printf '\n'
info "Done."
info "  GUI:  ./ipod-gui.sh"
info "  CLI:  ./ipod-sync.sh ~/Music/somefolder"
