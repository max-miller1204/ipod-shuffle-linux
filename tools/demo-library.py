#!/usr/bin/env python3
"""Builds the demo library the README screenshots are taken against.

The screenshots in `docs/` show a library nobody has: four albums with cover
art the app generates itself, a playlist that has reached the device and one
that has not, and an iPod called MAX SHUFFLE. That library used to be built by
hand in a temporary directory, which is why `docs/screenshot.png` went stale
and could not simply be retaken - the directory was gone and nothing recorded
what had been in it.

So it is built here instead. Everything the window shows is real: the tracks
are real MP3s with real tags, the device is a stand-in volume that
`ipod-sync.sh` has actually written to, and the "On iPod" badges and playlist
dots are the app's own answers about that volume rather than a fixture's
claims about itself.

    tools/demo-library.py /tmp/shuffle-demo

prints the command that launches the app against what it built.

The album covers are not files. `make_cover` generates a placeholder from an
"artist/title" seed when a track has no embedded art, so the colours in the
screenshot follow from the names below and nothing else - which is why the
same four albums always come out the same four colours.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

# The device's name is its mount point's basename, the way a real volume's is.
IPOD_NAME = "MAX SHUFFLE"

# Four albums, because the screenshot's state pills count albums and reading
# "All 4 / On iPod 1 / In library 3" is the point of the shot. Warm Ridge is
# the one that gets synced, so it is the only one wearing an "On iPod" badge.
ALBUMS = [
    ("Ana Petrov", "Field Notes", ["Paper Boats", "Coastal Road"]),
    ("Elle Marchetti", "Warm Ridge", ["Low Sun", "Ridge Line"]),
    ("Kova", "Nightbus", ["Last Stop"]),
    ("The Fen", "Slow Copper", ["Slow Copper"]),
]

# One playlist that reaches the device and one that does not, because the dot
# beside a playlist means the same thing it means beside a track and a shot
# with only one state cannot show that.
SYNCED_PLAYLIST = "Morning Ride"
LOCAL_PLAYLIST = "Downloads"


def run(command, **kwargs):
    """Run a command, failing loudly rather than leaving a half-built demo."""
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        sys.exit(
            f"{command[0]} failed ({result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def write_track(path, artist, album, title, number):
    """One second of silence, tagged the way a ripped file is tagged.

    Real audio rather than a file of zeroes: the app reads durations with
    mutagen and shows them, and the sync copies whatever it is handed, so a
    track that is not decodable would be a demo of the wrong thing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "1", "-c:a", "libmp3lame", "-b:a", "128k",
        "-metadata", f"artist={artist}",
        "-metadata", f"album={album}",
        "-metadata", f"title={title}",
        "-metadata", f"track={number}",
        str(path),
    ])


def build_library(music):
    """The four albums, as Artist/Album/NN - Title.mp3."""
    built = {}
    for artist, album, titles in ALBUMS:
        paths = []
        for number, title in enumerate(titles, start=1):
            path = music / artist / album / f"{number:02d} - {title}.mp3"
            write_track(path, artist, album, title, number)
            paths.append(path)
        built[album] = paths
    return built


def build_playlists(playlists_dir, tracks):
    """The two playlists, as the M3U files the app itself would have written."""
    playlists_dir.mkdir(parents=True, exist_ok=True)
    lists = {
        SYNCED_PLAYLIST: tracks["Warm Ridge"],
        LOCAL_PLAYLIST: tracks["Nightbus"] + tracks["Slow Copper"],
    }
    for name, entries in lists.items():
        path = playlists_dir / f"{name}.m3u"
        body = "".join(f"{entry}\n" for entry in entries)
        path.write_text(f"#EXTM3U\n{body}", encoding="utf-8")
    return lists


def pin_rendering(home):
    """Pin the demo's GTK scale, so a screenshot is the same on any machine.

    GTK sizes most of the window from the desktop's font DPI, which is 192 on
    a HiDPI session and 96 on the one the existing screenshots were taken on -
    the difference is a sidebar twice as wide and three albums to a row rather
    than four. XSETTINGS wins over GDK_DPI_SCALE, so the value is written into
    the demo's own config rather than the developer's: the demo replaces HOME
    anyway, and nothing here touches the real desktop.
    """
    settings = home / ".config/gtk-4.0/settings.ini"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        # gtk-xft-dpi counts 1024ths of a point, so 96 dpi is 96 * 1024.
        "[Settings]\ngtk-xft-dpi=98304\ngtk-font-name=Cantarell 11\n",
        encoding="utf-8",
    )


def build_ipod(root):
    """An empty volume shaped like a shuffle, for ipod-sync.sh to fill.

    Speakable and Device are what tell the sync this is a shuffle rather than
    any other VFAT stick: without them it refuses the volume, which is the
    check that stops a sync from unpacking itself over somebody's camera card.
    """
    ipod = root / IPOD_NAME
    for part in (
        "iPod_Control/iTunes",
        "iPod_Control/Music",
        "iPod_Control/Speakable/System",
        "iPod_Control/Device",
    ):
        (ipod / part).mkdir(parents=True, exist_ok=True)
    speakable = ipod / "iPod_Control/Speakable/System/battery.wav"
    speakable.write_bytes(b"spoken battery prompt\n")
    (ipod / "iPod_Control/Device/SysInfo").write_bytes(b"device identity\n")
    return ipod


def sync(ipod, sources):
    """Put one album and one playlist on the device, using the shipped sync.

    Running the real script rather than staging files by hand is what makes
    the badges in the screenshot true: the app reads the device back and says
    what it finds there, so a fixture that only claimed to have synced would
    show every album as "In library".
    """
    command = [
        str(REPO / "ipod-sync.sh"),
        "--ipod", str(ipod),
        "--playlist-voiceover",
        *[str(source) for source in sources],
    ]
    environment = {**os.environ, "IPOD_ASSUME_YES": "1"}
    return subprocess.run(
        command, capture_output=True, text=True, env=environment
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="where to build the demo")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="add to an existing demo instead of rebuilding it",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="build the files but leave the device empty",
    )
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed to write the demo's tracks")

    root = args.root.expanduser().resolve()
    if root.exists() and not args.keep:
        shutil.rmtree(root)
    home = root / "home"
    music = home / "Music"
    music.mkdir(parents=True, exist_ok=True)

    pin_rendering(home)
    tracks = build_library(music)
    playlists = build_playlists(music / "Playlists", tracks)
    ipod = build_ipod(root)

    if not args.no_sync:
        synced = sync(
            ipod,
            [*tracks["Warm Ridge"], music / "Playlists" / f"{SYNCED_PLAYLIST}.m3u"],
        )
        if synced.returncode != 0:
            print(
                "warning: the sync did not finish, so the device will look "
                f"empty:\n{synced.stderr.strip() or synced.stdout.strip()}",
                file=sys.stderr,
            )

    print(f"Demo built in {root}")
    print(f"  library    {music}")
    print(f"  playlists  {', '.join(sorted(playlists))}")
    print(f"  device     {ipod}")
    print()
    print("Launch the app against it with:")
    print()
    print(
        "  # Quit any running Shuffle first: the app is single-instance, so a\n"
        "  # second launch hands off to the one already running and this one\n"
        "  # never opens a window of its own.\n"
        "  #\n"
        "  # For docs/screenshot.png, run it inside a nested server instead of\n"
        "  # the real desktop - the window then comes up at exactly 1180x760\n"
        "  # with four albums to a row, on any machine:\n"
        "  #   Xephyr :9 -screen 1300x860 -dpi 96 -br -noreset &\n"
        "  #   ...and add GDK_BACKEND=x11 GSK_RENDERER=cairo DISPLAY=:9\n"
        "  # GSK_RENDERER because Xephyr offers no accelerated backend."
    )
    print()
    # IPOD_VENV_PYTHON because replacing HOME also hides install.sh's
    # virtualenv from the tag reader, and a library with no artist or album is
    # a library with no album grid - the thing the screenshot is of.
    print(
        f'  env HOME="{home}" \\\n'
        f'      FAKE_IPOD_MOUNT="{ipod}" \\\n'
        f'      IPOD_VENV_PYTHON="{Path.home() / "ipod-tools/venv/bin/python"}" \\\n'
        f'      PATH="{REPO / "tests" / "bin"}:$PATH" \\\n'
        f'      {REPO / "ipod-gui.py"}'
    )


if __name__ == "__main__":
    main()
