#!/usr/bin/env python3
"""Checks the wrapper that runs the checks written down in .no-mistakes.yaml.

`tools/lint.py` exists so a contributor and the pipeline run the same checks
rather than two copies that drift, which only holds if the wrapper runs the
whole of what that file configures. The failure worth a check of its own is
the quiet one: a block scalar read short still runs, still exits 0, and has
simply stopped doing one of the checks - so every case here is decided by
what the command actually did rather than by what the wrapper returned alone.

The configuration is written here rather than read from the repository's own,
because a check that only ever saw one file would pass on a parser that
handled that file and nothing else. The real file is used for the one thing
only it can answer: that what the wrapper extracts from it today is whole.
"""

import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Loaded by path for the same reason the other tools are: it is a script, and
# running the real one is the point.
_spec = importlib.util.spec_from_file_location("lint_wrapper", REPO / "tools" / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

# The shape the repository's own file uses, and the shapes around it that a
# reader can trip over: a comment and a blank line inside the block, a nested
# quoted script carrying a `#` of its own, a `lint:` under another section
# that is not this one, and a top-level key after the block that ends it.
CONFIGURATION = """\
---
# The repair budget, which is not a command.
auto_fix:
  review: 2

agent:
  lint: |
    printf 'the wrong section ran\\n' >> "$LINT_WRAPPER_MARKER"

commands:
  test: "not this one either"
  lint: |
    printf 'first\\n' >> "$LINT_WRAPPER_MARKER"

    # A comment inside the block, and a nested script quoting one of its own.
    bash -c '
      # The directory the checks resolve their relative paths against.
      printf "%s\\n" "$PWD" >> "$LINT_WRAPPER_MARKER"
    '
    printf 'last\\n' >> "$LINT_WRAPPER_MARKER"

ignore_patterns:
  - ".no-mistakes/**"
"""


def run(document, cwd):
    """Run the wrapper over `document` from `cwd`, answering (code, lines)."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace = Path(workspace).resolve()
        configuration = workspace / ".no-mistakes.yaml"
        configuration.write_text(document, encoding="utf-8")
        marker = workspace / "marker.txt"
        marker.touch()
        os.environ["LINT_WRAPPER_MARKER"] = str(marker)
        original_config, original_cwd = lint.CONFIG, Path.cwd()
        lint.CONFIG = configuration
        os.chdir(cwd)
        try:
            code = lint.main()
        finally:
            lint.CONFIG = original_config
            os.chdir(original_cwd)
            del os.environ["LINT_WRAPPER_MARKER"]
        return code, marker.read_text(encoding="utf-8").splitlines()


# Every line of the block, in order, run from the repository root rather than
# from wherever the wrapper was invoked - which is what the checks' own
# relative paths are written against. The first and last lines are what a
# reader that stopped early or started late would lose.
with tempfile.TemporaryDirectory() as elsewhere:
    code, lines = run(CONFIGURATION, elsewhere)
assert code == 0, f"a passing check reported {code}"
assert lines == ["first", str(REPO), "last"], lines

# The command's own code is the wrapper's, so a failing check fails the run
# that asked for it instead of being reported as a clean one.
code, lines = run(
    CONFIGURATION.replace(
        "    printf 'last\\n' >> \"$LINT_WRAPPER_MARKER\"\n",
        "    exit 3\n",
    ),
    REPO,
)
assert code == 3, f"a check that failed with 3 was reported as {code}"
assert lines == ["first", str(REPO)], lines

# A file with no checks configured says so and runs nothing, rather than
# reporting the clean run of an empty command.
code, lines = run("---\nauto_fix:\n  review: 2\n", REPO)
assert code == 1, f"a file with no commands.lint reported {code}"
assert lines == [], lines

# What the repository's own file yields today is a whole program: a block read
# short would stop inside the nested script it configures, and bash is the
# reader that decides that rather than this check.
configured = lint.lint_command(lint.CONFIG.read_text(encoding="utf-8"))
assert configured, f"{lint.CONFIG} has no commands.lint"
parsed = subprocess.run(
    ["bash", "-n", "-c", configured], capture_output=True, text=True
)
assert parsed.returncode == 0, (
    f"the configured checks did not survive being read: {parsed.stderr}"
)

# And is the same text a YAML parser reads out of it, where there is one. The
# wrapper has its own reader because the repository depends on no parser, so
# the two agreeing on the real file is worth having when a parser is around,
# and worth saying is missing when it is not.
try:
    import yaml
except ImportError:
    print("lint wrapper ok (no PyYAML here; the parser cross-check was skipped)")
else:
    document = yaml.safe_load(lint.CONFIG.read_text(encoding="utf-8"))
    assert configured == document["commands"]["lint"].rstrip("\n"), (
        "the wrapper and a YAML parser read different checks out of "
        f"{lint.CONFIG}"
    )
    print("lint wrapper ok")
