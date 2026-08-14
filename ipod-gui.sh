#!/usr/bin/env bash
#
# Launch the GTK4 GUI.
#
# PyGObject comes from the distro, so the python3 first on PATH frequently
# cannot import GTK at all. find_gui_python in lib.sh decides which interpreter
# this runs in, and why; nothing here assumes one.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

if ! gui_python="$(find_gui_python)"; then
    err "No Python with GTK4 bindings found."
    err "Install them with:"
    err "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
    exit 1
fi

exec "$gui_python" "$(dirname "$(readlink -f "$0")")/ipod-gui.py" "$@"
