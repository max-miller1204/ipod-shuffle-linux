#!/usr/bin/env python3
"""Exercise destructive authorization through a real terminal.

Every device-changing script is driven twice: once pausing at its confirmation
prompt so the mounted volume's identity can be replaced before the answer is
typed, and once with --yes, which is a confirmation a person could have typed
but not the authorization a destructive run needs.
"""

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

TRACK = "Album/Keep.mp3"
TRACK_BYTES = b"keep me\n"

# One entry per device-changing script, with the confirmation it stops at. The
# sync prompt names the one track the fixture puts on the device.
CASES = (
    ("ipod-wipe.sh", b"Wipe this iPod?"),
    ("ipod-remove.sh", b"Remove them?"),
    ("ipod-sync.sh", b"Delete 1 existing track(s) from the iPod?"),
)

REFUSED_SWAP = b"unplugged or replaced mid-operation"
REFUSED_YES = b"Destructive action refused: confirmation is not authorization"


def make_ipod(root: Path, name: str, identity: str) -> tuple[Path, Path]:
    mount = root / name
    track = mount / "iPod_Control" / "Music" / TRACK
    (mount / "iPod_Control" / "iTunes").mkdir(parents=True)
    (mount / "iPod_Control" / "Speakable").mkdir(parents=True)
    (mount / "iPod_Control" / "Device").mkdir(parents=True)
    track.parent.mkdir(parents=True)
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text(
        identity, encoding="utf-8"
    )
    track.write_bytes(TRACK_BYTES)
    return mount, track


def command(script: str, mount: Path, source: Path, *flags: str) -> list[str]:
    # Flags go before the positional arguments, which is where a caller of any
    # of the three would put them.
    argv = [str(ROOT / script), "--ipod", str(mount), *flags]
    if script == "ipod-remove.sh":
        argv.append(TRACK)
    elif script == "ipod-sync.sh":
        argv += ["--clear", str(source)]
    return argv


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

    source = workspace / "source"
    source.mkdir()
    (source / "Song.mp3").write_bytes(b"a track\n")

    for script, prompt in CASES:
        name = script.removeprefix("ipod-").removesuffix(".sh")

        # The iPod is swapped for another one while the person is still reading
        # the prompt, so the answer arrives for a device that is no longer
        # there. Nothing on the replacement may change.
        swapped, swapped_track = make_ipod(workspace, f"{name}-swap", "original-ipod")
        process, master, output = run_in_pty(
            command(script, swapped, source), env, prompt
        )
        (swapped / "iPod_Control" / "Device" / "SysInfo").write_text(
            "replacement-ipod", encoding="utf-8"
        )
        os.write(master, b"y\n")
        swap_output = finish(process, master, output)
        (EVIDENCE / f"{name}-device-swap-pty.txt").write_bytes(swap_output)
        assert process.returncode == 5, (script, process.returncode, swap_output)
        assert swapped_track.read_bytes() == TRACK_BYTES, (
            f"{script} changed the replacement device"
        )
        assert REFUSED_SWAP in swap_output, (script, swap_output)

        # --yes at a terminal is still not authorization: the run is refused
        # without the token from its own plan.
        automatic, automatic_track = make_ipod(
            workspace, f"{name}-yes", "yes-on-terminal"
        )
        process, master, output = run_in_pty(
            command(script, automatic, source, "--yes"), env
        )
        yes_output = finish(process, master, output)
        (EVIDENCE / f"{name}-yes-without-token-pty.txt").write_bytes(yes_output)
        assert process.returncode == 7, (script, process.returncode, yes_output)
        assert automatic_track.read_bytes() == TRACK_BYTES, (
            f"{script} let --yes bypass plan authorization"
        )
        assert REFUSED_YES in yes_output, (script, yes_output)

print("destructive authorization PTY checks passed")
