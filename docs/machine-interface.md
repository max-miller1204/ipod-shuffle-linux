# Driving the scripts from another program

The [README](../README.md) is written for a person reading a terminal.
The scripts also answer in JSON, they report what they are doing while they do it, and they say what went wrong as a number rather than as English, so that another program can act on any of it without reading prose.

## What is on the device

```bash
./ipod-remove.sh --list --json
```

This is the same reading the window takes when you plug the iPod in, and it covers everything the window paints from:

| Field | What it holds |
| --- | --- |
| `mount_point` | Where the volume is mounted |
| `identity` | What the volume calls itself: its filesystem UUID, or a digest of the device's own `SysInfo` when it has no UUID, or `null` when it will say neither |
| `storage` | `total_bytes`, `used_bytes` and `free_bytes`, or `null` when the volume will not report its size |
| `track_count` | How many tracks are on the device |
| `tracks` | Each track's path under `iPod_Control/Music`, which is exactly what `ipod-remove.sh` takes back as an argument |
| `playlists` | Each playlist at the volume root: its `name`, its `entries`, and `spoken`, whether the device can say its name out loud |
| `sync_options` | The playlist and voiceover flags the last sync saved on the device |
| `schema` | `1`, and bumped only when a field changes meaning or leaves |

Without `--json`, `--list` still prints nothing but the track paths, so the two forms of the same question give the same answer in different shapes.

## Planning and authorizing changes

`ipod-sync.sh`, `ipod-remove.sh` and `ipod-wipe.sh` accept `--dry-run`.
It resolves the device and requested targets, prints one JSON plan, and changes nothing.
The plan names the `action`, the device `mount` and `identity`, whether the operation is `destructive`, its normalized `arguments`, and a `confirmationToken` bound to all of those values.
The plan is the whole of stdout: anything the run would have said to a person, such as which saved options it is replaying, goes to stderr instead, so what the caller parses is one document and nothing else.

A non-interactive clear, removal or wipe is refused even with `--yes` unless the caller first reads that plan and returns its token with `--confirm-token TOKEN`.
This prevents `--yes`, which records a human confirmation in an interactive flow, from becoming authorization merely because an automated caller copied the flag.
Pass the plan's device identity back as `--expect-device ID` as well.
Every device-changing script checks it after resolving the mount, and refuses if another iPod has replaced the one the caller inspected.
That refusal leaves with `1`, the code for a failure whose message is the explanation, and the message names both the identity that was expected and the one that answered.
`7` is kept for the missing or wrong token, which is the withheld approval a declined prompt reports.
A volume that will say neither a UUID nor a `SysInfo` of its own has no identity to pin: the plan carries an empty one, and passing an empty `--expect-device` back checks nothing.

```bash
plan="$(./ipod-wipe.sh --ipod "$mount" --dry-run)"
identity="$(printf '%s' "$plan" | jq -r '.device.identity')"
token="$(printf '%s' "$plan" | jq -r '.confirmationToken')"
./ipod-wipe.sh --ipod "$mount" --yes \
  --expect-device "$identity" --confirm-token "$token"
```

Changing an argument, choosing another mount, or replacing the device changes the token, so approval for one plan cannot authorize another.

## What a run is doing, while it is doing it

```bash
./ipod-sync.sh --progress-json ~/Music/roadtrip 3>progress.ndjson
./ipod-sync.sh --progress-json=7 ~/Music/roadtrip 7>&1 1>/dev/null | your-program
```

`ipod-sync.sh`, `ipod-remove.sh` and `ipod-wipe.sh` will report what they are doing as one JSON object per line, on a file descriptor you open.
The default is `3`; `--progress-json=FD` names another, which is what the window uses, since a descriptor it did not choose is one it would have to renumber in the child.
A descriptor nobody opened is refused rather than reported into nowhere.

This is an additional stream, not a replacement.
The scripts are the product, and what they print stays exactly as it reads in a terminal: the suite runs the same sync twice, once with the stream and once without, and compares the two outputs.

Every line is one event, and the `event` field says which:

| Event | What it says |
| --- | --- |
| `start` | The run has begun: which `script` it is, and the `schema` of what follows |
| `device` | The `ipod` it settled on, which is worth having when you let it autodetect |
| `plan` | How many items the run will report on, as `total` |
| `stage` | A `name`d stretch that is one long wait rather than an item at a time - `backup`, `clear`, `copy`, `rebuild` - with `state` `start` or `done` |
| `file` | One file the run has finished with: its `status`, its `name`, where it landed as `dest`, and `done` of `total` |
| `playlist` | One playlist: its `status`, its `name`, how many `tracks` it now holds, and `done` of `total` |
| `result` | The last line of every run: `ok`, the `code` the script is about to exit with, and what it did |

A file's `status` is `copied`, `duplicate` for one already on the device, `missing` for a playlist entry that is not on this computer, `broken` for a dangling symlink, or `removed`.
A playlist's is `written`, `removed`, or `skipped`.
The `result` carries whichever counters the script keeps: `copied`, `duplicates`, `unsupported`, `broken`, `removed`, `playlists`, and `tracks` for how many the device holds afterwards.
It reports what the run actually did rather than what it set out to do, so a removal stopped at the prompt says it removed nothing.

`done` counts the items in the plan and reaches `total` exactly, which is what lets it drive a bar that gets to the end.
Only files the firmware could play are counted, so the cover art in an album folder is neither planned nor reported - it is in the `unsupported` count and nowhere else.
`total` follows the count when a run turns out to have more to do than it planned: removing a track also takes it out of any playlist naming it, which nothing could have known before the removal.
A wipe emits no plan and no `file` events at all, because it is one bulk delete rather than a file at a time; its stages are what there is to show.

A run that fails still ends with a `result`, carrying the same code the script exits with, so a reader always has a last line rather than a stream that stops.
The stream is closed and its writer waited for before the script returns, so a caller holding the exit code has the whole document.
And a caller that stops reading half way through - a window closed during a copy - does not take the copy with it: the script says the stream has gone and carries on to the end.

The JSON is written by `ipod-report.py`, for the reason the reports above are, and it will not encode an event or a status that is not in its own table.
A typo in a script is a run that fails loudly rather than a line that reaches you as valid JSON meaning nothing.

## What is installed

```bash
./install.sh --check
./install.sh --check --json
```

`--check` installs nothing and writes nothing.
It reports each capability the project needs, whether this machine has it, and the packages that would provide it, then exits `0` when they are all there and `6` when one is not.
So a caller can find out whether a sync would work before deciding to run one.

With `--json` the same reading arrives as a document instead of as lines of prose:

| Field | What it holds |
| --- | --- |
| `satisfied` | Whether every capability is there, which is the same answer the exit code gives |
| `capabilities` | One entry per capability: its `id`, whether it is `available`, the `label` the installer prints for it, the `detail` it prints beside it, and the `packages` that provide it |
| `missing_packages` | The apt names for everything missing, as one list rather than one per capability, so a caller can install them in a single transaction |
| `schema` | `1`, on the same rule as the device report above |

The same report closes a real install, in the same words, because a caller that asked what this machine could do and a person watching an install finish have to be told the same thing.

## What went wrong

| Code | What it means | What a caller can do about it |
| --- | --- | --- |
| `0` | It worked | |
| `1` | Something else failed | Read the message |
| `3` | No iPod, either mounted or at the path given | Plug one in, or name a different path |
| `4` | Several iPods are connected | Name which one with `--ipod` |
| `5` | The iPod stopped answering part way through | Plug it back in and try again |
| `6` | A dependency is missing | Run `./install.sh`, or `./install.sh --check` to find out which |
| `7` | A prompt was declined or a non-interactive destructive plan lacked its token | Ask again, or run `--dry-run` and return its `confirmationToken` |

Code `5` is the one worth explaining.
Unplugging an iPod mid-copy is the failure this project sees most, and it arrives as whichever command happened to touch the volume next: a copy that cannot write, a walk that cannot descend, a database builder that cannot open its file.
Rather than name a code at each of those and still miss the next one, the scripts remember what the volume called itself when they latched onto it, and on the way out of any failure they look at whether that volume is still there.
A volume that has gone, or that has been replaced by a different one at the same path, turns the failure into `5`.
A builder that fails while the iPod is still sitting there stays `1`.
Asking what the volume calls itself needs `python3`, and a machine without one is treated as a volume that will not say rather than as a failure of its own, since this question is asked on the way out of every other failure and has to come back with an answer.
Such a machine still gets `6` from the work that genuinely needs the interpreter - the JSON report, the progress stream and the database builder - while a plain `--list`, which never needed it, still answers.

`--json` follows one rule: a whole document or nothing at all.
A report is assembled in full, checked against the device it started reading, and only then written, so a device unplugged half way through produces exit `5` and an empty stream rather than a confident-looking description of a device nobody can see.
Anything it cannot read stops it the same way.
An album whose folder cannot be entered is the case worth naming, because nothing fails on its own there: the directory walk the window uses simply yields nothing for that folder, and the report would have counted the tracks it could see and called that the device.
A caller told a full iPod holds nothing is one about to sync an entire library onto it.

This is the rule `ipod-fetch.sh --new-tracks FILE` already followed by deleting the file rather than leaving it stale when `yt-dlp` cannot say what it downloaded.
A stale answer reads as a definite one to whoever picks it up next.
