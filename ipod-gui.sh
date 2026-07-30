#!/usr/bin/env bash
#
# Launch the GTK4 GUI.
#
# PyGObject is installed by the distro and therefore belongs to the system
# interpreter, which is frequently not the python3 first on PATH. This picks
# one that can actually import GTK rather than assuming.

set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

if ! gui_python="$(find_gui_python)"; then
    err "No Python with GTK4 bindings found."
    err "Install them with:"
    err "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
    exit 1
fi

exec "$gui_python" "$(dirname "$(readlink -f "$0")")/ipod-gui.py" "$@"
