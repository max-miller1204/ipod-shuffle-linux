#!/usr/bin/env python3
"""The shipped app, opened once and then driven only over D-Bus.

Nothing in here imports the package or touches a widget. The window is the one
`ipod-gui.sh` launches, against the demo library `tools/demo-library.py` builds
- real MP3s with real tags, and a stand-in shuffle `ipod-sync.sh` has actually
written two tracks to - and every step below is the `gdbus call` a person would
type, run as a subprocess and transcribed with the answer it gave.

Each step photographs the window afterwards, so what the actions did to the
screen is beside the JSON the state dump reported about it.

The display is gtk4-broadwayd, which serves the window over HTTP, and the
camera is a headless browser looking at it: this machine has no Xvfb, and the
alternative - a nested server on the developer's own desktop - would put a
window on the screen of whoever is sitting in front of it.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEMO = Path(os.environ.get("DEMO_ROOT", "/tmp/gio-actions-demo"))
URL = os.environ.get("BROADWAY_URL", "http://127.0.0.1:8087/")

DEST = "io.github.max_miller1204.IpodShuffle"
OBJECT = "/io/github/max_miller1204/IpodShuffle"

sys.path.insert(0, str(HERE))
from shutter import Browser  # noqa: E402

HOME = DEMO / "home"
MUSIC = HOME / "Music"
IPOD = DEMO / "MAX SHUFFLE"
STAGE_FOLDER = MUSIC / "Kova" / "Nightbus"
NOT_MUSIC = MUSIC / "Kova" / "Nightbus" / "cover.jpg"

# A folder of real tracks outside every music folder, staged before the sync so
# that the run lasts long enough to be photographed. The demo's own four albums
# are read, copied, catalogued and spoken in a quarter of a second on an NVMe
# disk - less time than one screenshot takes - and bytes are not what costs:
# 400 MB copies in a tenth of a second here, while the per-file work does not
# get any cheaper. So it is a lot of small files. Outside the music roots, so
# that the library the earlier steps report on is not changed by it.
LONG_RUN = DEMO / "Long Run"
LONG_RUN_TRACKS = 300

# Where the Sync button sits on the page the browser is holding, read off
# 08-device-page.png. The window is served into a browser of a fixed size and
# broadway puts it in the same place every run, so this is the same point every
# run - and the only thing here that is a pixel rather than an action, because
# starting a sync is deliberately not one of the actions.
SYNC_BUTTON = (985, 189)

ENVIRONMENT = {
    **os.environ,
    "HOME": str(HOME),
    "XDG_CONFIG_HOME": str(HOME / ".config"),
    "XDG_CACHE_HOME": str(HOME / ".cache"),
    "FAKE_IPOD_MOUNT": str(IPOD),
    # Replacing HOME hides install.sh's own directory from the sync as well as
    # from the tag reader, so both are named outright: the real database
    # builder and the real virtualenv, driven against a demo that is only
    # pretending to be an iPod.
    "IPOD_VENV_PYTHON": str(Path.home() / "ipod-tools/venv/bin/python"),
    "IPOD_DB_TOOL": str(Path.home() / "ipod-tools/IPod-Shuffle-4g/ipod-shuffle-4g.py"),
    "PATH": f"{REPO / 'tests' / 'bin'}:{os.environ['PATH']}",
    "GDK_BACKEND": "broadway",
    "BROADWAY_DISPLAY": os.environ.get("BROADWAY_DISPLAY", ":7"),
}
ENVIRONMENT.pop("DISPLAY", None)

transcript = []
shot_number = 0


def gdbus(*arguments, method="org.gtk.Actions.Activate"):
    """One `gdbus call`, exactly as the documentation writes it."""
    command = [
        "gdbus", "call", "--session",
        "--dest", DEST,
        "--object-path", OBJECT,
        "--method", method,
        *arguments,
    ]
    answer = subprocess.run(command, capture_output=True, text=True, env=ENVIRONMENT)
    printed = (answer.stdout + answer.stderr).strip()
    transcript.append(("command", " ".join(shlex.quote(part) for part in command)))
    transcript.append(("output", printed))
    if answer.returncode != 0:
        raise SystemExit(f"gdbus failed: {printed}")
    return printed


def activate(name, argument=None):
    payload = "[]" if argument is None else f'[<"{argument}">]'
    return gdbus(name, payload, "{}")


def dump():
    """Activate dump-state and read the state that activation left behind.

    Two calls, because that is the whole of the protocol: activating asks, and
    Describe is where the answer is. The state comes back inside a GVariant
    printed as text - `((true, signature '', [<'...'>]),)` - and the JSON is
    what is between the innermost quotes.
    """
    activate("dump-state")
    printed = gdbus("dump-state", method="org.gtk.Actions.Describe")
    start = printed.index("[<'") + 3
    end = printed.rindex("'>]")
    return json.loads(printed[start:end])


def settle(condition, seconds=20):
    """Read the dump again until it says what we are waiting for.

    The actions are fire-and-forget, which the documentation says outright: a
    dump read in the same breath as an activation can answer from the moment
    before the work landed. This is what a client written against that looks
    like.
    """
    deadline = time.monotonic() + seconds
    mark = len(transcript)
    state = dump()
    while time.monotonic() < deadline:
        if condition(state):
            return state
        time.sleep(0.3)
        # Only the reading that answered is left in the transcript. A poll that
        # came back the same as the one before it is the same two commands
        # again, and this loop runs for as long as a sync does.
        del transcript[mark:]
        state = dump()
    return state


def note(text):
    transcript.append(("note", text))
    print(text)


def shot(browser, slug, caption, settle_first=1.5):
    """Photograph the window, once it has finished painting what it was told.

    The pause is the camera's, not the product's: an action returns before the
    window has repainted, and a picture taken in that gap shows the state
    dump's answer beside a screen that has not caught up with it yet.
    """
    global shot_number
    time.sleep(settle_first)
    shot_number += 1
    path = HERE / f"{shot_number:02d}-{slug}.png"
    browser.photograph(path)
    transcript.append(("shot", f"{path.name}|{caption}"))
    print(f"  photographed {path.name}")
    return path


def catch_the_bar(browser, idle, slug, caption, frames=60):
    """Photograph as fast as the browser answers, keeping the frame with the
    sync bar in it.

    A sync onto a stand-in volume on a local disk is over in about a second,
    and every dump read costs two `gdbus` round trips, so waiting for the JSON
    to say "copying" and photographing afterwards catches the page after the
    bar has gone. This looks for the bar itself instead: it is a row that
    opens between the page and the now-playing bar, so a frame in which that
    strip is unlike the same window a moment before the click is a frame with
    the bar up.

    The strip is the bar's own and nothing else's. Wider, and it would also
    hold the toast that reports the run - "Sync complete" slides up over the
    now-playing bar the moment the sync bar goes, which is a difference from
    the same picture and the wrong one - and the controls a running script
    dims, which are a difference before the bar has finished opening.
    """
    region = (70, 672, 1080, 730)
    at_rest = Image.open(idle).convert("RGB").crop(region)
    area = at_rest.width * at_rest.height
    scratch = Path("/tmp/gio-actions-burst.png")
    for _ in range(frames):
        browser.photograph(scratch)
        now = Image.open(scratch).convert("RGB").crop(region)
        difference = ImageChops.difference(now, at_rest).convert("L")
        changed = sum(count for value, count in
                      zip(range(256), difference.histogram()) if value > 24)
        if changed > area * 0.02:
            global shot_number
            shot_number += 1
            path = HERE / f"{shot_number:02d}-{slug}.png"
            # The frame that answered is the bar opening, which is a strip of
            # itself. The picture is taken a moment after, with the bar the
            # size it stays.
            time.sleep(0.45)
            browser.photograph(path)
            transcript.append(("shot", f"{path.name}|{caption}"))
            print(f"  photographed {path.name}")
            return path
    return None


def wait_for_paint(browser, seconds=60):
    """Hold until the browser has the window on screen.

    A picture taken before broadway has sent the first frame is a white page,
    which reads as an app that opened blank rather than as a camera that was
    early. A painted window is a screenshot of some size; an empty page is a
    couple of kilobytes of white.
    """
    scratch = Path("/tmp/gio-actions-first-frame.png")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        browser.photograph(scratch)
        if scratch.stat().st_size > 20_000:
            return True
        time.sleep(0.5)
    return False


def write_json(slug, document):
    path = HERE / f"{slug}.json"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {path.name}")
    return path


def build_long_run():
    """The folder above: one real tagged MP3, and that many copies of it.

    Encoded once rather than three hundred times, because what this folder is
    for is the number of files in it - every one of them is read for its tags,
    copied, catalogued and named on the device, and that is the work the bar
    is reporting while the picture is taken.
    """
    LONG_RUN.mkdir(parents=True, exist_ok=True)
    master = LONG_RUN / "01 - Long Run 1.mp3"
    if not master.is_file():
        subprocess.run(
            [
                "/usr/bin/ffmpeg", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", "-c:a", "libmp3lame", "-b:a", "128k",
                "-id3v2_version", "3",
                "-metadata", "artist=Fixture Choir",
                "-metadata", "album=Long Run",
                "-metadata", "title=Long Run",
                str(master),
            ],
            check=True,
            capture_output=True,
        )
    for number in range(2, LONG_RUN_TRACKS + 1):
        copy = LONG_RUN / f"{number:03d} - Long Run {number}.mp3"
        if not copy.is_file():
            copy.write_bytes(master.read_bytes())


def cli(*arguments):
    """The headless CLI, run against the same library the window has open."""
    answer = subprocess.run(
        ["/usr/bin/python3", "-m", "ipod_gui.cli", *arguments],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=ENVIRONMENT,
    )
    if answer.returncode != 0:
        raise SystemExit(f"the CLI failed: {answer.stderr.strip()}")
    return json.loads(answer.stdout)


def main():
    if not IPOD.is_dir():
        raise SystemExit(
            f"no demo in {DEMO}: build one with tools/demo-library.py first"
        )
    NOT_MUSIC.write_bytes(b"a picture, not a song")
    build_long_run()

    app = subprocess.Popen(
        [str(REPO / "ipod-gui.sh")],
        env=ENVIRONMENT,
        stdout=open("/tmp/gio-actions-app.log", "wb"),
        stderr=subprocess.STDOUT,
    )
    browser = None
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            listed = subprocess.run(
                [
                    "gdbus", "call", "--session", "--dest", DEST,
                    "--object-path", OBJECT, "--method", "org.gtk.Actions.List",
                ],
                capture_output=True,
                text=True,
                env=ENVIRONMENT,
            )
            if listed.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise SystemExit("the app never took its name on the bus")

        browser = Browser(URL, int(os.environ.get("SHUTTER_PORT", "9407")),
                          "/tmp/gio-actions-profile", size=(1360, 1010))
        if not wait_for_paint(browser):
            raise SystemExit("the browser never showed the window")

        # ---------------------------------------------------------- the surface
        note("The actions the running application exports:")
        gdbus(method="org.gtk.Actions.List")

        # ------------------------------------------------------------ the dump
        note("What the window is showing, before anything is asked of it:")
        state = settle(lambda s: s["visibleCounts"]["library"], seconds=30)
        write_json("dump-01-opened", state)
        shot(browser, "opened", "The window as it opens, on the library page")

        # --------------------------------------------------------- navigate
        note("navigate: follow a sidebar row from outside the window.")
        activate("navigate", "playlists")
        state = settle(lambda s: s["page"] == "playlists")
        note(f'  the dump now reports page {state["page"]!r}')
        shot(browser, "navigate-playlists", "navigate playlists")

        note("navigate: a page name the window has no page under is refused.")
        activate("navigate", "bogus")
        time.sleep(1)
        state = dump()
        note(f'  the dump still reports page {state["page"]!r}')
        shot(browser, "navigate-refused", "navigate bogus, refused")

        # ----------------------------------------------------------- search
        note("search: open the search page with a query in the field.")
        activate("search", "ridge")
        state = settle(lambda s: s["page"] == "search")
        note(f'  the dump reports page {state["page"]!r}')
        # The results arrive on the field's own debounce and, for the YouTube
        # half, on a subprocess after it, so the picture waits for both.
        shot(browser, "search-ridge", "search ridge", settle_first=5)

        note("search: a query too short to ask YouTube, which the page says.")
        activate("search", "a")
        state = settle(lambda s: s["inlineError"])
        note(f'  the dump reports inlineError {state["inlineError"]!r}')
        write_json("dump-02-inline-error", state)
        shot(browser, "search-inline-error", "the note the search page shows")

        note("navigate back to the library, which ends the search.")
        activate("navigate", "library")
        state = settle(lambda s: s["page"] == "library")
        note(f'  inlineError is now {state["inlineError"]!r}')

        # ------------------------------------------------------------ queue
        note(f"queue: stage {STAGE_FOLDER} for the next sync.")
        activate("queue", str(STAGE_FOLDER))
        state = settle(lambda s: s["staged"]["sources"])
        write_json("dump-03-staged", state)
        note(f'  staged {state["staged"]["sources"]}')
        note(f'  {len(state["staged"]["tracks"])} track(s), '
             f'{state["staged"]["changes"]} change(s), '
             f'{state["staged"]["bytes"]} bytes, '
             f'against {state["staged"]["deviceIdentity"]!r}')
        note(f'  visibleCounts are now {state["visibleCounts"]}')
        shot(browser, "queued", "one album staged by the queue action",
             settle_first=3)

        note(f"queue: {NOT_MUSIC.name} is not something a sync can read back.")
        before = state["staged"]["sources"]
        activate("queue", str(NOT_MUSIC))
        time.sleep(2)
        state = dump()
        note(f'  staged is still {state["staged"]["sources"]}')
        if state["staged"]["sources"] != before:
            raise SystemExit("a file that is not music was staged")

        # ---------------------------------------------------------- refresh
        note("refresh: re-detect the device and rescan the music folders.")
        activate("refresh")
        shot(browser, "refresh", "refresh, mid-scan")
        state = settle(lambda s: s["visibleCounts"]["library"])
        note(f'  after the rescan the counts read {state["visibleCounts"]}')
        write_json("dump-04-refreshed", state)

        # -------------------------------------------------------- the sync bar
        note("navigate: the device page, where the Sync button is.")
        activate("navigate", "settings")
        settle(lambda s: s["page"] == "settings")
        shot(browser, "device-page", "the device page, with an album staged")

        # One album is 20 KB onto a stand-in volume on a local disk, which is
        # copied, catalogued and spoken faster than a photograph can be taken
        # of it. So the queue action is asked for more before the sync is
        # started, from a folder outside every music folder: the run is then
        # long enough that the bar is still up when the shutter opens, and
        # nothing else about it is any different.
        note(f"queue: {LONG_RUN_TRACKS} more tracks, from a folder outside "
             f"every music folder, so that the sync below lasts long enough "
             f"to be photographed.")
        activate("queue", str(LONG_RUN))
        state = settle(lambda s: len(s["staged"]["tracks"]) > 1, seconds=60)
        note(f'  {len(state["staged"]["tracks"])} tracks staged, '
             f'{state["staged"]["changes"]} changes, '
             f'{state["staged"]["bytes"]} bytes')

        note("Sync, clicked on the window itself. Starting a sync is the one "
             "thing these actions do not do, and the bar it puts up is what "
             "the dump's sync half reports on.")
        reference = Path("/tmp/gio-actions-at-rest.png")
        browser.photograph(reference)
        browser.click(*SYNC_BUTTON)
        if catch_the_bar(browser, reference, "syncing",
                         "the sync bar, mid-run") is None:
            raise SystemExit("the sync finished before the bar was photographed")
        state = dump()
        note(f'  with the bar on screen, sync read {state["sync"]}')
        write_json("dump-05-syncing", state)
        state = settle(lambda s: not s["sync"]["active"], seconds=300)
        note(f'  when the run finished sync read {state["sync"]}')
        note(f'  the counts at that moment read {state["visibleCounts"]}')
        write_json("dump-06-synced", state)
        # The window re-reads the iPod it has just written to, and that read
        # is another thing the dump can answer from in front of. Which is the
        # documentation's fire-and-forget note from the other end: read once
        # and this can still be the iPod as it was; read again until it has
        # landed and it is the iPod as it now is.
        state = settle(lambda s: s["visibleCounts"]["ipod"], seconds=300)
        note(f'  and once the window had finished re-reading the iPod, '
             f'{state["visibleCounts"]}')
        write_json("dump-07-rescanned", state)
        shot(browser, "synced", "everything staged now on the iPod, the queue "
             "empty", settle_first=3)

        # ------------------------------------- the same track, both surfaces
        note("The staged track, as the state dump writes it and as the "
             "headless CLI writes it.")
        staged = json.loads((HERE / "dump-03-staged.json").read_text())
        staged_track = staged["staged"]["tracks"][0]
        listed = cli("library")["result"]["tracks"]
        matching = [t for t in listed if t["path"] == staged_track["path"]]
        if not matching:
            raise SystemExit("the CLI does not list the staged track")
        comparison = {
            "path": staged_track["path"],
            "dumpState": staged_track,
            "headlessCli": matching[0],
            "sameFields": sorted(staged_track) == sorted(matching[0]),
            "sameExceptState": {
                key: value for key, value in staged_track.items()
                if key not in ("state", "onIpod")
            } == {
                key: value for key, value in matching[0].items()
                if key not in ("state", "onIpod")
            },
        }
        write_json("track-shape-comparison", comparison)
        note(f'  same fields: {comparison["sameFields"]}; '
             f'same values apart from where the track lives: '
             f'{comparison["sameExceptState"]}')

        state = dump()
        write_json("dump-08-final", state)
    finally:
        if browser is not None:
            browser.close()
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()

    lines = ["# Driving the running window with gdbus", ""]
    for kind, body in transcript:
        if kind == "note":
            lines += ["", body, ""]
        elif kind == "command":
            lines += ["```console", f"$ {body}"]
        elif kind == "output":
            # One dump taken while three hundred tracks are staged is a
            # hundred kilobytes on one line, and a transcript nothing will
            # render is a transcript nobody reads. Cut where it stops being
            # readable, and say so; the documents themselves are the JSON
            # files beside this one.
            if len(body) > 1500:
                # Both ends rather than the first 1500 characters: the fields
                # a reader is here for are at the end of the document, and a
                # cut that keeps only the head of a staged track list hides
                # every one of them.
                body = (
                    body[:1100]
                    + f"\n[… {len(body) - 1500} more characters. The whole "
                    "document is in the dump-*.json files beside this "
                    "transcript. It ends:]\n"
                    + body[-400:]
                )
            lines += [body or "(no output)", "```"]
        elif kind == "shot":
            name, caption = body.split("|", 1)
            lines += [f"![{caption}]({name})", ""]
    (HERE / "gdbus-session.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {HERE / 'gdbus-session.md'}")


if __name__ == "__main__":
    main()
