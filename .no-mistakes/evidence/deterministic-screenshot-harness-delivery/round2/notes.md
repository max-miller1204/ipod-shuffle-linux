# Round 2 - the two fixes, exercised end to end

This round validates commit `0a26ad3`, which answers the two warnings the
previous round raised: a CI runner with no tag reader, and a shot whose colour
scheme followed the host.

Unlike the previous round, everything here ran **beside a live GNOME Wayland
session** (`DISPLAY=:1`, `WAYLAND_DISPLAY=wayland-0`, the user's session bus),
which is the harder case for the isolation promise and the case the colour
scheme defect needed.
`isolation-beside-a-live-desktop.txt` shows the same probe run twice: bare, it
lands on the desktop compositor (`wayland-0`, 1536x960); routed through
`tools/headless-run.py`, it lands on `:99` over X11 at 2400x1800 with a session
bus in `/tmp`.
No harness window appeared on the desktop during any run.

## The shots, and the one fact that ties both fixes together

`canonical/` is `python3 tests/screenshot-harness.py` run as a developer on
this machine: HOME with `install.sh`'s virtualenv in it, a desktop that prefers
dark.

`ci-like/` is the same command run the way the runner will: HOME with no
virtualenv, no desktop to ask for a colour scheme, and the tag reader supplied
only through `IPOD_VENV_PYTHON`, exactly as the workflow's new step does.

| file | developer `canonical/` | runner `ci-like/` |
| --- | --- | --- |
| `library-1180-1x.png` (1180x760) | `5a468269...` | `5a468269...` |
| `library-1180-2x.png` (2360x1520) | `f5d97d90...` | `f5d97d90...` |
| `playlists-760-2x.png` (1520x1520) | `9e008a57...` | `9e008a57...` |

Byte-identical, and identical to the shots the previous round recorded.
The library shot reads `All 4 / On iPod 1 / In library 3` with the four albums,
their covers and their artists - the canonical fixture, not the `Unknown album`
collapse.

## Fix 1 - a CI runner with no tag reader now fails instead of publishing

`ci-step-replay.txt` replays the workflow's two new pieces with
`${{ runner.temp }}` expanded and a runner-like HOME: the tag-reader venv, then
the `Deterministic screenshots` step verbatim, then a listing of what
`actions/upload-artifact` would collect from `${RUNNER_TEMP}/screenshots`.
It ends with the three canonical PNGs at the right sizes and hashes.
(`python3 -m venv` cannot install pip on this machine - no `ensurepip`, which
is exactly the package the workflow now adds - so the venv was built
`--without-pip` and mutagen dropped into it; its product, an interpreter at
`${RUNNER_TEMP}/tag-venv/bin/python` that imports mutagen, is what the next step
consumes.)

`no-tag-reader-refusal.txt` is the other half: the same harness on a machine
where no interpreter has mutagen stops before rendering anything, with

```
AssertionError: the fixture under .../demo reads as ['None'], not the
['Field Notes', 'Nightbus', 'Slow Copper', 'Warm Ridge'] the canonical shot is
of: no interpreter here has mutagen, so point IPOD_VENV_PYTHON at one that does
```

So the state the previous round found - a runner quietly uploading a
one-tile library - is now a red step rather than an artifact.

## Fix 2 - the colour scheme is the tool's, not the host's

`scheme/` is one command, one fixture, two environments:

| file | HOME | mean channel | sha256 |
| --- | --- | --- | --- |
| `pinned-dark-host-home.png` | `/home/max` (GNOME `prefer-dark`) | 32.5 | `5a468269...` |
| `pinned-dark-empty-home.png` | empty (no preference; the runner's case) | 32.5 | `5a468269...` |
| `unpinned-host-home.png` | `/home/max` | 32.5 | `5a468269...` |
| `unpinned-empty-home.png` | empty | **226.3** | `5bfac896...` |

The `unpinned-*` pair was rendered with the one new line in `tools/shoot.py`
(`Adw.StyleManager ... FORCE_DARK`) commented out, and reproduces the defect:
the whole window in the light scheme, a different picture from the one the
developer gets. With the line in place both environments land on the same
bytes, and on the same dark scheme `docs/screenshot.png` is in (measured 33.5
over the same 1180x760 box).

Run against that same mutation, the harness's new brightness assertion is what
catches it:

```
AssertionError: .../library-1180-1x.png averages 226 of 255 across its colour
channels, so it came out in the light scheme: the shot is following this
machine's colour-scheme preference rather than the dark scheme tools/shoot.py
pins
```

`tools/shoot.py` was restored immediately after; the working tree is clean.

## Also exercised on this commit

- `shoot-argument-contract.txt` - the tool refusing, with no PNG left behind,
  for a missing `--scale`, `--scale 3`, an unknown page, a directory that is
  not a fixture, and a width under the window's own minimum (660).
- `tests/gui-window-build.py`, `tests/gui-window-minimum.py` and
  `tests/gui-gio-actions.py`, each run bare beside the live desktop: each
  started its own display and bus and passed.
