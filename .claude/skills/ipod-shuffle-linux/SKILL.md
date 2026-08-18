---
name: ipod-shuffle-linux
description: Work safely in the iPod Shuffle Linux repository. Use for any change to its shell product, GTK interface, tests, demo fixture, or machine interfaces, especially sync, remove, wipe, playlists, device detection, and GUI automation.
---

# iPod Shuffle Linux

## Product boundary

Treat `ipod-sync.sh`, `ipod-remove.sh`, `ipod-wipe.sh`, and `ipod-fetch.sh` as the product.
The GTK application invokes these scripts rather than reimplementing device-changing behavior.
Keep copy rules, database rebuilds, saved options, device detection, removal, and wipe semantics in the scripts and their shared `lib.sh` helpers.
The GUI and other clients may plan, authorize, launch, and report a script run.
They must not grow a second implementation of it.

## Destructive-operation rails

Sync changes a device.
Remove, clear, and wipe destroy data.
Preserve all of these rules when changing a device operation:

1. Resolve and validate the target iPod before mutation.
2. Produce a non-mutating `--dry-run` JSON plan first for automated use.
3. Bind the plan's `confirmationToken` to its action, normalized arguments, mount, and device identity.
4. Refuse a destructive run that carries `--yes`, or whose input is not a terminal, unless its caller returned the plan's `--confirm-token TOKEN`.
   Have that caller pass the plan's identity back as `--expect-device ID` as well, which is checked whenever the plan carries a non-empty identity; a volume that reports neither a UUID nor a `SysInfo` has none to pin.
   Validate every non-empty `--confirm-token` against that run's own plan even when the run destroys nothing, so a wrong or stale token never reaches a copy or a database rebuild; only an omitted token is left to the destructive rule above.
5. Treat `--yes` as confirmation, not as authorization that bypasses the plan token.
6. Recheck device identity immediately before mutation and refuse a changed device.
7. Keep human-readable output on stdout and stderr while structured progress uses its explicitly opened descriptor.
8. End every structured progress stream with a `result` event, including failures.

Read [`docs/machine-interface.md`](../../../docs/machine-interface.md) before changing these contracts.

## GUI architecture

`IpodWindow` is one `Adw.ApplicationWindow` split into mixins by responsibility.
Cross-mixin state is an explicit architecture decision recorded in `tools/mixin-contract.py`.
Run `python3 tools/check.py staged`, which carries that check, after changing a mixin or shared window attribute.
Add intentional shared state to its `SHARED_STATE` table rather than bypassing the check.
Do not move device-changing behavior into a mixin.

`ipod_gui/__init__.py` imports modules eagerly after pinning GTK versions.
Its `__all__` list is ordered innermost first: each module may import only modules earlier in that list.
Add every package module to both that eager import tuple, which is alphabetical, and `__all__`, which is in dependency order.
A module named in `__all__` but absent from the import tuple still passes the harness's completeness check and then raises `AttributeError` in every harness-based test.
`tests/harness.py` relies on this order to find defining bindings and replace every imported copy in tests.
Use the harness facade for test doubles instead of patching one package module directly.

## Verification

Run the check closest to the behavior first, then the repository gates relevant to the change.
The main end-to-end product check is:

```bash
EVIDENCE_DIR=/tmp/ipod-shuffle-evidence \
  IPOD_REAL_DB_TOOL=/absolute/path/to/ipod-shuffle-4g.py \
  bash tests/product-e2e.sh
```

`EVIDENCE_DIR` preserves diagnostics that otherwise live in a temporary directory.
`IPOD_REAL_DB_TOOL` selects the upstream database builder used to prove rewritten playlist entries resolve, overriding the copy `./install.sh` clones into `~/ipod-tools/IPod-Shuffle-4g/`.
With neither the variable nor that copy, the local suite reports that hardware-compatible builder coverage was skipped rather than silently claiming it.

The repository-owned validation profiles are:

```bash
python3 tools/check.py staged
python3 tools/check.py push
python3 tools/check.py full
python3 tools/check.py fix
```

`staged` runs deterministic shell, architecture, Python syntax, and runner checks.
`push` adds the display-free behavioral checks, and `full` adds the real-window, screenshot, and product end-to-end checks required by CI.
`fix` is a declared no-op; add only explicitly approved mechanical rewrites to it.
The runner declares each check once, selects the installed application interpreter, preflights native capabilities, runs independent read-only checks concurrently, and prints their captured output in declaration order.
A check whose capabilities this machine lacks is reported as `[SKIP]` and leaves the profile exiting 2, while every check that can run still runs.
Each check's captured output is also written to `CHECK_EVIDENCE_DIR`, which a failing local run copies to `.check-evidence/`.
Change profile membership and commands in `tools/check.py`.

The real-window checks are:

```bash
python3 tests/gui-window-build.py
python3 tests/gui-window-minimum.py
python3 tests/gui-gio-actions.py
python3 tests/screenshot-harness.py
```

Run them directly.
Each re-executes through `tools/headless-run.py`, which requires `xvfb-run` and `dbus-run-session` and gives the process a private Xvfb display and D-Bus session.
Never weaken this by falling back to the user's desktop or existing application session.
Use `tools/headless-run.py COMMAND ...` for any new display-backed GTK check.
Set `SCREENSHOT_EVIDENCE_DIR` to retain screenshot artifacts.

Consult [`tools/check.py`](../../../tools/check.py) for the complete list of checks CI runs, and [`.github/workflows/tests.yml`](../../../.github/workflows/tests.yml) for the native dependencies it installs before invoking them.

## Canonical demo library

Build the deterministic four-album library, playlists, and synthetic iPod with:

```bash
python3 tools/demo-library.py /tmp/shuffle-demo
```

This command rebuilds the target directory, so use only a dedicated disposable path.
Pass `--keep` to add to an existing fixture or `--no-sync` to leave its synthetic device empty.
The command prints the exact isolated environment and launch recipe.
Use that fixture for screenshots and UI reproduction instead of a developer's real library or iPod.
Render deterministic evidence with:

```bash
python3 tools/shoot.py --fixture /tmp/shuffle-demo \
  --page library --width 1180 --scale 1 --output /tmp/library.png
```

## Machine interfaces

Do not rediscover or scrape terminal prose when a structured interface exists.
Use [`docs/machine-interface.md`](../../../docs/machine-interface.md) as the authoritative contract for fields, exit codes, timing, and authorization.
It documents:

- Script JSON reports and stable exit codes.
- `--dry-run`, `--expect-device`, and `--confirm-token` authorization.
- NDJSON progress on an explicitly selected file descriptor.
- `install.sh --check --json` capability reporting.
- The display-free `python3 -m ipod_gui.cli` library, device, search, playlists, cache, and config commands.
- `navigate`, `search`, `queue`, `refresh`, and stateful `dump-state` Gio actions on the running window.
- `tools/mcp-server.py`, which exposes read, plan, and execute tools over MCP stdio.

Gio actions return before asynchronous work lands.
Poll `dump-state` for the state transition a caller needs instead of assuming the next read is current.
MCP execute tools must retain the same expected-device and confirmation-token rails as direct script execution.
