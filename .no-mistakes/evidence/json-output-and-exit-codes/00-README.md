# JSON output and stable exit codes: what was run, and what it showed

Everything here was produced by driving the shipped scripts the way a caller
drives them, on a device built by a real `ipod-sync.sh` run rather than by hand.
`driver-walkthrough.sh` is the whole session; the transcript, the two documents
and the code table below are its output, and the PNGs are that same session
rendered so it can be read without opening a file.

The upstream database builder is not installed on this machine, so the sync that
builds the device uses `tests/fake-db-builder.py`, exactly as the suite does.
`install.sh --check` is asked with none of those overrides, so what it reports is
what this machine can actually do.

| file | what it shows |
| --- | --- |
| `01-cli-walkthrough.txt` | the whole session: every command, its output and the code it left with |
| `02-device-report.json` | `ipod-remove.sh --list --json`, as a caller receives it |
| `03-install-check.json` | `install.sh --check --json`, as a caller receives it |
| `04-names-from-tags-round-trip.txt` | the same report over names a shell could not have emitted, and a path read out of it handed straight back |
| `05-exit-code-table.txt` | the code every state above actually produced |
| `06-device-report-json.png` | the device report beside the prose `--list` it used to be, and a caller reading it |
| `07-install-check.png` | `--check` reporting nine capabilities and leaving with 6, having installed nothing |
| `08-install-check-missing-packages.png` | the same probe on a machine with no ffmpeg and no speech engine, read as one apt line |
| `09-exit-codes.png` | the five states, each asserted as the number it is |
| `10-never-stale-and-table.png` | the refusals that print nothing at all, and the resulting table |

## The device report

`ipod-remove.sh --list --json` carries the mount point, the device identity, free
space, the track count, the track paths, the playlists with whether each can be
announced, and the options the last sync saved.
The plain `--list` above it in the transcript prints the same three tracks, so
the flag is the same answer in another shape rather than a second opinion.

The paths come back as `ipod-remove.sh` takes them.
`04-names-from-tags-round-trip.txt` reads one out of the document and hands it
straight back, twice: once for `Sigur Rós/Hoppípolla ★.mp3` and once for a name
holding a byte no UTF-8 decode accepts, which is what a FAT volume mounted under
the wrong iocharset gives back.
Both removals land, the playlist that listed the first goes with it, and the
following report says two tracks.
The document is pure ASCII either way, which is what keeps it valid under any
locale.

## The codes

`05-exit-code-table.txt` is not a transcription of the table in the README; each
row is the status a run in this session actually exited with.

    3  no iPod, whether autodetected or named explicitly
    4  several iPods
    5  the device stopped answering mid-operation
    6  a missing dependency
    7  a declined prompt
    1  everything a caller cannot act on differently

Code 5 is produced by unplugging the device during the database write, which is
the last thing every device-changing script does.
The row under it is the case that keeps the number meaningful: a builder that
fails while the iPod is still sitting there stays 1.
`ipod-report.py` run on its own answers a vanished device with the same 5.

## A definite answer or no answer

Three refusals are shown printing nothing at all on stdout: an album the walk
cannot enter, a saved-options file that exists and cannot be read, and a device
that went away under the report.
The first is the defect writing the report surfaced, since `Path.rglob` yields
nothing for a folder it cannot read and would have reported a full iPod as
empty.

The last pair is a machine with no `python3` anywhere on `PATH`: `--json` says so
once and leaves with 6, and plain `--list` still lists the device, because a
caller cannot install an interpreter to read a folder.

## What is not here

The post-install `Verifying` section is not exercised: it needs a privileged
install to reach.
It calls the same `probe_capabilities` and `report_capabilities` pair that
`07-install-check.png` shows rendering, so what is unproven here is the second
call site rather than the report.
