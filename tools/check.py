#!/usr/bin/env python3
"""Run repository-owned validation profiles with deterministic output."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import functools
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON_SOURCES = (
    "ipod-gui.py ipod-report.py ipod_gui/*.py tests/*.py tools/*.py"
)

# The shuffle has no screen, so a playlist's spoken name is its whole identity.
# Any of the three engines the product itself accepts can record one.
SPEECH_ENGINES = ("pico2wave", "espeak", "say")

# Where ./install.sh clones the upstream database builder that product E2E
# proves rewritten playlist entries resolve against.
INSTALLED_DB_TOOL = (
    Path.home() / "ipod-tools" / "IPod-Shuffle-4g" / "ipod-shuffle-4g.py"
)


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


STATIC_CHECKS = (
    Check(
        "shellcheck",
        ("bash", "-c", "shellcheck -x ./*.sh tests/product-e2e.sh"),
        ("shellcheck",),
    ),
    Check(
        "mixin contract",
        (
            "bash",
            "-c",
            "exec \"$IPOD_CHECK_GUI_PYTHON\" tools/mixin-contract.py",
        ),
        ("gtk",),
    ),
    # py_compile never imports, so this holds on any interpreter and must keep
    # running on a checkout that has no GTK bindings yet. The mixin contract
    # above is the one that reads the package and therefore needs them.
    Check(
        "Python compile",
        ("bash", "-c", f"exec \"$IPOD_CHECK_PYTHON\" -m py_compile {PYTHON_SOURCES}"),
        ("python3",),
    ),
    Check("validation runner", ("python3", "tests/check-runner.py"), ("python3",)),
)

PUSH_CHECKS = STATIC_CHECKS + (
    Check("MCP server", ("python3", "tests/mcp-server.py")),
    Check("demo library guard", ("python3", "tests/demo-library-guard.py")),
    Check(
        "Python imports",
        (
            "bash",
            "-c",
            "\"$IPOD_CHECK_GUI_PYTHON\" -c 'import ipod_gui' && \"$IPOD_CHECK_GUI_PYTHON\" -m ipod_gui.cli --help && \"$IPOD_CHECK_GUI_PYTHON\" tests/headless-cli.py",
        ),
        ("gtk",),
    ),
    Check("GUI repaints coalesce", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/gui-repaint-coalescing.py"), ("gtk",)),
    Check("GUI refresh spinner", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/gui-refresh-spinner.py"), ("gtk",)),
    Check("GUI progress stream", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/gui-progress-stream.py"), ("gtk",)),
)

FULL_CHECKS = PUSH_CHECKS + (
    Check("headless isolation", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/headless-isolation.py"), ("gtk", "xvfb-run", "dbus-run-session")),
    Check("GUI window builds", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/gui-window-build.py"), ("gtk", "xvfb-run", "dbus-run-session")),
    Check("GUI window minimum", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/gui-window-minimum.py"), ("gtk", "xvfb-run", "dbus-run-session")),
    Check("Gio actions", ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/gui-gio-actions.py"), ("gtk", "xvfb-run", "dbus-run-session")),
    Check(
        "screenshots",
        ("bash", "-c", "exec \"$IPOD_CHECK_GUI_PYTHON\" tests/screenshot-harness.py"),
        ("gtk", "xvfb-run", "dbus-run-session", "ffmpeg"),
        (("SCREENSHOT_EVIDENCE_DIR", "screenshots"),),
    ),
    # speech and real-db-tool are declared although the suite would still exit
    # 0 without them: each one absent turns a sub-check into a printed NOTICE,
    # so a runner image that stopped shipping a speech engine would quietly
    # weaken this profile rather than fail it.
    Check(
        "product E2E",
        (
            "bash",
            "-c",
            "test \"$(id -u)\" -ne 0 || { echo \"product E2E must not run as root\" >&2; exit 1; }; exec bash tests/product-e2e.sh",
        ),
        ("bash", "uv", "distro-gtk", "non-root", "speech", "real-db-tool"),
        (("EVIDENCE_DIR", "product-e2e"),),
    ),
)

PROFILES = {
    "staged": STATIC_CHECKS,
    "push": PUSH_CHECKS,
    "full": FULL_CHECKS,
    "fix": (),
}


@functools.cache
def gui_python() -> str | None:
    """The interpreter the application runs on, or None before it is installed.

    Memoized because the probe execs up to five interpreters, and the profiles
    below ask for it once per GTK-backed check.
    """
    result = subprocess.run(
        ["bash", "-c", "source ./lib.sh && find_gui_python"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@functools.cache
def compile_python() -> str | None:
    """Any interpreter, preferring the application's own where one exists."""
    return gui_python() or shutil.which("python3")


@functools.cache
def distro_python_has_gtk() -> bool:
    return subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')",
        ],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def real_db_tool() -> str | None:
    configured = os.environ.get("IPOD_REAL_DB_TOOL")
    candidate = Path(configured) if configured else INSTALLED_DB_TOOL
    return str(candidate) if candidate.is_file() else None


def unmet(capability: str) -> str | None:
    """What a capability needs, when the machine does not have it."""
    if capability == "gtk":
        if not gui_python():
            return "Python with GTK4 and Libadwaita bindings"
    elif capability == "distro-gtk":
        if not distro_python_has_gtk():
            return "/usr/bin/python3 with GTK4 and Libadwaita bindings"
    elif capability == "non-root":
        if os.geteuid() == 0:
            return "an unprivileged user"
    elif capability == "speech":
        if not any(shutil.which(engine) for engine in SPEECH_ENGINES):
            return f"a speech engine ({', '.join(SPEECH_ENGINES)})"
    elif capability == "real-db-tool":
        if not real_db_tool():
            return "the upstream database builder (IPOD_REAL_DB_TOOL or ./install.sh)"
    elif shutil.which(capability) is None:
        return capability
    return None


def missing_capabilities(checks: tuple[Check, ...]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for check in checks:
        absent = [
            description
            for description in (unmet(capability) for capability in check.capabilities)
            if description is not None
        ]
        if absent:
            missing[check.name] = absent
    return missing


def evidence_log(check: Check) -> str:
    return re.sub(r"[^a-z0-9]+", "-", check.name.lower()).strip("-") + ".log"


_running: set[subprocess.Popen] = set()
_running_lock = threading.Lock()
_report_lock = threading.Lock()


def report(message: str) -> None:
    """Live progress, so a long profile is not a silent CI step."""
    with _report_lock:
        print(message, file=sys.stderr, flush=True)


def status_of(code: int) -> str:
    return "PASS" if code == 0 else f"FAIL ({code})"


# How long an interrupted check is given to take its own children down before
# the group is killed outright.
GRACE_SECONDS = 3


def signal_group(process: subprocess.Popen, number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, number)


def stop_running() -> None:
    """Tear down every child on interrupt, process group and all.

    Each check runs in a session of its own, so an Xvfb server, a session bus
    and a product suite midway through a device mutation would otherwise
    survive the runner that started them.

    Swept twice: a grandchild that forks while the first sweep is passing over
    its group - one of the display checks starting its Xvfb server - misses the
    signal its parent got. Nothing is waited for in between, because the
    check being killed is what pins its own pid as the group's id, and reaping
    it first would leave the second sweep addressing a number the kernel is
    free to have given to something else.
    """
    with _running_lock:
        processes = list(_running)
    if not processes:
        return
    for process in processes:
        signal_group(process, signal.SIGTERM)
    time.sleep(GRACE_SECONDS)
    for process in processes:
        signal_group(process, signal.SIGKILL)
    for process in processes:
        process.wait()


def run_check(check: Check, evidence_root: Path) -> tuple[int, str, float]:
    env = os.environ.copy()
    # Resolved from what the check declared, so a profile needing neither
    # interpreter never pays for the probe that finds them.
    if "gtk" in check.capabilities:
        env["IPOD_CHECK_GUI_PYTHON"] = str(gui_python())
    if "python3" in check.capabilities:
        env["IPOD_CHECK_PYTHON"] = str(compile_python())
    for variable, directory in check.environment:
        env.setdefault(variable, str(evidence_root / directory))

    started = time.monotonic()
    # The captured output is the evidence: written where a failing run says to
    # look for it rather than only held in memory until the summary prints.
    with (evidence_root / evidence_log(check)).open("w+", encoding="utf-8") as output:
        process = subprocess.Popen(
            check.command,
            cwd=REPO,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with _running_lock:
            _running.add(process)
        try:
            code = process.wait()
        finally:
            with _running_lock:
                _running.discard(process)
        output.seek(0)
        captured = output.read()

    elapsed = time.monotonic() - started
    report(f"  {status_of(code)} {check.name} ({elapsed:.1f}s)")
    return code, captured, elapsed


def run_profile(profile: str, jobs: int | None = None) -> int:
    checks = PROFILES[profile]
    if profile == "fix":
        print("fix: no repository-approved rewrites are configured")
        return 0

    # Unrunnable checks are named and counted, but they never hold back the
    # ones this machine can run: a checkout without ffmpeg still gets its
    # shell, syntax and display-free checks.
    missing = missing_capabilities(checks)
    runnable = tuple(check for check in checks if check.name not in missing)
    if missing:
        print(f"{profile}: skipping checks whose capabilities are unavailable:", file=sys.stderr)
        for name, capabilities in missing.items():
            print(f"  {name}: {', '.join(capabilities)}", file=sys.stderr)

    configured_root = os.environ.get("CHECK_EVIDENCE_DIR")
    temporary = None
    if configured_root:
        evidence_root = Path(configured_root)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="ipod-check-evidence-")
        evidence_root = Path(temporary.name)
    evidence_root.mkdir(parents=True, exist_ok=True)

    results: dict[str, tuple[int, str, float]] = {}
    if runnable:
        workers = jobs or min(len(runnable), os.cpu_count() or 1)
        report(f"{profile}: {len(runnable)} checks, {workers} at a time")
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {check.name: executor.submit(run_check, check, evidence_root) for check in runnable}
            results = {name: future.result() for name, future in futures.items()}
        except KeyboardInterrupt:
            # Cancel what has not started, kill what has, and only then let go
            # of the directory the running checks are still writing into.
            executor.shutdown(wait=False, cancel_futures=True)
            stop_running()
            executor.shutdown(wait=True)
            print(f"{profile}: interrupted; stopped every running check", file=sys.stderr)
            if temporary is not None:
                temporary.cleanup()
            raise
        finally:
            executor.shutdown(wait=True)

    failed = False
    for check in checks:
        if check.name in missing:
            print(f"[SKIP] {check.name} - needs {', '.join(missing[check.name])}")
            continue
        code, output, elapsed = results[check.name]
        print(f"[{status_of(code)}] {check.name} ({elapsed:.1f}s)")
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        failed |= code != 0

    if failed:
        if temporary is not None:
            destination = REPO / ".check-evidence"
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(evidence_root, destination)
            print(f"Failure evidence: {destination}", file=sys.stderr)
        else:
            print(f"Failure evidence: {evidence_root}", file=sys.stderr)
    if temporary is not None:
        temporary.cleanup()
    if failed:
        return 1
    return 2 if missing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILES, nargs="?", default="staged")
    parser.add_argument("--jobs", type=int, default=None)
    args = parser.parse_args(argv)
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        return run_profile(args.profile, args.jobs)
    except KeyboardInterrupt:
        # run_profile has already stopped the children and said so; the shell's
        # own convention for the signal is more use here than a traceback.
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
