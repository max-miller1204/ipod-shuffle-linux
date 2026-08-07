#!/usr/bin/env python3
"""Builds the demo library `docs/screenshot.png` is taken against.

That shot shows a library nobody has: four albums with cover art the app
generates itself, a playlist that has reached the device and one that has not,
and an iPod called MAX SHUFFLE. The library used to be built by hand in a
temporary directory, which is why the screenshot went stale and could not
simply be retaken - the directory was gone and nothing recorded what had been
in it.

So it is built here instead. Everything the window shows is real: the tracks
are real MP3s with real tags, the device is a stand-in volume that
`ipod-sync.sh` has actually written to, and the "On iPod" badges and playlist
dots are the app's own answers about that volume rather than a fixture's
claims about itself.

    tools/demo-library.py /tmp/shuffle-demo

prints the command that launches the app against what it built.

`docs/screenshot.png` is the only shot this reproduces. `docs/now-playing.png`
is of a track that runs 3:34, and every track here is one second of silence,
so retaking that one from this fixture would give a visibly different bar.

The covers are drawn here and embedded in the tracks, because the shot is
under a sentence about the app reading embedded art off the files, and four
albums falling through to the app's own placeholder would illustrate the
opposite. They are drawn from the "artist/album" name and nothing else, so
the same four albums always come out the same four covers on any machine.
"""

import argparse
import colorsys
import hashlib
import struct
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

# The device's name is its mount point's basename, the way a real volume's is.
IPOD_NAME = "MAX SHUFFLE"

# Left in the root of every demo this builds, and the only thing that lets it
# clear one out again. Rebuilding deletes the directory it was handed, and that
# directory is whatever was typed on the command line, so it has to be able to
# tell a demo of its own from a music folder somebody mistyped - the same
# reason assert_shuffle() reads a volume's shape instead of trusting its path.
MARKER = ".demo-library"

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
    """Run a command, failing loudly rather than leaving a half-built demo.

    Nothing here is answering prompts, and the output is captured, so a child
    that asked one would wait forever on a question nobody can see. Closing
    stdin turns that into an error instead of a hang.
    """
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )
    if result.returncode != 0:
        sys.exit(
            f"{command[0]} failed ({result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def png(width, height, rows):
    """Rows of RGB bytes, as a PNG file.

    Written out by hand rather than with an imaging library, because the tool
    otherwise needs nothing installed but ffmpeg and a cover is a few hundred
    bytes of header around what zlib already does.
    """
    body = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(kind, payload):
        block = kind + payload
        return (
            struct.pack(">I", len(payload))
            + block
            + struct.pack(">I", zlib.crc32(block) & 0xFFFFFFFF)
        )

    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        # 8 bits a channel, colour type 2 (RGB), no interlacing.
        chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(body, 9)),
        chunk(b"IEND", b""),
    ))


def shade(hue, saturation, value):
    """One colour, as the three bytes a pixel is written as."""
    return bytes(
        round(channel * 255)
        for channel in colorsys.hsv_to_rgb(hue % 1.0, saturation, value)
    )


def cover_art(seed, size=600):
    """One album's cover, drawn from its name and nothing else.

    A hash rather than a palette lookup so adding a fifth album needs no
    decision, and a hash rather than anything random so the screenshot is the
    same picture on any machine.

    The shapes take their share of the digest rather than being the same
    shapes in different colours, because two names can hash to neighbouring
    hues and two covers that differ only by a few degrees of hue read as one
    cover repeated at the size the album grid draws them.
    """
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    hue = digest[0] / 256
    field = shade(hue, 0.50, 0.34 + digest[1] % 32 / 250)
    corner = shade(hue + 0.07, 0.42, 0.62)
    disc = shade(hue + 0.5, 0.26 + digest[2] % 32 / 250, 0.80)

    split_across = digest[3] % 2
    radius = size * (3 + digest[4] % 3) / 20
    centre_x = size * (4 + digest[5] % 5) / 12
    centre_y = size * (4 + digest[6] % 5) / 12

    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            if (x - centre_x) ** 2 + (y - centre_y) ** 2 < radius**2:
                row += disc
            elif (x + y > size) if split_across else (x > y):
                row += corner
            else:
                row += field
        rows.append(row)
    return png(size, size, rows)


def write_track(path, artist, album, title, number, cover):
    """One second of silence, tagged the way a ripped file is tagged.

    Real audio rather than a file of zeroes: the app reads durations with
    mutagen and shows them, and the sync copies whatever it is handed, so a
    track that is not decodable would be a demo of the wrong thing. The cover
    is attached the way a ripped file carries one, as an ID3 picture frame,
    because reading art off the file is what the album grid is a picture of.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-i", str(cover),
        "-map", "0:a", "-map", "1:v",
        "-t", "1", "-c:a", "libmp3lame", "-b:a", "128k",
        "-c:v", "copy", "-disposition:v", "attached_pic",
        "-id3v2_version", "3",
        "-metadata:s:v", "title=Album cover",
        "-metadata:s:v", "comment=Cover (front)",
        "-metadata", f"artist={artist}",
        "-metadata", f"album={album}",
        "-metadata", f"title={title}",
        "-metadata", f"track={number}",
        str(path),
    ])


def build_library(music):
    """The four albums, as Artist/Album/NN - Title.mp3."""
    built = {}
    # The covers only have to exist long enough to be embedded: what the
    # library holds afterwards is tracks carrying their own art, which is what
    # the app reads.
    with tempfile.TemporaryDirectory() as drawn:
        for artist, album, titles in ALBUMS:
            cover = Path(drawn) / f"{album}.png"
            cover.write_bytes(cover_art(f"{artist}/{album}"))
            paths = []
            for number, title in enumerate(titles, start=1):
                path = music / artist / album / f"{number:02d} - {title}.mp3"
                write_track(path, artist, album, title, number, cover)
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
    the demo's own config rather than the developer's, and nothing here
    touches the real desktop. GTK looks for this file wherever
    XDG_CONFIG_HOME points, which the launch command below pins at this
    directory: replacing HOME alone would leave a session that exports that
    variable reading the desktop's settings instead of this one.
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

    --yes is the script's own flag for answering its prompts, and it is what
    covers assert_shuffle's device-type warning on a volume this tool built
    rather than plugged in.
    """
    command = [
        str(REPO / "ipod-sync.sh"),
        "--ipod", str(ipod),
        "--yes",
        "--playlist-voiceover",
        *[str(source) for source in sources],
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def built_here(root):
    """Whether this tool owns `root`, so it may write over what is in it.

    An empty directory counts, because that is what building somewhere new
    leaves behind the moment before the first file lands in it. A directory
    that cannot be listed does not: refusing to read one is no reason to feel
    free to delete it.
    """
    if (root / MARKER).is_file():
        return True
    try:
        return not any(root.iterdir())
    except OSError:
        return False


def claim_root(root, keep):
    """Take the demo's directory and return it, refusing what is not ours.

    `tools/demo-library.py ~/Music` must not be a way to lose a music library,
    and a rebuild deletes the directory whole, so it is only ever a directory
    this tool built or an empty one. Resolving the path is part of the guard
    rather than something done to it beforehand: a symlinked root stands for
    the real directory, and the real directory is the one that would go.
    """
    root = Path(root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        sys.exit(f"Not a directory: {root}")
    if root.is_dir() and not built_here(root):
        sys.exit(
            f"Refusing to build in {root}: it holds something this tool did "
            f"not build, so there is no {MARKER} in it saying the contents are "
            "expendable. Build the demo somewhere new, or empty that "
            "directory yourself first."
        )
    if root.is_dir() and not keep:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text(
        "Built by tools/demo-library.py, and safe to delete - though deleting "
        "it makes the tool refuse to rebuild this directory.\n",
        encoding="utf-8",
    )
    return root


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

    root = claim_root(args.root, args.keep)
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
        "  # GSK_RENDERER because Xephyr offers no accelerated backend.\n"
        "  #\n"
        "  # Park the pointer off the window before grabbing - the screen is\n"
        "  # larger than the window for that - or whichever album card it\n"
        "  # happens to rest on wears its hover state in the shot:\n"
        "  #   DISPLAY=:9 xdotool mousemove 1290 850\n"
        "  #\n"
        "  # Then grab the window rather than the root. The screen is bigger\n"
        "  # than the window on purpose, so a root grab keeps that margin as a\n"
        "  # black band and the parked pointer as a cursor sitting in it:\n"
        "  #   DISPLAY=:9 import -window \"$(DISPLAY=:9 xdotool search \\\n"
        "  #     --name '^Shuffle$' | head -1)\" docs/screenshot.png\n"
        "  # With no window id to hand, crop the root back to the window - the\n"
        "  # window is at the origin, so the top-left 1180x760 is all of it:\n"
        "  #   DISPLAY=:9 import -window root -crop 1180x760+0+0 +repage \\\n"
        "  #     docs/screenshot.png"
    )
    print()
    # Replacing HOME does not by itself keep the demo out of the developer's
    # own files. XDG_CONFIG_HOME and XDG_CACHE_HOME override it wherever a
    # session exports them, and the app reads its config from the first: left
    # inherited, the demo would scan the music folders configured for the real
    # library, show those in the album grid, and write back into that config
    # from a throwaway run.
    #
    # IPOD_VENV_PYTHON because replacing HOME also hides install.sh's
    # virtualenv from the tag reader, and a library with no artist or album is
    # a library with no album grid - the thing the screenshot is of.
    print(
        f'  env HOME="{home}" \\\n'
        f'      XDG_CONFIG_HOME="{home / ".config"}" \\\n'
        f'      XDG_CACHE_HOME="{home / ".cache"}" \\\n'
        f'      FAKE_IPOD_MOUNT="{ipod}" \\\n'
        f'      IPOD_VENV_PYTHON="{Path.home() / "ipod-tools/venv/bin/python"}" \\\n'
        f'      PATH="{REPO / "tests" / "bin"}:$PATH" \\\n'
        f'      {REPO / "ipod-gui.py"}'
    )


if __name__ == "__main__":
    main()
