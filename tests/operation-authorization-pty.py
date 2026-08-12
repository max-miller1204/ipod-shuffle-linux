#!/usr/bin/env python3
"""Exercise destructive authorization through a real terminal."""

from __future__ import annotations

import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time


ROOT = Path(sys.argv[1]).resolve()
EVIDENCE = Path(sys.argv[2]).resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)


def make_ipod(root: Path, identity: str) -> tuple[Path, Path]:
    mount = root / identity
    track = mount / "iPod_Control" / "Music" / "Album" / "Keep.mp3"
    (mount / "iPod_Control" / "iTunes").mkdir(parents=True)
    (mount / "iPod_Control" / "Speakable").mkdir(parents=True)
    (mount / "iPod_Control" / "Device").mkdir(parents=True)
    track.parent.mkdir(parents=True)
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text(
        identity, encoding="utf-8"
    )
    track.write_bytes(b"keep me\n")
    return mount, track


def run_in_pty(args: list[str], env: dict[str, str], prompt: bytes | None = None):
    master, slave = pty.openpty()
    process = subprocess.Popen(
        args,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 15
    while prompt is not None and prompt not in output:
        if time.monotonic() >= deadline:
            process.kill()
            raise AssertionError(f"prompt never appeared: {output!r}")
        readable, _, _ = select.select([master], [], [], 0.2)
        if readable:
            output.extend(os.read(master, 4096))
    return process, master, output


def finish(process: subprocess.Popen[bytes], master: int, output: bytearray) -> bytes:
    deadline = time.monotonic() + 15
    while process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            raise AssertionError(f"terminal run did not finish: {output!r}")
        readable, _, _ = select.select([master], [], [], 0.2)
        if readable:
            try:
                output.extend(os.read(master, 4096))
            except OSError:
                pass
    while True:
        readable, _, _ = select.select([master], [], [], 0)
        if not readable:
            break
        try:
            output.extend(os.read(master, 4096))
        except OSError:
            break
    os.close(master)
    return bytes(output)


with tempfile.TemporaryDirectory(prefix="ipod-authorization-pty-") as workspace_text:
    workspace = Path(workspace_text)
    fake_bin = workspace / "bin"
    fake_bin.mkdir()
    findmnt = fake_bin / "findmnt"
    findmnt.write_text("#!/usr/bin/env bash\nprintf -- '-\\n'\n", encoding="utf-8")
    findmnt.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    swapped, swapped_track = make_ipod(workspace, "original-ipod")
    process, master, output = run_in_pty(
        [str(ROOT / "ipod-wipe.sh"), "--ipod", str(swapped)],
        env,
        b"Wipe this iPod?",
    )
    (swapped / "iPod_Control" / "Device" / "SysInfo").write_text(
        "replacement-ipod", encoding="utf-8"
    )
    os.write(master, b"y\n")
    swap_output = finish(process, master, output)
    (EVIDENCE / "wipe-device-swap-pty.txt").write_bytes(swap_output)
    assert process.returncode == 5, (process.returncode, swap_output)
    assert swapped_track.read_bytes() == b"keep me\n", "replacement device was wiped"
    assert b"unplugged or replaced mid-operation" in swap_output, swap_output

    automatic, automatic_track = make_ipod(workspace, "yes-on-terminal")
    process, master, output = run_in_pty(
        [str(ROOT / "ipod-wipe.sh"), "--ipod", str(automatic), "--yes"], env
    )
    yes_output = finish(process, master, output)
    (EVIDENCE / "wipe-yes-without-token-pty.txt").write_bytes(yes_output)
    assert process.returncode == 7, (process.returncode, yes_output)
    assert automatic_track.read_bytes() == b"keep me\n", "--yes bypassed plan authorization"
    assert b"Non-interactive destructive action refused" in yes_output, yes_output

print("destructive authorization PTY checks passed")
