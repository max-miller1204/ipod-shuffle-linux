#!/usr/bin/env python3
"""Run the repository's deterministic shell, architecture and syntax checks.

Those checks are the `lint` command in .no-mistakes.yaml, which is where the
pipeline runs them from and where they are written down once. This reads that
entry and runs it, so a contributor has one command to type without a second
copy of it to keep in step with the first: what runs here and what runs in the
pipeline cannot drift, because they are the same text.

Only the one block scalar is read rather than the whole document, because the
repository has no YAML parser among its dependencies and adding one in order
to run the checks would weigh more than the checks do. That keeps this to the
one shape the file uses: a literal block under `commands`.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / ".no-mistakes.yaml"


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def lint_command(document):
    """The literal block scalar at commands.lint, dedented to its own margin.

    Returns None when the file has no such entry, which is the one failure a
    caller can do anything about.
    """
    section = None
    key_indent = None
    body = []
    for line in document.splitlines():
        stripped = line.strip()
        if key_indent is not None:
            # A blank line inside a block scalar belongs to it; anything at or
            # left of the key's own indentation ends it.
            if not stripped or indent_of(line) > key_indent:
                body.append(line)
                continue
            break
        if not stripped or stripped.startswith("#"):
            continue
        if indent_of(line) == 0:
            section = stripped.rstrip(":")
        elif section == "commands" and stripped in ("lint: |", "lint: |-"):
            key_indent = indent_of(line)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        return None
    margin = min(indent_of(line) for line in body if line.strip())
    return "\n".join(line[margin:] if line.strip() else "" for line in body)


def main():
    command = lint_command(CONFIG.read_text())
    if command is None:
        print(f"{CONFIG} has no commands.lint to run", file=sys.stderr)
        return 1
    # From the repository root, because the command's own paths are relative
    # to it exactly as they are when the pipeline runs it.
    return subprocess.run(["bash", "-c", command], cwd=REPO).returncode


if __name__ == "__main__":
    raise SystemExit(main())
