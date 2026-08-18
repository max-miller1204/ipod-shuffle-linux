#!/usr/bin/env python3
"""Behavior checks for the repository validation runner."""

import contextlib
import importlib.util
import io
import tempfile
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("repository_check", REPO / "tools" / "check.py")
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check
_spec.loader.exec_module(check)


def command(name, text, code=0):
    script = f"print({text!r}); raise SystemExit({code})"
    return check.Check(name, ("python3", "-c", script))


def rendezvous(name, mine, peer, marker, linger=0.0, deadline=5.0):
    """A check that can only succeed while the other one is also running.

    Each writes its own file and then waits for its peer's, so overlap is the
    condition for finishing rather than something a stopwatch infers after the
    fact: run one at a time and the first of them waits out its deadline and
    fails, whatever the machine's load happens to be.
    """
    script = "\n".join(
        (
            "import pathlib, time",
            f"pathlib.Path({str(mine)!r}).write_text('here')",
            f"peer = pathlib.Path({str(peer)!r})",
            f"deadline = time.monotonic() + {deadline}",
            "while not peer.exists():",
            "    if time.monotonic() > deadline:",
            "        raise SystemExit('the other check never started')",
            "    time.sleep(0.01)",
            f"time.sleep({linger})",
            f"print({marker!r})",
        )
    )
    return check.Check(name, ("python3", "-c", script))


def run(checks, jobs=None, evidence=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.dict(check.PROFILES, {"test": checks}), contextlib.redirect_stdout(
        stdout
    ), contextlib.redirect_stderr(stderr):
        if evidence is None:
            code = check.run_profile("test", jobs=jobs)
        else:
            with mock.patch.dict(check.os.environ, {"CHECK_EVIDENCE_DIR": str(evidence)}):
                code = check.run_profile("test", jobs=jobs)
    return code, stdout.getvalue(), stderr.getvalue()


# Independent checks really do overlap, and each one's captured output is
# printed under its own heading in declaration order rather than in the order
# the processes happened to finish. "slow first" lingers so that it is the last
# to exit while still being the first reported.
with tempfile.TemporaryDirectory() as evidence:
    signals = Path(evidence) / "signals"
    signals.mkdir()
    first, second = signals / "first", signals / "second"
    checks = (
        rendezvous("slow first", first, second, "alpha-body", linger=0.2),
        rendezvous("fast second", second, first, "beta-body"),
    )
    code, text, _ = run(checks, jobs=2, evidence=evidence)
    assert code == 0, text
    assert (
        text.index("[PASS] slow first")
        < text.index("alpha-body")
        < text.index("[PASS] fast second")
        < text.index("beta-body")
    ), text
    # The captured output is also on disk, under the name the failure message
    # points at, rather than only in the summary above.
    assert (Path(evidence) / "slow-first.log").read_text().strip() == "alpha-body"
    assert (Path(evidence) / "fast-second.log").read_text().strip() == "beta-body"

# What the pairing above rests on, shown failing: one worker cannot satisfy a
# rendezvous, so a runner that stopped overlapping checks fails this file
# rather than passing it a little more slowly.
with tempfile.TemporaryDirectory() as evidence:
    signals = Path(evidence) / "signals"
    signals.mkdir()
    first, second = signals / "first", signals / "second"
    checks = (
        rendezvous("one", first, second, "alpha-body", deadline=0.5),
        rendezvous("two", second, first, "beta-body", deadline=0.5),
    )
    code, text, _ = run(checks, jobs=1, evidence=evidence)
assert code == 1, text
assert "the other check never started" in text, text

# Every check completes even when another fails, and the profile reports one
# failure while preserving each check's own exit status and output.
with tempfile.TemporaryDirectory() as evidence:
    checks = (command("failure", "bad", 7), command("survivor", "good"))
    code, text, _ = run(checks, jobs=2, evidence=evidence)
assert code == 1, code
assert "[FAIL (7)] failure" in text and "bad" in text, text
assert "[PASS] survivor" in text and "good" in text, text

# A check this machine cannot run is named and counted, but it never holds back
# the checks that can run. The profile still fails, with a status of its own.
with tempfile.TemporaryDirectory() as evidence:
    checks = (
        check.Check("needs a display", ("false",), ("missing-test-binary",)),
        command("runs anyway", "ran"),
    )
    code, text, errors = run(checks, jobs=2, evidence=evidence)
assert code == 2, code
assert "needs a display: missing-test-binary" in errors, errors
assert "[SKIP] needs a display - needs missing-test-binary" in text, text
assert "[PASS] runs anyway" in text and "ran" in text, text

# A real failure outranks an unrunnable check, so a caller telling the two
# apart by exit status is told about the failure.
with tempfile.TemporaryDirectory() as evidence:
    checks = (
        check.Check("unsatisfiable", ("false",), ("missing-test-binary",)),
        command("failure", "bad", 7),
    )
    code, text, _ = run(checks, jobs=2, evidence=evidence)
assert code == 1, text

# Product E2E's distro interpreter, non-root, speech engine and database
# builder requirements are all part of the same preflight, rather than
# conditions the suite discovers late and reports as a skipped sub-check.
checks = (
    check.Check("product", ("false",), ("distro-gtk", "non-root", "speech", "real-db-tool")),
)
with mock.patch.object(check, "distro_python_has_gtk", return_value=False), mock.patch.object(
    check.os, "geteuid", return_value=0
), mock.patch.object(check.shutil, "which", return_value=None), mock.patch.object(
    check, "INSTALLED_DB_TOOL", REPO / "does-not-exist.py"
), mock.patch.dict(check.os.environ, {}, clear=True):
    missing = check.missing_capabilities(checks)
assert missing == {
    "product": [
        "/usr/bin/python3 with GTK4 and Libadwaita bindings",
        "an unprivileged user",
        "a speech engine (pico2wave, espeak, say)",
        "the upstream database builder (IPOD_REAL_DB_TOOL or ./install.sh)",
    ]
}, missing

# The same two, satisfied: a speech engine on PATH and a builder the caller
# named, which is how CI supplies the one it fetched itself.
with tempfile.NamedTemporaryFile(suffix=".py") as builder:
    with mock.patch.object(check, "distro_python_has_gtk", return_value=True), mock.patch.object(
        check.os, "geteuid", return_value=1000
    ), mock.patch.object(check.shutil, "which", return_value="/usr/bin/espeak"), mock.patch.dict(
        check.os.environ, {"IPOD_REAL_DB_TOOL": builder.name}
    ):
        assert check.missing_capabilities(checks) == {}, check.missing_capabilities(checks)

# Fix is intentionally constrained to a declared no-op until the repository
# approves a mechanical rewrite. It must never silently run a read-only profile.
stdout = io.StringIO()
with contextlib.redirect_stdout(stdout):
    code = check.run_profile("fix")
assert code == 0, code
assert "no repository-approved rewrites" in stdout.getvalue(), stdout.getvalue()

print("validation runner ok")
