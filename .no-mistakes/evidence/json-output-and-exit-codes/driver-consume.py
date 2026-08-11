#!/usr/bin/env python3
"""What a caller does with the report instead of parsing sentences."""

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

print(f"schema           {report['schema']}")
print(f"mount point      {report['mount_point']}")
print(f"identity         {report['identity']}")
free = report["storage"]["free_bytes"] / 1000**3
total = report["storage"]["total_bytes"] / 1000**3
print(f"free space       {free:.1f} GB of {total:.1f} GB")
print(f"tracks           {report['track_count']}")
for track in report["tracks"]:
    print(f"                 {track}")
for playlist in report["playlists"]:
    voice = "announced" if playlist["spoken"] else "silent on the device"
    print(f"playlist         {playlist['name']} ({len(playlist['entries'])} entries, {voice})")
print(f"saved options    {' '.join(report['sync_options']) or '(none)'}")

# The paths come back exactly as ipod-remove.sh takes them, so a caller can
# hand one straight back without touching it.
print()
print("a path from the report, handed straight back:")
print(f"  ./ipod-remove.sh --yes {report['tracks'][0]!r}")
