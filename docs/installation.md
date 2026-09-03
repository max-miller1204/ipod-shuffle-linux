# Installation

## Requirements

Install these tools before you run the installer:

- A Linux distribution with Python 3
- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Clone the repository and run the installer:

```bash
git clone https://github.com/max-miller1204/ipod-shuffle-linux.git
cd ipod-shuffle-linux
./install.sh
```

The installer does these tasks:

1. Downloads the database builder to `~/ipod-tools/`.
2. Creates a uv environment from the distribution Python.
3. Installs the Python dependencies from `requirements.txt`.
4. Checks the required native packages.
5. Offers to install compatible missing system packages.
6. Installs an application-grid entry when GTK is available.

By default, the installer asks before it installs system packages. The `--yes` option skips this prompt. In a graphical session, the installer uses `pkexec` when it is available. In a text session, or when `pkexec` is not available, it uses `sudo`. Install and configure at least one applicable privilege helper before you request system-package installation.

If you move the repository after installation, run `./install.sh` again. This updates the path in the desktop entry.

## Installer options

Check the system and make no changes:

```bash
./install.sh --check
```

Skip system-package installation:

```bash
./install.sh --no-system
```

This option still downloads the database builder, creates or updates the Python environment, reports missing native packages, and manages the desktop entry.

Get the capability report as JSON:

```bash
./install.sh --check --json
```

Run `./install.sh --help` for all options. See [What is installed](machine-interface.md#what-is-installed) for the JSON schema and exit codes.

## Dependencies

| Component | Installation location | Purpose |
| --- | --- | --- |
| `mutagen` | uv environment | Reads artist, album, and track metadata |
| `yt-dlp` | uv environment | Downloads audio from YouTube |
| `python3-gi`, GTK 4, and Libadwaita | system | Runs the graphical interface |
| A supported speech engine | system | Creates spoken track and playlist names |
| `ffmpeg` | system | Converts unsupported audio and downloaded Opus audio |
| GStreamer | system | Plays previews through the computer |
| A supported JavaScript runtime | system | Solves the YouTube signature challenge |

The application can run without some optional features. The final installer report tells you which capabilities are available.

## Why GTK stays outside the uv environment

GTK 4 is a native library. PyGObject from PyPI needs development headers for GObject Introspection and Cairo. The distribution packages are smaller and are already built for the installed GTK libraries.

The installer creates the uv environment from the distribution Python and enables its system site packages. The environment reads PyGObject from the system and reads `mutagen` and `yt-dlp` from its own package directory. This also prevents an unrelated Python earlier on `PATH` from taking control of the application.

GStreamer uses the same PyGObject bindings. Speech engines and `ffmpeg` are native executables, so they also stay outside the uv environment.

The project recognizes `pico2wave`, `espeak`, and macOS `say` as speech engines. On apt-based Linux systems, the installer offers `libttspico-utils`, which provides `pico2wave`. You can install `espeak` as an alternative with your package manager.

## Preview playback packages

On Debian or Ubuntu, install the playback packages with:

```bash
sudo apt install gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

The base package provides the player. The good plugins provide MP3, WAV, and audio-output support. The bad plugins provide AAC playback for `.m4a` files.

The application still runs without these packages, but preview controls are not available.

## JavaScript for YouTube

Most YouTube downloads need a JavaScript runtime. The download script checks for these runtimes in order:

1. Deno 2.3 or later
2. Node.js 22 or later
3. Bun 1.2.11 through 1.3.14

The installer reports a missing runtime but does not install one. Some distribution packages are too old for `yt-dlp`. Use the [yt-dlp EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS) to install a supported runtime.

See [YouTube downloads](youtube-downloads.md) for more information.
