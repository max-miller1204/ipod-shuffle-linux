#!/usr/bin/env python3
"""Run a GTK check on a private, invisible Broadway display and session bus."""

import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

MARKER = "SHUFFLE_HEADLESS_TEST"


def wait_for_broadway(process, port):
    for _ in range(100):
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return True
        except OSError:
            time.sleep(0.02)
    return False


def inside(command):
    server = None
    selected_display = None
    for display in random.sample(range(40, 200), 160):
        candidate = subprocess.Popen(
            ["broadwayd", f":{display}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if wait_for_broadway(candidate, 8080 + display):
            selected_display = display
            server = candidate
            break
        candidate.terminate()
        candidate.wait(timeout=2)
    if server is None:
        sys.exit("could not start a private Broadway display")

    env = dict(os.environ)
    env.update(
        GDK_BACKEND="broadway",
        BROADWAY_DISPLAY=f":{selected_display}",
        GSK_RENDERER="cairo",
        SHUFFLE_HEADLESS_TEST="1",
    )
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    try:
        return subprocess.run(command, env=env).returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main():
    if len(sys.argv) < 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} COMMAND [ARG ...]")
    if os.environ.get(MARKER):
        return inside(sys.argv[1:])
    return subprocess.run(
        ["dbus-run-session", "--", sys.executable, __file__, *sys.argv[1:]],
        env={**os.environ, MARKER: "bootstrap"},
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
