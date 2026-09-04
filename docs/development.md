# Development and tests

Read [`.claude/skills/ipod-shuffle-linux/SKILL.md`](../.claude/skills/ipod-shuffle-linux/SKILL.md) before you change the repository.

## Product boundary

The shell scripts are the product:

- `ipod-sync.sh`
- `ipod-remove.sh`
- `ipod-wipe.sh`
- `ipod-fetch.sh`

Keep device-changing behavior in these scripts and their shared `lib.sh` helpers. The GTK application can plan, authorize, start, and report a script run. It must not implement a second copy of sync, remove, or wipe behavior.

Read [Machine interface](machine-interface.md) before you change JSON reports, progress events, exit codes, authorization, CLI output, D-Bus actions, or MCP tools.

## Code layout

| Path | Purpose |
| --- | --- |
| `install.sh` | Creates the environment and installs the desktop entry |
| `lib.sh` | Shared device detection and shell helpers |
| `ipod-report.py` | Writes safe JSON reports and NDJSON progress for shell scripts |
| `ipod-gui.py`, `ipod-gui.sh` | Start the GTK application |
| `ipod_gui/` | Application package |
| `tools/mcp-server.py` | Exposes read, plan, and execute tools over MCP stdio |
| `tools/check.py` | Owns all validation profiles |
| `tools/demo-library.py` | Builds a deterministic demo library and synthetic iPod |
| `tools/shoot.py` | Renders deterministic screenshots |
| `tests/` | Behavioral, integration, architecture, and GUI checks |

### Application modules

The `IpodWindow` class is split into mixins by responsibility.

| Module | Responsibility |
| --- | --- |
| `config.py` | Paths, settings, and track states |
| `text.py` | User-facing text and number formatting |
| `tags.py` | Metadata reading and local scans |
| `device.py` | iPod detection and device reads |
| `shell.py` | Installed-capability checks |
| `youtube.py` | Search, artwork, and download commands |
| `previews.py` | Preview cache |
| `model.py` | Merged library and album model |
| `playlists.py` | Local M3U storage |
| `theme.py` | Application stylesheet |
| `widgets.py` | Shared widgets |
| `player.py` | GStreamer pipeline |
| `*_view.py` | View construction and view-specific behavior |
| `queue.py` | Staged sync sources and tracks |
| `commands.py` | Script execution and progress handling |
| `window.py` | Window assembly and shared state |
| `app.py` | `Adw.Application` |
| `cli.py` | Display-free JSON interface to the model |

`ipod_gui/__init__.py` imports modules eagerly. Its `__all__` entries are in dependency order, with the innermost module first. A module can import only modules that occur before it. Add each new package module to both the eager import tuple and `__all__`.

`tools/mixin-contract.py` defines intentional state shared by mixins. Add new shared state to its `SHARED_STATE` table. Do not bypass the check.

Tests use `tests/harness.py` to replace imported names across all package modules. Use this harness instead of patching one module binding directly.

## Validation profiles

Run the smallest relevant check first. Then run one of the repository profiles:

```bash
python3 tools/check.py staged
python3 tools/check.py push
python3 tools/check.py full
python3 tools/check.py fix
```

| Profile | Scope |
| --- | --- |
| `staged` | Deterministic shell, architecture, Python syntax, and runner checks |
| `push` | `staged` plus display-free behavioral checks |
| `full` | `push` plus real-window, screenshot, and product end-to-end checks |
| `fix` | No changes at present; reserved for approved mechanical rewrites |

Checks declare their required capabilities. An unavailable capability produces `[SKIP]`, but the runner continues with all other checks. A profile exits `2` when at least one check was skipped and `1` when a check failed.

Set `CHECK_EVIDENCE_DIR` to keep the output from each check. A failed local run also copies evidence to `.check-evidence/`.

Install Git hooks with:

```bash
uv tool install pre-commit
pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Product end-to-end test

Run the complete shell-product test with:

```bash
EVIDENCE_DIR=/tmp/ipod-shuffle-evidence \
IPOD_REAL_DB_TOOL=/absolute/path/to/ipod-shuffle-4g.py \
bash tests/product-e2e.sh
```

The test uses a synthetic iPod and does not need physical hardware. The upstream database builder proves that rewritten playlist entries resolve. If the builder is not available, the local suite reports that this coverage was skipped.

Some checks need an unprivileged user because root ignores permission bits. CI runs the suite as an unprivileged user.

## Real-window tests

These checks construct GTK windows:

```bash
python3 tests/gui-window-build.py
python3 tests/gui-window-minimum.py
python3 tests/gui-gio-actions.py
python3 tests/screenshot-harness.py
```

Each check re-executes through `tools/headless-run.py`. The runner requires `xvfb-run` and `dbus-run-session`. It gives the process a private X11 display and D-Bus session. It never falls back to the current desktop.

Use the same runner for a new display-backed check:

```bash
python3 tools/headless-run.py COMMAND ...
```

Set `SCREENSHOT_EVIDENCE_DIR` to retain screenshots. Set `IPOD_KEEP_SCRATCH=1` to retain temporary fixtures from `tests/scratch.py`.

## Demo library and screenshots

Build the canonical four-album library, playlists, and synthetic iPod in a disposable path:

```bash
python3 tools/demo-library.py /tmp/shuffle-demo
```

This command rebuilds its target directory. Use `--keep` to add to an existing fixture or `--no-sync` to leave the synthetic device empty.

Render a deterministic page:

```bash
python3 tools/shoot.py --fixture /tmp/shuffle-demo \
  --page library --width 1180 --scale 1 \
  --output /tmp/library.png
```

`--page` accepts `library`, `playlists`, `search`, or `settings`. `--scale` accepts `1` or `2` and changes raster density, not layout. The renderer fixes the color scheme and refuses invalid dimensions instead of silently changing them.

Render the playlist-aware search evidence:

```bash
python3 tools/shoot.py --fixture /tmp/shuffle-demo \
  --page search --width 1180 --scale 1 \
  --output /tmp/search.png
```

## Continuous integration

`.github/workflows/tests.yml` installs native test dependencies and the pinned upstream database builder. It runs:

```bash
python3 tools/check.py full
```

The workflow uploads screenshot and test evidence even when validation fails.
