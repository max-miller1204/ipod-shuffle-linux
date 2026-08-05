"""Print the track paths the GUI's library scan finds under a folder.

The suite uses this to hold the scan against what ipod-sync.sh copies out of
the same folder. The two walk separately - the script through find -L, the
scan through the tag reader in whichever interpreter has mutagen - and the
count the scan produces is what drives the sync progress bar, so a folder they
disagree about is a sync that finishes somewhere other than the end.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import gui  # noqa: E402

records, complete = gui.scan_tracks(sys.argv[1])
assert complete, f"scan of {sys.argv[1]} was incomplete"
json.dump(sorted(record["path"] for record in records), sys.stdout, indent=2)
sys.stdout.write("\n")
