#!/usr/bin/env bash
# Turns the walkthrough transcript into pictures, so the surface this change
# adds can be looked at rather than read out of a log.
#
# The only edit made to what was recorded is to the two absolute paths every
# line carries: this checkout becomes `.` and the session's temporary
# directory becomes `/tmp/session`, which is what a person running these
# commands would have typed. Nothing else is touched.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
SHORT="$HERE/.walkthrough-shortened.txt"

/usr/bin/python3 - "$HERE/01-cli-walkthrough.txt" "$ROOT" "$SHORT" <<'PY'
import re
import sys

source, root, out = sys.argv[1:4]
text = open(source, encoding="utf-8", errors="replace").read()
text = text.replace(root + "/", "./")
text = re.sub(r"/tmp/tmp\.[A-Za-z0-9]+", "/tmp/session", text)
open(out, "w", encoding="utf-8").write(text)
PY

render() {
    /usr/bin/python3 "$HERE/driver-render-png.py" "$SHORT" "$1" "$2" "$HERE/$3" "$4"
}

render '1. THE OLD WAY OUT' '3. THE WAYS ROUND IT' \
    06-refused-then-planned.png \
    'A caller with no terminal is refused, and asks for the plan instead'
render '3. THE WAYS ROUND IT' '5. THE SAME HANDSHAKE' \
    07-approval-is-bound-to-the-plan.png \
    'Approval belongs to one plan on one device, and to nothing else'
render '6. AND FOR A WIPE' '8. EVERY STATE ABOVE' \
    08-wipe-and-a-person-at-a-terminal.png \
    'A wipe through the handshake, and the same clear answered by hand'
render '8. EVERY STATE ABOVE' '(end of walkthrough)' \
    09-exit-codes.png \
    'Every state above, and the code it left with'

rm -f "$SHORT"
