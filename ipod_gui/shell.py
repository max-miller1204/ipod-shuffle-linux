"""Asking lib.sh what is installed, so the window and the scripts agree.

The capability answers are the scripts' own, reached by sourcing lib.sh and
calling the function, rather than a second opinion the GUI formed by itself
and could drift on.
"""

import shutil
import subprocess

from .config import FETCH_SCRIPT, LIB_SCRIPT


def has_speech_engine():
    """Whether any text-to-speech engine the builder recognises is installed."""
    return any(shutil.which(engine) for engine in ("pico2wave", "espeak", "say"))


def lib_function_succeeds(name):
    """Run a helper from lib.sh and report whether it succeeded.

    Asking the shared library rather than repeating its checks here keeps the
    GUI's idea of an installed dependency identical to the scripts'. The
    supported runtime versions in particular are a moving target, and a second
    copy of those rules would eventually disagree with the one that matters.
    """
    if not LIB_SCRIPT.is_file():
        return False
    try:
        return (
            subprocess.run(
                ["bash", "-c", f'source "$1" && {name}', "_", str(LIB_SCRIPT)],
                capture_output=True,
                timeout=20,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def lib_function_output(name):
    """Run a helper from lib.sh and return what it printed, or None.

    lib_function_succeeds answers whether a dependency is installed; this
    answers where it is. Searching runs yt-dlp directly rather than through one
    of the scripts, and the virtualenv copy install.sh keeps current is not on
    PATH, so the path has to come from the same helper the scripts use.
    """
    if not LIB_SCRIPT.is_file():
        return None
    try:
        result = subprocess.run(
            ["bash", "-c", f'source "$1" && {name}', "_", str(LIB_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def youtube_unavailable_reason():
    """Why downloading from YouTube is not possible here, or None if it is.

    Checked before offering the button because every one of these failures
    surfaces late and misleadingly: without a JavaScript runtime the download
    dies with HTTP 403 on everything but the oldest uploads, and without
    ffmpeg yt-dlp hands back the Opus file the shuffle silently ignores.
    """
    if not FETCH_SCRIPT.is_file():
        return "Not available in this build"
    if not lib_function_succeeds("yt_dlp_bin"):
        return "yt-dlp is not installed - run ./install.sh"
    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not installed - run ./install.sh"
    if not lib_function_succeeds("js_runtime"):
        return "Needs Deno, Node, or Bun - see the README"
    return None


def youtube_search_unavailable_reason():
    """Why searching YouTube is not possible here, or None if it is.

    Deliberately weaker than youtube_unavailable_reason, which is about the
    download. Searching only reads metadata, and yt-dlp does that without a
    JavaScript runtime and without ffmpeg: it is the media URL that is signed
    and the audio that has to be converted. Gating the field on the download's
    requirements would blank out the half of the window that still works and
    leave the user no way to find out what they could have downloaded.
    """
    if not lib_function_succeeds("yt_dlp_bin"):
        return "yt-dlp is not installed - run ./install.sh"
    return None
