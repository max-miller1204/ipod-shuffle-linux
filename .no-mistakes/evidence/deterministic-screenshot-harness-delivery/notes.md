# Deterministic screenshot harness - test evidence

Everything below was produced by this branch's code, unmodified except where a
mutation is named, and every command ran with the desktop out of reach:
`DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR`
were all unset, so GTK reports `connected display: None` and a direct
`python3 ipod-gui.py` raises `Gtk couldn't be initialized` instead of opening a
window.
The harness still works there, because it starts a private Xvfb display and a
private session bus of its own for every shot.

## The shots

| file | command | size |
| --- | --- | --- |
| `library-1180-1x.png` | `--page library --width 1180 --scale 1` | 1180x760 |
| `playlists-760-2x.png` | `--page playlists --width 760 --scale 2` | 1520x1520 |
| `library-1180-2x.png` | `--page library --width 1180 --scale 2` | 2360x1520 |
| `readme-usage-library-1180-2x.png` | the README's own command, run verbatim | 2360x1520 |

The library shot reads `On iPod 1 / In library 3` with Warm Ridge badged
`On iPod`, which is what the fixture holds - the stale grid the previous run
recorded (`On iPod 0 / In library 4`) is gone.

## Deterministic, across runs and across fixtures

`readme-usage-library-1180-2x.png` and `library-1180-2x.png` are byte-identical
(`f5d97d90...`), and so are the playlists shots (`9e008a57...`), although they
were rendered by separate invocations, minutes apart, against two different
fixture directories, under two different environments.
`shoot-cli-transcript.txt` shows one command run twice landing on the same
SHA-256, and shows the tool refusing - with no PNG left behind - for a missing
`--scale`, a `--scale 3`, a width under the window's own minimum, a directory
that is not a fixture, and an unknown page.

## Never the desktop, never the running application

`isolation-from-running-app.txt` runs the harness beside a stand-in desktop: a
private X display with a real Shuffle already up on it, owning
`io.github.max_miller1204.IpodShuffle` on that display's session bus.

- Bypassing the runner (`SHUFFLE_HEADLESS_TEST=1`) shows what isolation is
  for: the shot is refused, because it would be forwarded to that Shuffle.
- As shipped, the same command renders its shot, the stand-in desktop's window
  list is unchanged (no harness window ever appears on it), the running Shuffle
  survives, and the PNG is byte-identical to the one taken with nothing else
  running.

`tests/headless-isolation.py` passes, which is where the display GDK actually
connected to, that display's screen size, and the bus the child was handed are
read back from inside the child.
`tests/gui-window-build.py`, `tests/gui-window-minimum.py` and
`tests/gui-gio-actions.py` were each run bare in the same desktop-less
environment and each started its own display and bus.

## The pixel coverage is live

`tests/screenshot-harness.py` was run against three mutations of
`tools/shoot.py` to see whether its assertions actually hold anything:

- the pre-fix `tools/shoot.py` from commit `524b475` fails at the first
  assertion - `library-1180-1x.png is (1180, 757), not the 1180x760 layout
  rendered at 1x`;
- moving `--scale` off the render node and onto the content allocation - the
  layout-versus-density defect the previous run recorded - passes every size
  and state guard in the tool and is caught only by the pixel comparison:
  `mean channel distance 14.78 over 5.0, or 10.59% of channels grossly apart
  over 2.0%`;
- dropping the coalesced-repaint wait, or the spinner wait, from the settle
  condition changed nothing on this machine: the shots came out byte-identical
  to the shipped ones.

## What a CI runner will actually upload

`ci-runner-library-1180-1x-no-mutagen.png` is the canonical library shot
rendered with no mutagen reachable, which is the state of the
`ubuntu-latest` runner the workflow builds: the four-album fixture collapses to
a single `Unknown album / Unknown artist` tile and the pills read `All 1 /
On iPod 0 / In library 1`.
`ci-runner-library-1180-1x-with-mutagen.png` is the same command with mutagen
reachable, for comparison: the four albums, their covers and their artists.
The workflow installs `python3-gi`, `xvfb`, `dbus`, `espeak` and `ffmpeg`, but
nothing that provides mutagen, and `ipod_gui/tags.py` reads tags through
whichever interpreter has it.

The theme differs between the two pairs for a second reason worth knowing: the
colour scheme is inherited from the invoking user's desktop preference through
the portal on the private bus, so the same command is dark on a dark GNOME
session and light on a runner.
