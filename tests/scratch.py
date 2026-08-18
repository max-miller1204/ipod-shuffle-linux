#!/usr/bin/env python3
"""Temporary directories the check that built them takes away again.

Every check builds its fixtures somewhere disposable - a sandbox HOME, a
stand-in iPod, a library to scan - and then exits. Nothing removed them, so a
machine that runs the suite often kept one tree per directory per run, for
every run it had ever done.

Registering the removal where the directory is made keeps that decision next
to it, rather than in an exit path every check would have to remember and that
a check exiting on its first failure would skip anyway.

`IPOD_KEEP_SCRATCH=1` keeps them and says where they are, for looking at what
a check actually built.
"""

import atexit
import contextlib
import os
import shutil
import sys
import tempfile

_MADE: list[str] = []


def directory(*arguments, **keywords) -> str:
    """A temporary directory, removed when this process ends.

    Takes what `tempfile.mkdtemp` takes, and answers the same thing.
    """
    path = tempfile.mkdtemp(*arguments, **keywords)
    _MADE.append(path)
    return path


def _erase(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
    if not os.path.exists(path):
        return
    # What is left is a fixture a check made unreadable on purpose, to prove
    # the product says so rather than skipping the album quietly. Its owner
    # can still chmod it back, top down, because a directory has to be
    # readable before what is under it can be listed.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    for root, directories, _ in os.walk(path):
        for name in directories:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(root, name), 0o700)
    shutil.rmtree(path, ignore_errors=True)


@atexit.register
def _remove() -> None:
    if os.environ.get("IPOD_KEEP_SCRATCH"):
        for path in _MADE:
            print(f"scratch kept: {path}", file=sys.stderr)
        return
    for path in _MADE:
        # This runs with the check already on its way out, so a directory it
        # removed itself, or one a stopped subprocess still holds, is not a
        # reason to fail a check that has otherwise passed.
        _erase(path)
