#!/usr/bin/env python3
"""Build the canonical fixture and render representative 1x and 2x shots."""

import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix="screenshot-harness-")) / "demo"
out = Path(os.environ.get("SCREENSHOT_EVIDENCE_DIR", root.parent / "shots"))
subprocess.run(["/usr/bin/python3", "tools/demo-library.py", str(root)], cwd=repo, check=True, stdout=subprocess.DEVNULL)
for page, width, scale in (("library", 1180, 1), ("playlists", 760, 2)):
    target = out / f"{page}-{width}-{scale}x.png"
    subprocess.run(
        [
            "/usr/bin/python3", "tools/shoot.py", "--fixture", str(root),
            "--page", page, "--width", str(width), "--scale", str(scale),
            "--output", str(target),
        ],
        cwd=repo,
        check=True,
    )
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert target.stat().st_size > 10_000
print(f"screenshot harness ok: {out}")
