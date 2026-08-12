# `python3 -m ipod_gui.cli` - the application's model, with no window

A real session against a throwaway `HOME`: three tagged MP3s and two cached
previews built with ffmpeg, and the repository's yt-dlp stand-in for the
YouTube search so nothing reaches the network.
`DISPLAY` and `WAYLAND_DISPLAY` are unset for every run below, and the
command still enters through the `ipod_gui` package, so PyGObject is loaded
and no display is needed.

Each block is what a caller actually gets: the command, its stdout, its
stderr, and its exit code. Absolute paths are shown as `$HOME`.

## No display anywhere, and the entry point still loads the GTK bindings

```console
$ echo "DISPLAY=[$DISPLAY] WAYLAND_DISPLAY=[$WAYLAND_DISPLAY]"; /usr/bin/python3 -c 'import sys, ipod_gui.cli; from gi.repository import Gtk; print("gi loaded:", "gi" in sys.modules); print("GTK pinned by the package:", Gtk.get_major_version(), Gtk.get_minor_version())'
DISPLAY=[] WAYLAND_DISPLAY=[]
gi loaded: True
GTK pinned by the package: 4 14
```

## The command surface, as the CLI itself describes it

```console
$ python3 -m ipod_gui.cli --help
usage: python3 -m ipod_gui.cli [-h]
                               {library,device,search,playlists,cache,config}
                               ...

positional arguments:
  {library,device,search,playlists,cache,config}

options:
  -h, --help            show this help message and exit
```

## One subcommand's own arguments

```console
$ python3 -m ipod_gui.cli playlists --help
usage: python3 -m ipod_gui.cli playlists [-h] [--root ROOT]
                                         {list,create,add,remove,reorder} ...

positional arguments:
  {list,create,add,remove,reorder}

options:
  -h, --help            show this help message and exit
  --root ROOT
```

## Configuration: the roots and layout the window reads from the same file

```console
$ python3 -m ipod_gui.cli config --music-root $HOME/Music --group album --view grid
{
  "schema": 1,
  "command": "config",
  "result": {
    "file": "$HOME/config/ipod-shuffle-linux/config.json",
    "musicRoots": [
      "$HOME/Music"
    ],
    "group": "album",
    "view": "grid"
  }
}
$ echo $?
0
```

## The config file the window itself reads

```console
$ cat "$XDG_CONFIG_HOME"/ipod-shuffle-linux/config.json
{
  "music_roots": [
    "$HOME/Music"
  ],
  "group_mode": "album",
  "view_mode": "grid"
}
```

## The library: tracks, albums, per-state counts

```console
$ python3 -m ipod_gui.cli library
{
  "schema": 1,
  "command": "library",
  "result": {
    "tracks": [
      {
        "path": "$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3",
        "title": "Surfin Safari",
        "artist": "Beach Boys",
        "album": "Surfin Safari",
        "genre": "Surf",
        "duration": 2.0375510204081633,
        "trackNumber": 1,
        "art": null,
        "size": 16628,
        "state": "library",
        "onIpod": false
      },
      {
        "path": "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "title": "Heart of Gold",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 3.030204081632653,
        "trackNumber": 3,
        "art": null,
        "size": 24568,
        "state": "library",
        "onIpod": false
      },
      {
        "path": "$HOME/Music/Neil Young/Harvest/06 Old Man.mp3",
        "title": "Old Man",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 4.048979591836734,
        "trackNumber": 6,
        "art": null,
        "size": 32712,
        "state": "library",
        "onIpod": false
      }
    ],
    "albums": [
      {
        "title": "Surfin Safari",
        "artist": "Beach Boys",
        "state": "library",
        "trackCount": 1
      },
      {
        "title": "Harvest",
        "artist": "Neil Young",
        "state": "library",
        "trackCount": 2
      }
    ],
    "counts": {
      "ipod": 0,
      "queued": 0,
      "library": 3,
      "preview": 0
    },
    "complete": true
  }
}
$ echo $?
0
```

## The same library grouped by artist

```console
$ python3 -m ipod_gui.cli library --group artist
{
  "schema": 1,
  "command": "library",
  "result": {
    "tracks": [
      {
        "path": "$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3",
        "title": "Surfin Safari",
        "artist": "Beach Boys",
        "album": "Surfin Safari",
        "genre": "Surf",
        "duration": 2.0375510204081633,
        "trackNumber": 1,
        "art": null,
        "size": 16628,
        "state": "library",
        "onIpod": false
      },
      {
        "path": "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "title": "Heart of Gold",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 3.030204081632653,
        "trackNumber": 3,
        "art": null,
        "size": 24568,
        "state": "library",
        "onIpod": false
      },
      {
        "path": "$HOME/Music/Neil Young/Harvest/06 Old Man.mp3",
        "title": "Old Man",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 4.048979591836734,
        "trackNumber": 6,
        "art": null,
        "size": 32712,
        "state": "library",
        "onIpod": false
      }
    ],
    "albums": [
      {
        "title": "Beach Boys",
        "artist": "1 album",
        "state": "library",
        "trackCount": 1
      },
      {
        "title": "Neil Young",
        "artist": "1 album",
        "state": "library",
        "trackCount": 2
      }
    ],
    "counts": {
      "ipod": 0,
      "queued": 0,
      "library": 3,
      "preview": 0
    },
    "complete": true
  }
}
$ echo $?
0
```

## One device probe, with nothing plugged in

```console
$ python3 -m ipod_gui.cli device
{
  "schema": 1,
  "command": "device",
  "result": {
    "candidates": [],
    "mountPoint": null,
    "identity": null,
    "readable": true,
    "trackCount": 0,
    "playlists": [],
    "storage": null
  }
}
$ echo $?
0
```

## One device probe, with a shuffle mounted: what the window reads in one pass

```console
$ PATH=tests/bin:$PATH FAKE_IPOD_MOUNT='$HOME/media/ALEX IPOD' python3 -m ipod_gui.cli device
{
  "schema": 1,
  "command": "device",
  "result": {
    "candidates": [
      "$HOME/media/ALEX IPOD"
    ],
    "mountPoint": "$HOME/media/ALEX IPOD",
    "identity": "uuid:/dev/sdz",
    "readable": true,
    "trackCount": 3,
    "playlists": [
      {
        "name": "Beach Day",
        "entries": [
          "F00/Surfin Safari.mp3"
        ],
        "spoken": false
      },
      {
        "name": "Road Trip",
        "entries": [
          "F00/Heart of Gold.mp3",
          "F00/Old Man.mp3"
        ],
        "spoken": true
      }
    ],
    "storage": {
      "totalBytes": 491001659392,
      "usedBytes": 122939367424,
      "freeBytes": 343045525504
    }
  }
}
$ echo $?
0
```

## Local search over the scanned library

```console
$ python3 -m ipod_gui.cli search heart
{
  "schema": 1,
  "command": "search",
  "result": {
    "local": [
      {
        "path": "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "title": "Heart of Gold",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 3.030204081632653,
        "trackNumber": 3,
        "art": null,
        "size": 24568,
        "state": "library",
        "onIpod": false
      }
    ],
    "complete": true
  }
}
$ echo $?
0
```

## The same search with YouTube asked as well (yt-dlp stand-in, no network)

```console
$ python3 -m ipod_gui.cli search 'heart of gold' --youtube
{
  "schema": 1,
  "command": "search",
  "result": {
    "local": [
      {
        "path": "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "title": "Heart of Gold",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 3.030204081632653,
        "trackNumber": 3,
        "art": null,
        "size": 24568,
        "state": "library",
        "onIpod": false
      }
    ],
    "complete": true,
    "youtube": [
      {
        "title": "Test Track (heart of gold)",
        "uploader": "Test Artist",
        "duration": 180.0,
        "url": "https://www.youtube.com/watch?v=testvideo",
        "video_id": "testvideo",
        "thumbnail": "https://i.ytimg.com/vi/testvideo/hqdefault.jpg",
        "playlist_title": "",
        "playlist_count": 0
      }
    ],
    "reachedYoutube": true
  }
}
$ echo $?
0
```

## A YouTube search that could not reach YouTube: empty results, and says so

```console
$ FAKE_YTDLP_SEARCH_FAILS=1 python3 -m ipod_gui.cli search 'heart of gold' --youtube
{
  "schema": 1,
  "command": "search",
  "result": {
    "local": [
      {
        "path": "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "title": "Heart of Gold",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 3.030204081632653,
        "trackNumber": 3,
        "art": null,
        "size": 24568,
        "state": "library",
        "onIpod": false
      }
    ],
    "complete": true,
    "youtube": [],
    "reachedYoutube": false
  }
}
$ echo $?
0
```

## Create an M3U playlist with one track

```console
$ python3 -m ipod_gui.cli playlists create 'Road Trip' '$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3'
{
  "schema": 1,
  "command": "playlists",
  "result": {
    "name": "Road Trip",
    "path": "$HOME/Music/Playlists/Road Trip.m3u",
    "editable": true,
    "entries": [
      "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3"
    ]
  }
}
$ echo $?
0
```

## Add two more tracks

```console
$ python3 -m ipod_gui.cli playlists add 'Road Trip' '$HOME/Music/Neil Young/Harvest/06 Old Man.mp3' '$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3'
{
  "schema": 1,
  "command": "playlists",
  "result": {
    "added": 2
  }
}
$ echo $?
0
```

## List the playlists in the library folder

```console
$ python3 -m ipod_gui.cli playlists list
{
  "schema": 1,
  "command": "playlists",
  "result": [
    {
      "name": "Road Trip",
      "path": "$HOME/Music/Playlists/Road Trip.m3u",
      "editable": true,
      "entries": [
        "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "$HOME/Music/Neil Young/Harvest/06 Old Man.mp3",
        "$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3"
      ]
    }
  ]
}
$ echo $?
0
```

## Move the third entry to the front

```console
$ python3 -m ipod_gui.cli playlists reorder 'Road Trip' 2 0
{
  "schema": 1,
  "command": "playlists",
  "result": {
    "moved": true
  }
}
$ echo $?
0
```

## Remove one track

```console
$ python3 -m ipod_gui.cli playlists remove 'Road Trip' '$HOME/Music/Neil Young/Harvest/06 Old Man.mp3'
{
  "schema": 1,
  "command": "playlists",
  "result": {
    "removed": 1
  }
}
$ echo $?
0
```

## The playlist as it now stands

```console
$ python3 -m ipod_gui.cli playlists list
{
  "schema": 1,
  "command": "playlists",
  "result": [
    {
      "name": "Road Trip",
      "path": "$HOME/Music/Playlists/Road Trip.m3u",
      "editable": true,
      "entries": [
        "$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3",
        "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3"
      ]
    }
  ]
}
$ echo $?
0
```

## The M3U left on disk, which the window and the sync read back

```console
$ cat "$HOME/Music/Playlists/Road Trip.m3u"
#EXTM3U
$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3
$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3
```

## The preview cache before anything is pruned

```console
$ find "$HOME/cache/ipod-shuffle-linux/previews" | sort
$HOME/cache/ipod-shuffle-linux/previews
$HOME/cache/ipod-shuffle-linux/previews/Beach Boys
$HOME/cache/ipod-shuffle-linux/previews/Beach Boys/Surfin Safari [othervideo].mp3
$HOME/cache/ipod-shuffle-linux/previews/Neil Young
$HOME/cache/ipod-shuffle-linux/previews/Neil Young/Heart of Gold [testvideo].mp3
```

## Preview cache status: what is in it and how big it is

```console
$ python3 -m ipod_gui.cli cache status
{
  "schema": 1,
  "command": "cache",
  "result": {
    "root": "$HOME/cache/ipod-shuffle-linux/previews",
    "sizeBytes": 113093,
    "removed": [],
    "entries": [
      {
        "path": "$HOME/cache/ipod-shuffle-linux/previews/Neil Young/Heart of Gold [testvideo].mp3",
        "size": 96566,
        "mtime": 1.0
      },
      {
        "path": "$HOME/cache/ipod-shuffle-linux/previews/Beach Boys/Surfin Safari [othervideo].mp3",
        "size": 16527,
        "mtime": 2.0
      }
    ],
    "complete": true
  }
}
$ echo $?
0
```

## Prune the cache to a 17039 byte budget: the oldest preview goes

```console
$ python3 -m ipod_gui.cli cache prune --limit 17039
{
  "schema": 1,
  "command": "cache",
  "result": {
    "root": "$HOME/cache/ipod-shuffle-linux/previews",
    "sizeBytes": 16527,
    "removed": [
      "$HOME/cache/ipod-shuffle-linux/previews/Neil Young/Heart of Gold [testvideo].mp3"
    ],
    "entries": [
      {
        "path": "$HOME/cache/ipod-shuffle-linux/previews/Beach Boys/Surfin Safari [othervideo].mp3",
        "size": 16527,
        "mtime": 2.0
      }
    ],
    "complete": true
  }
}
$ echo $?
0
```

## The pruned preview's artist folder went with it

```console
$ find "$HOME/cache/ipod-shuffle-linux/previews" | sort
$HOME/cache/ipod-shuffle-linux/previews
$HOME/cache/ipod-shuffle-linux/previews/Beach Boys
$HOME/cache/ipod-shuffle-linux/previews/Beach Boys/Surfin Safari [othervideo].mp3
```

## Clear what is left

```console
$ python3 -m ipod_gui.cli cache clear
{
  "schema": 1,
  "command": "cache",
  "result": {
    "root": "$HOME/cache/ipod-shuffle-linux/previews",
    "sizeBytes": 0,
    "removed": [
      "$HOME/cache/ipod-shuffle-linux/previews/Beach Boys/Surfin Safari [othervideo].mp3"
    ],
    "entries": [],
    "complete": true
  }
}
$ echo $?
0
```

## The cache is empty, and the cache root itself is still there

```console
$ find "$HOME/cache/ipod-shuffle-linux/previews" | sort
$HOME/cache/ipod-shuffle-linux/previews
```

## Refusal: a playlist that is not there (exit 1, a sentence, no document)

```console
$ python3 -m ipod_gui.cli playlists add Nowhere '$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3'
playlist not found: Nowhere
$ echo $?
1
```

## Refusal: a name FAT cannot store never reaches the folder

```console
$ python3 -m ipod_gui.cli playlists create AC/DC
A playlist name cannot contain \ / : * ? " < > |
$ echo $?
1
```

## Usage error: a position that is not a number (exit 2, from the parser)

```console
$ python3 -m ipod_gui.cli playlists reorder 'Road Trip' first 0
usage: python3 -m ipod_gui.cli playlists reorder [-h] name source target
python3 -m ipod_gui.cli playlists reorder: error: argument source: invalid int value: 'first'
$ echo $?
2
```

## Two roots configured, one of them missing

```console
$ python3 -m ipod_gui.cli config --music-root $HOME/Music --music-root $HOME/Gone
{
  "schema": 1,
  "command": "config",
  "result": {
    "file": "$HOME/config/ipod-shuffle-linux/config.json",
    "musicRoots": [
      "$HOME/Music",
      "$HOME/Gone"
    ],
    "group": "album",
    "view": "grid"
  }
}
$ echo $?
0
```

## Partial answer: what was read is still written, marked `complete: false`, exit 1

```console
$ python3 -m ipod_gui.cli library
{
  "schema": 1,
  "command": "library",
  "result": {
    "tracks": [
      {
        "path": "$HOME/Music/Beach Boys/Surfin Safari/01 Surfin Safari.mp3",
        "title": "Surfin Safari",
        "artist": "Beach Boys",
        "album": "Surfin Safari",
        "genre": "Surf",
        "duration": 2.0375510204081633,
        "trackNumber": 1,
        "art": null,
        "size": 16628,
        "state": "library",
        "onIpod": false
      },
      {
        "path": "$HOME/Music/Neil Young/Harvest/03 Heart of Gold.mp3",
        "title": "Heart of Gold",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 3.030204081632653,
        "trackNumber": 3,
        "art": null,
        "size": 24568,
        "state": "library",
        "onIpod": false
      },
      {
        "path": "$HOME/Music/Neil Young/Harvest/06 Old Man.mp3",
        "title": "Old Man",
        "artist": "Neil Young",
        "album": "Harvest",
        "genre": "Folk Rock",
        "duration": 4.048979591836734,
        "trackNumber": 6,
        "art": null,
        "size": 32712,
        "state": "library",
        "onIpod": false
      }
    ],
    "albums": [
      {
        "title": "Surfin Safari",
        "artist": "Beach Boys",
        "state": "library",
        "trackCount": 1
      },
      {
        "title": "Harvest",
        "artist": "Neil Young",
        "state": "library",
        "trackCount": 2
      }
    ],
    "counts": {
      "ipod": 0,
      "queued": 0,
      "library": 3,
      "preview": 0
    },
    "complete": false
  }
}
a music folder could not be read through: this answer is partial
$ echo $?
1
```
