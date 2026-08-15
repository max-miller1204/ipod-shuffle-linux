# One uv-managed environment: end-to-end evidence

Branch `unify-uv-python-environment`, base `75e00f5`, target `b5304ba`.
Everything below was produced on a machine whose first `python3` on `PATH` is a uv-managed 3.14, with the distro Python at `/usr/bin/python3` (3.12.3) carrying the GTK4 bindings - which is exactly the mismatch the change is about.

## `install-transcript.txt` - the installer a user runs

`./install.sh --no-system` against a throwaway `IPOD_TOOLS_DIR`, over the real network with the real `uv`.
It creates one environment and the closing report names it for the graphical interface as well as for the database builder, which is the whole point of the change:

```
==> Creating uv environment at .../venv
==> Synchronizing Python dependencies
==>   mutagen 1.48.1
==>   yt-dlp 2026.07.04
==>   graphical interface  ok (.../venv/bin/python)
```

That one interpreter reads GTK from the distro and its own packages from uv:

```
interpreter          .../venv/bin/python
built from           /usr/bin/python3.12 3.12.3
GTK 4.14 (distro)    /usr/lib/python3/dist-packages/gi
mutagen 1.48.1 (uv)  .../venv/lib/python3.12/site-packages/mutagen
distro site visible  True
its own pip?         0 executables named pip
```

A second run reuses it rather than rebuilding it.

## `gui-library-uv-environment.png` - the window running in it

The canonical four-album fixture rendered by `tools/shoot.py`, launched with that environment's interpreter and with `IPOD_VENV_PYTHON` deliberately pointed at nothing, so the only possible source of tags is the interpreter the window is running in.
Real artists and albums appear: Field Notes / Ana Petrov, Warm Ridge / Elle Marchetti, Nightbus / Kova, Slow Copper / The Fen.

`gui-library-without-mutagen.png` is the same command on a bare `/usr/bin/python3` with no crossover available, and is what the first shot would look like if the environment were not doing its job: one "Unknown album / Unknown artist" tile over "All 1".

## `interpreter-routing.txt` - which interpreter each entry point gets

After an install, the GUI launcher, the database builder and yt-dlp all resolve into the one environment.
Before an install - and with the uv-managed 3.14 first on `PATH` - both fallbacks still choose `/usr/bin/python3`, the interpreter `install.sh` validates and builds from.

## `fetch-update.txt` - `ipod-fetch.sh --update` with no pip in the environment

Pinned to `yt-dlp 2025.11.12`, then `./ipod-fetch.sh --update` moves it to `2026.07.04` through `uv pip install --python`.
With `uv` off `PATH` it refuses with exit 6 and a link to the installer instead of reaching for a pip that does not own the environment.

## `e2e-console.txt` and `e2e/` - `bash tests/product-e2e.sh`

The whole product suite, exit 0, 101 `PASS` lines, including the six this change is responsible for:

```
PASS: GUI and database fallbacks preferred the validated distro Python
PASS: a failed migration put the previous environment back
PASS: an interrupted migration put the previous environment back
PASS: a failed dependency synchronization left the installed environment intact
PASS: an environment predating the distro-Python contract was rebuilt from it
PASS: --update upgraded yt-dlp in an environment that holds no pip
```

`e2e/install-migration-failure.txt` and `e2e/install-migration-signalled.txt` are the transactional rebuild seen from the terminal: a synchronization that cannot reach an index, and one interrupted by a signal, both end with `Kept the environment already at .../venv` rather than leaving the machine with less than it had.

## `mcp-and-requirements.txt` - the corrected documentation, executed

`docs/machine-interface.md` now tells a client to launch `tools/mcp-server.py` with the interpreter `install.sh` builds.
Driving the protocol with exactly that interpreter passes, and the tag reader it reaches from there is the same interpreter rather than a crossover to somewhere else.
The documented hard requirement is real too: without `uv` the installer stops with a link to it.

## `repository-configuration.txt` - the gate configuration, as the gate reads it

`.no-mistakes.yaml` parsed into its semantic model: `auto_fix.review` is the integer `2`, `ignore_patterns` is `['.no-mistakes/**']` covering 461 tracked evidence files, and `commands.lint` is a 21-line string.
Those key names are checked against the installed gate binary's own YAML decoder tags, because a repo config parses non-strictly and a mistyped key would be dropped in silence.

## `lint-wrapper-check.txt` - the new test has teeth

`tests/lint-wrapper.py` is added by this test pass.
`tools/lint.py` is the only reader of the block scalar the checks are written down in, and a read that stopped short would run fewer checks while still exiting 0.
The file shows the new check failing when that reader is made to drop the block's last line, and passing against the real one.

The lint checks themselves were not run here; they belong to the pipeline's lint step.
