#!/usr/bin/env bash
#
# Build and install the Flatpak locally.
#
# Needs org.flatpak.Builder and the GNOME SDK, both from Flathub:
#   flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//50

set -euo pipefail

readonly APP_ID="io.github.max_miller1204.IpodShuffle"
HERE="$(dirname "$(readlink -f "$0")")"
readonly HERE
readonly MANIFEST="$HERE/$APP_ID.yml"

# Kept outside the checkout because the manifest takes the repository root as
# a source directory, and a build tree inside it would be copied into itself.
readonly BUILD_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/ipod-shuffle-flatpak"

command -v flatpak >/dev/null || { echo "flatpak is not installed" >&2; exit 1; }
flatpak info org.flatpak.Builder >/dev/null 2>&1 || {
    echo "org.flatpak.Builder is missing. Install it with:" >&2
    echo "  flatpak install --user flathub org.flatpak.Builder" >&2
    exit 1
}

mkdir -p "$BUILD_ROOT"

# rofiles-fuse is disabled because it fails on several filesystems, and its
# only role is guarding against a build that writes outside its own tree.
flatpak run org.flatpak.Builder \
    --user --install --force-clean --disable-rofiles-fuse \
    --state-dir="$BUILD_ROOT/state" \
    "$BUILD_ROOT/build" \
    "$MANIFEST"

echo
echo "Installed. Run it with:"
echo "  flatpak run $APP_ID"
