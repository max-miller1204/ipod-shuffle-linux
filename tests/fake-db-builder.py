#!/usr/bin/env python3
"""Test double for the upstream iTunesSD writer.

It records the exact argument vector and materializes the database file that
the product scripts promise to regenerate.
"""

import json
import os
import sys
from pathlib import Path


def main():
    mount_point = Path(sys.argv[-1])
    record_path = Path(os.environ["FAKE_DB_RECORD"])
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("a", encoding="utf-8") as record:
        record.write(json.dumps(sys.argv[1:]) + "\n")

    database = mount_point / "iPod_Control" / "iTunes" / "iTunesSD"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_text(
        "synthetic iTunesSD generated with " + " ".join(sys.argv[1:-1]) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
