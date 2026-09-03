# iPod Shuffle for Linux

Manage music on an iPod shuffle from Linux without iTunes.

This project provides a GTK 4 application and command-line scripts. You can manage a music library, create playlists, download audio, sync tracks, remove tracks, and wipe a device with confirmation and backup options.

![The iPod Shuffle application](docs/screenshot.png)

> [!IMPORTANT]
> The project is built and tested with the 4th-generation iPod shuffle, USB ID `05ac:1303`. The 3rd-generation model uses the same database format but is not tested. Do not use these tools with an iPod nano or another iPod model.

## Install

You need Linux, Python 3, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/max-miller1204/ipod-shuffle-linux.git
cd ipod-shuffle-linux
./install.sh
```

The installer creates a Python environment in `~/ipod-tools/`, downloads the iPod database builder, checks native dependencies, and installs a desktop entry for the application. By default, it asks for approval before it installs system packages. The `--yes` option skips that prompt.

Check the system without making changes:

```bash
./install.sh --check
```

See [Installation](docs/installation.md) for dependency details and manual setup options.

## Start the application

```bash
./ipod-gui.sh
```

You can also start **iPod Shuffle** from the application grid after installation.

The application finds a connected shuffle automatically. Add your music folders in **Device & Settings**, queue albums or tracks, and then select **Sync**. Device changes use the same shell scripts as the command line.

See the [Graphical interface guide](docs/graphical-interface.md) for library states, playlists, search, downloads, and preview playback.

## Use the command line

Sync a folder:

```bash
./ipod-sync.sh ~/Music/roadtrip
```

List tracks on the device:

```bash
./ipod-remove.sh --list
```

Remove one track and rebuild the device database:

```bash
./ipod-remove.sh 'roadtrip/01-highway.mp3'
```

Back up and wipe the device. Use a new directory outside the iPod:

```bash
./ipod-wipe.sh --backup ~/ipod-backups/shuffle-before-wipe
```

> [!WARNING]
> Sync can change the device. Remove, clear, and wipe operations delete data. Read the generated plan before you automate a device change. See [Safe automation](docs/command-line.md#safe-automation).

Run any script with `--help` for all options. See the [Command-line guide](docs/command-line.md) for sync, removal, wipe, playlist, and format details.

## How it works

The shuffle does not play a file only because it is on the volume. The firmware reads the binary file `iPod_Control/iTunes/iTunesSD`. This project copies supported audio, rebuilds that database, and can then unmount the device.

The project uses the reverse-engineered database builder from [nims11/IPod-Shuffle-4g](https://github.com/nims11/IPod-Shuffle-4g). Rockbox does not support iPod shuffle models.

## Documentation

- [Documentation index](docs/README.md)
- [Installation](docs/installation.md)
- [Graphical interface](docs/graphical-interface.md)
- [Command-line tools](docs/command-line.md)
- [YouTube downloads](docs/youtube-downloads.md)
- [Device notes and recovery](docs/device.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Machine interface](docs/machine-interface.md)
- [Development and tests](docs/development.md)

## Licence

GPL-2.0-only. See [LICENSE](LICENSE).
