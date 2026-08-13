# Destructive-operation authorization evidence

Terminal transcripts captured while driving `ipod-sync.sh`, `ipod-remove.sh` and
`ipod-wipe.sh` against synthetic iPods, exactly as a person or an automated
caller would.

- `end-user-authorization-walkthrough.txt` - the three end-user paths in one
  transcript: a person answering the prompt at a real terminal (succeeds, no
  token), the same person whose iPod is swapped while the prompt is on screen
  (exit 5, replacement untouched), and an automated caller whose `--yes` is
  refused until it returns the plan's `confirmationToken` (dry-run plan,
  wrong `--expect-device`, then the approved run with NDJSON progress).
- `pty-before-fix/` - the same swap and `--yes` runs against the base commit
  `4c20cc9`: the wipe ran to completion on the replacement device (exit 0), and
  `--yes` at a terminal wiped the iPod with no plan token.
- `pty-after-fix/` - `tests/operation-authorization-pty.py` output on the
  change: every script refuses both the swap and `--yes` without a token.
- `mid-copy-device-swap.txt` - the iPod replaced mid-copy during a 400-track
  sync: the run stops after 2 tracks with exit 5 and the progress stream still
  ends with a `result` event.
