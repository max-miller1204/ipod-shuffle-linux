# Rails for destructive operations run non-interactively: what was run, and what it showed

Everything here was produced by driving the shipped scripts and the shipped
window the way a caller drives them.
The iPod is a synthetic volume, as it is throughout this project's tests, and
the database builder is `tests/fake-db-builder.py`; nothing else is stood in
for.

`driver-handshake.sh` is the whole CLI session and `01-cli-walkthrough.txt` is
its output.
`driver-gui-screenshots.sh` runs the real `IpodWindow` under a display and
photographs it deleting a track off the device.
`driver-before-after.sh` runs the same command against the commit this branch
starts from and against this checkout, which is the difference the goal is
about.

| file | what it shows |
| --- | --- |
| `01-cli-walkthrough.txt` | the whole session: every command, its output, and the code it left with |
| `02-sync-plan.json` | the plan `ipod-sync.sh --clear --dry-run` printed, as a caller receives it |
| `03-remove-plan.json` | the same for `ipod-remove.sh` |
| `04-wipe-plan.json` | the same for `ipod-wipe.sh` |
| `05-exit-codes.tsv` | the code every state in the session actually produced |
| `06-refused-then-planned.png` | a caller with no terminal refused, then reading the plan instead |
| `07-approval-is-bound-to-the-plan.png` | a guessed token, another plan's token, a changed plan and a swapped device, then the run that was approved |
| `08-wipe-and-a-person-at-a-terminal.png` | a wipe through the handshake, and the same `--clear` answered by hand at a terminal |
| `09-exit-codes.png` | that table, rendered |
| `10-before-and-after.txt` | the same `--clear --yes` on `fafb2d7` and on this branch, on the same device |
| `20-tracks-on-the-ipod.png` | the app with three tracks on the device |
| `21-remove-asks-first.png` | the Remove button pressed, and the only consent in the run |
| `22-removed-through-the-handshake.png` | the track gone from the iPod and left in the library, the device down to two |
| `23-gui-details-pane.txt` | `ipod-remove.sh`'s own output, read back off the app's Details pane |

## The handshake, from the caller's side

A destructive run with no terminal behind it is refused, `--yes` or not
(`01-cli-walkthrough.txt`, scene 1, exit 7).
`--dry-run` prints one JSON document on stdout and changes nothing: the
session checksums every file on the volume either side of the plan and diffs
them, and the anything a person would have been told - the warning about the
USB id, the saved options being replayed - goes to stderr where it cannot
break the document the caller parses.

The token is bound to the whole plan, so scene 3 gets nowhere four different
ways: a token invented from nothing, this plan's token on a run that names a
different folder, this plan's token on a run that also ejects, and a device
identity that belongs to another iPod.
After all four the volume is byte for byte what it was.
Scene 4 is the same command carrying the plan's own token and the plan's own
device, and it clears and copies.

`ipod-remove.sh` still refuses a path that climbs out of the music folder
before any of this, and `ipod-wipe.sh` still keeps the Speakable prompts the
firmware needs and verifies its backup.

## The person at the terminal

Scene 7 runs `--clear` under a pseudo-terminal with no `--yes` and no token at
all, and answers `y` at the prompt.
That is the case the rails must not reach: there is somebody to ask, so the
question is asked and the answer is the authorization.

## The window

The app is opened from the app grid, so it has no terminal either - which is
why the three pictures matter.
`driver-gui-screenshots.py` closes the process's own stdin onto `/dev/null`
before the window starts, presses Remove on a track the way a finger presses
it, answers the confirmation, and photographs the result: the track leaves the
iPod, stays in the library, and the device count drops from three to two.
The removal it ran is `ipod-remove.sh --yes`, which would have been refused if
the window had not planned it and returned the plan's token first.

## Before and after

`10-before-and-after.txt` is the point of the goal in twenty lines.
On `fafb2d7`, `ipod-sync.sh --clear --yes` from a script with nobody watching
deleted both tracks on the device and left `--dry-run` unrecognised.
On this branch the same command stops at exit 7 with the device untouched, and
`--dry-run` answers with the plan it wants the caller to read.

## Where the automated coverage lives

`tests/product-e2e.sh` drives all of this as assertions - the plans, the
device left byte for byte identical by a dry run, the guessed token, the
borrowed token, the changed plan, the swapped device, the path escape, the
preserved Speakable prompts - and `tests/gui-progress-stream.py` drives the
window's half against a real script, including a track named `--list` to
prove the window can still tell a removal from a listing.
