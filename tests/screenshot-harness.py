#!/usr/bin/env python3
"""Build the canonical fixture and render representative 1x and 2x shots."""

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

if not os.environ.get("SHUFFLE_HEADLESS_TEST"):
    raise SystemExit(
        subprocess.run(
            [sys.executable, REPO / "tools/headless-run.py", sys.executable, __file__]
        ).returncode
    )
HEIGHT = 760
SIGNATURE = b"\x89PNG\x0d\x0a\x1a\x0a"

# The four albums tools/demo-library.py builds, which are what the canonical
# shot is of. Spelled out here rather than imported from the builder, so a
# fixture that quietly stopped carrying them is a failure rather than an
# agreement between two halves of the same change.
ALBUMS = {"Field Notes", "Warm Ridge", "Nightbus", "Slow Copper"}

# The window's two colour schemes, as one number each: the mean of a shot's
# colour channels. The dark scheme measures 32 of a possible 255 over the
# canonical library page and the light one 226, so halfway between them tells
# which scheme a shot came out in and nothing finer.
DARK_CEILING = 128

# Two renders of one layout at different densities are not the same pixels:
# glyphs are hinted onto whichever pixel grid they are painted on, so the 2x
# shot scaled back down lands a fraction of a pixel off the 1x one along every
# edge. These bound that and nothing more. A shot that took a breakpoint the
# other one did not - a folded-away sidebar, the whole page shifted sideways -
# is two orders of magnitude past either: the defect this pins measured 15.1
# and 11.7% where an agreeing pair measures 0.2 and 0.14%.
SCALE_MEAN_TOLERANCE = 5.0
SCALE_GROSS_TOLERANCE = 2.0


def png_size(path):
    """The image's own dimensions, off the IHDR chunk PNG opens with.

    The bytes are the format's, not this repository's: the signature, then a
    length and the chunk type, then width and height as big-endian uint32.
    This is the tool's output contract - a file that really is the pixel box
    that was asked for - and not a check of the scale, which is pinned inside
    the tool where the render node can be measured against it.
    """
    header = path.read_bytes()[:24]
    assert header[:8] == SIGNATURE, f"{path} is not a PNG"
    assert header[12:16] == b"IHDR", f"{path} does not open with IHDR"
    return struct.unpack(">II", header[16:24])


def shoot(root, page, width, scale, target):
    """Render one shot, and let a refusal to render it fail the check.

    A non-zero exit is the tool refusing to write a shot it cannot vouch for -
    the wrong page, a library that scanned to nothing, a render that came out
    at the wrong scale, or a window the display never gave the asked-for width
    - so checking that it succeeded is the first thing every case here does.
    """
    subprocess.run(
        [
            sys.executable, "tools/shoot.py", "--fixture", str(root),
            "--page", page, "--width", str(width), "--scale", str(scale),
            "--output", str(target),
        ],
        cwd=REPO,
        check=True,
    )
    assert png_size(target) == (width * scale, HEIGHT * scale), (
        f"{target} is {png_size(target)}, not the {width}x{HEIGHT} layout "
        f"rendered at {scale}x"
    )
    assert target.stat().st_size > 10_000, (
        f"{target} holds too little to be a window"
    )
    return target


def rows(path, divide=1):
    """The image's pixels, row by row, optionally scaled down by `divide`.

    Read back through GdkPixbuf rather than off the file, because the rows a
    PNG holds are filtered and interlaced against each other and say nothing
    about what the image looks like. The rowstride is dropped here: it is
    padding at the end of each row that no pixel is stored in.
    """
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    if divide != 1:
        image = image.scale_simple(
            image.get_width() // divide,
            image.get_height() // divide,
            GdkPixbuf.InterpType.BILINEAR,
        )
    data = image.get_pixels()
    stride = image.get_rowstride()
    line = image.get_width() * image.get_n_channels()
    return [data[y * stride: y * stride + line] for y in range(image.get_height())]


def brightness(path):
    """The mean of the image's colour channels, 0 for black and 255 for white.

    Alpha is dropped rather than averaged in: every pixel here is opaque, so
    including it would pull every shot a quarter of the way towards white and
    leave the two schemes reading closer together than they are.
    """
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    channels = image.get_n_channels()
    data = image.get_pixels()
    stride = image.get_rowstride()
    line = image.get_width() * channels
    total = 0
    counted = 0
    for y in range(image.get_height()):
        row = data[y * stride: y * stride + line]
        for start in range(0, len(row), channels):
            total += sum(row[start:start + 3])
            counted += 3
    return total / counted


def difference(one, other):
    """How far apart two images are: mean channel distance, and gross share.

    The mean answers whether they are the same picture painted differently;
    the share of channels that are nowhere near each other answers whether
    they are the same picture at all, which a mean over a mostly-flat window
    dilutes away.
    """
    assert len(one) == len(other), "images differ in height"
    total = 0
    gross = 0
    channels = 0
    for left, right in zip(one, other):
        assert len(left) == len(right), "images differ in width"
        channels += len(left)
        for a, b in zip(left, right):
            distance = abs(a - b)
            total += distance
            gross += distance > 48
    return total / channels, 100 * gross / channels


# The shots outlive the fixture they were taken of: the fixture is six encoded
# tracks, four covers and a stand-in volume, and leaving one of those in /tmp
# per run is what the developers this harness is documented for would collect.
evidence = os.environ.get("SCREENSHOT_EVIDENCE_DIR")
out = Path(evidence) if evidence else Path(tempfile.mkdtemp(prefix="shuffle-shots-"))

with tempfile.TemporaryDirectory(prefix="screenshot-harness-") as workspace:
    root = Path(workspace) / "demo"
    subprocess.run(
        [sys.executable, "tools/demo-library.py", str(root)],
        cwd=REPO,
        check=True,
        stdout=subprocess.DEVNULL,
    )

    # The fixture read the way the window reads it, before any of it is
    # rendered. Tags are read in whichever interpreter has mutagen, which on a
    # machine that never ran install.sh is none of them, and a window with no
    # tags still comes up: the six tracks it knows only the filenames of
    # collapse into a single "Unknown album" tile over "All 1". That is a shot
    # of a library nobody has, and once it is a PNG in an artifact nothing
    # tells it from the canonical one - so the fixture is read here, and a
    # machine that cannot read it fails rather than publishing it.
    #
    # The cache is redirected first because the reader writes the cover art it
    # extracts there, and this one goes with the fixture rather than into the
    # cache of whoever ran the check.
    os.environ["XDG_CACHE_HOME"] = str(Path(workspace) / "cache")
    sys.path.insert(0, str(REPO))
    from ipod_gui import tags  # noqa: E402

    records, complete = tags.scan_tracks(root / "home" / "Music")
    assert complete, f"the tag reader did not read {root} through"
    albums = {record.get("album") for record in records}
    assert albums == ALBUMS, (
        f"the fixture under {root} reads as {sorted(map(str, albums))}, not "
        f"the {sorted(ALBUMS)} the canonical shot is of: no interpreter here "
        "has mutagen, so point IPOD_VENV_PYTHON at one that does"
    )

    library = shoot(root, "library", 1180, 1, out / "library-1180-1x.png")
    playlists = shoot(root, "playlists", 760, 2, out / "playlists-760-2x.png")
    # The width the sidebar is shown at, rendered again at the other scale:
    # the README's own 2x command, and the pair the scale is checked against
    # below. It has to be a width above the sidebar's collapse threshold,
    # because a breakpoint both scales take the same way hides the defect.
    library_2x = shoot(root, "library", 1180, 2, out / "library-1180-2x.png")

    # The scheme the shot came out in, which the tool pins rather than
    # inherits. Adw.StyleManager follows the desktop's colour-scheme
    # preference, read over whatever session bus the process was handed, so
    # unpinned this one command is dark for a developer whose desktop prefers
    # dark and light on a runner with no desktop to ask - the same window in
    # two entirely different pictures, one of them not the scheme
    # docs/screenshot.png and the artifacts beside it are in. Measured over
    # the whole image rather than at a sampled pixel, because what changes
    # between the two is every surface in the window at once.
    lit = brightness(library)
    assert lit < DARK_CEILING, (
        f"{library} averages {lit:.0f} of 255 across its colour channels, so "
        "it came out in the light scheme: the shot is following this "
        "machine's colour-scheme preference rather than the dark scheme "
        "tools/shoot.py pins"
    )

    # The harness's headline claim, checked against itself: one command, run
    # twice, is one file. Anything the window is still doing when the shot is
    # taken shows up here - a spinner mid-turn, a caret mid-blink, a repaint
    # still queued behind the scan that asked for it - because none of those
    # land in the same place twice. The playlist shot is the one used: the
    # library shot at 1180 shows the sidebar, and the sidebar reports the free
    # space of the filesystem the fixture is on, which is the host's number
    # and not the harness's to pin.
    again = shoot(root, "playlists", 760, 2, Path(workspace) / "playlists-again.png")
    assert playlists.read_bytes() == again.read_bytes(), (
        f"{playlists} and {again} are the same shot taken twice and came out "
        "different: the window was still moving when they were taken"
    )

    # --scale is the raster density and nothing else. Scaled back down, the 2x
    # shot has to be the 1x shot: same layout, same breakpoints, same window.
    # It is checked at 1180 because that is a width the sidebar is shown at,
    # and a scale that reaches the window instead of the render node collapses
    # it - a layout no user at that width sees.
    mean, gross = difference(rows(library), rows(library_2x, divide=2))
    assert mean < SCALE_MEAN_TOLERANCE and gross < SCALE_GROSS_TOLERANCE, (
        f"{library_2x} scaled back down is not {library}: mean channel "
        f"distance {mean:.2f} over {SCALE_MEAN_TOLERANCE}, or {gross:.2f}% of "
        f"channels grossly apart over {SCALE_GROSS_TOLERANCE}% - --scale "
        "changed the layout rather than only the density it is rendered at"
    )

print(f"screenshot harness ok: {out}")
