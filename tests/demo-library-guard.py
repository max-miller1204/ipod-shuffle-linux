#!/usr/bin/env python3
"""Checks the guard that decides whether the demo builder may empty a folder.

`tools/demo-library.py` rebuilds by deleting the directory it was handed, and
that directory is whatever was typed on the command line, so this guard is the
only thing standing between a mistyped path and a music library. It is the one
irreversible step in the tool, and the only one worth its own check.

Every directory here is made inside a temporary folder of this check's own,
because a test of a delete must never be able to name a path it did not build.
"""

import importlib.util
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Loaded by path because the tool is a hyphenated script rather than a module,
# and running the real guard is the whole point: a copy of its rules here
# would agree with itself while the tool did something else.
_spec = importlib.util.spec_from_file_location(
    "demo_library", REPO / "tools" / "demo-library.py"
)
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


def claim(root, keep=False):
    """Run the guard, answering (the directory it took, why it refused)."""
    try:
        return demo.claim_root(root, keep), None
    except SystemExit as refusal:
        return None, str(refusal)


with tempfile.TemporaryDirectory() as workspace:
    workspace = Path(workspace)

    # A directory this tool did not build is refused, and everything in it is
    # still there afterwards - the mistyped ~/Music this guard exists for.
    library = workspace / "Music"
    (library / "Pixies" / "Doolittle").mkdir(parents=True)
    keeper = library / "Pixies" / "Doolittle" / "Debaser.mp3"
    keeper.write_bytes(b"a track somebody would miss")
    taken, refusal = claim(library)
    assert taken is None, "the guard cleared a directory it had not built"
    assert "Refusing to build in" in (refusal or ""), refusal
    assert keeper.read_bytes() == b"a track somebody would miss", (
        "a refused build deleted something anyway"
    )

    # --keep is no way in either. It writes rather than deletes, but leaving
    # the marker behind in somebody's music folder would make the next rebuild
    # of that path a deletion the guard then waves through.
    taken, refusal = claim(library, keep=True)
    assert taken is None, refusal
    assert not (library / demo.MARKER).exists(), (
        "a refused build planted the marker that lets the next one delete"
    )

    # A symlink is refused for what it points at. The tool resolves the path
    # it was handed, and the resolved directory is the one that would go.
    pointer = workspace / "pointer"
    pointer.symlink_to(library)
    taken, refusal = claim(pointer)
    assert taken is None, "a symlink got past the guard its target would not"
    assert keeper.exists(), "a refused build followed a link and deleted"

    # Something that is not a directory at all is said so plainly rather than
    # reaching rmtree and failing there.
    notes = workspace / "notes.txt"
    notes.write_text("not a demo", encoding="utf-8")
    taken, refusal = claim(notes)
    assert taken is None and "Not a directory" in (refusal or ""), refusal
    assert notes.read_text(encoding="utf-8") == "not a demo"

    # An empty directory is claimed: it is what building somewhere new looks
    # like a moment before the first file lands.
    empty = workspace / "empty"
    empty.mkdir()
    taken, refusal = claim(empty)
    assert taken == empty, refusal
    assert (empty / demo.MARKER).is_file(), "a claimed directory carries no marker"

    # So is one that does not exist yet, and the resolved path is what comes
    # back, because that is where the caller then builds.
    fresh = workspace / "link-to-workspace" / "fresh"
    (workspace / "link-to-workspace").symlink_to(workspace / "elsewhere")
    (workspace / "elsewhere").mkdir()
    taken, refusal = claim(fresh)
    assert taken == workspace / "elsewhere" / "fresh", (taken, refusal)
    assert (taken / demo.MARKER).is_file()

    # A directory carrying this tool's own marker is a demo it built, so a
    # rebuild empties it. That is what makes a rebuild reproducible rather
    # than a fresh build on top of whatever the last one left.
    leftover = empty / "home" / "Music" / "from last time.mp3"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"stale")
    taken, refusal = claim(empty)
    assert taken == empty, refusal
    assert not leftover.exists(), "a rebuild kept what the last build left"
    assert (empty / demo.MARKER).is_file(), "a rebuild left no marker behind"

    # And --keep on a demo of its own adds to it rather than starting over.
    kept = empty / "home" / "Music" / "added.mp3"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"added to the demo")
    taken, refusal = claim(empty, keep=True)
    assert taken == empty, refusal
    assert kept.exists(), "--keep rebuilt the demo instead of adding to it"

print("demo library guard ok")
