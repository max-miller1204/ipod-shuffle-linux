#!/usr/bin/env python3
"""Build the canonical fixture and render representative 1x and 2x shots."""

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HEIGHT = 760
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(path):
    """The image's own dimensions, off the IHDR chunk PNG opens with.

    The bytes are the format's, not this repository's: the signature, then a
    length and the chunk type, then width and height as big-endian uint32.
    Read rather than assumed because the size is the one thing about the shot
    that a wrong scale changes and a file the viewer still opens.
    """
    header = path.read_bytes()[:24]
    assert header[:8] == SIGNATURE, f"{path} is not a PNG"
    assert header[12:16] == b"IHDR", f"{path} does not open with IHDR"
    return struct.unpack(">II", header[16:24])


repo = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix="screenshot-harness-")) / "demo"
out = Path(os.environ.get("SCREENSHOT_EVIDENCE_DIR", root.parent / "shots"))
subprocess.run(
    [sys.executable, "tools/demo-library.py", str(root)],
    cwd=repo,
    check=True,
    stdout=subprocess.DEVNULL,
)
for page, width, scale in (("library", 1180, 1), ("playlists", 760, 2)):
    target = out / f"{page}-{width}-{scale}x.png"
    # A non-zero exit is the tool refusing to write a shot it cannot vouch
    # for - the wrong page, or a library that scanned to nothing - so the
    # check that it succeeded is most of the coverage here.
    subprocess.run(
        [
            sys.executable, "tools/shoot.py", "--fixture", str(root),
            "--page", page, "--width", str(width), "--scale", str(scale),
            "--output", str(target),
        ],
        cwd=repo,
        check=True,
    )
    assert png_size(target) == (width * scale, HEIGHT * scale), (
        f"{target} is {png_size(target)}, not the {width}x{HEIGHT} layout "
        f"rendered at {scale}x"
    )
    assert target.stat().st_size > 10_000, f"{target} holds too little to be a window"
print(f"screenshot harness ok: {out}")
