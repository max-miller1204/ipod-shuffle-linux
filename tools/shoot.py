#!/usr/bin/env python3
"""Render a deterministic Shuffle page from a demo-library fixture."""

import argparse
import os
import sys
import time
from pathlib import Path


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--page", choices=("library", "playlists", "settings"), required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--scale", type=int, choices=(1, 2), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


args = arguments()
if args.width < 660:
    sys.exit("--width must be at least the window minimum, 660")
root = args.fixture.resolve()
home = root / "home"
host_home = Path.home()
if not (root / ".demo-library").is_file():
    sys.exit(f"not a tools/demo-library.py fixture: {root}")

os.environ.update(
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / ".config"),
    XDG_CACHE_HOME=str(home / ".cache"),
    FAKE_IPOD_MOUNT=str(root / "MAX SHUFFLE"),
    GDK_SCALE=str(args.scale),
)
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
venv_python = host_home / "ipod-tools/venv/bin/python"
if venv_python.is_file():
    os.environ.setdefault("IPOD_VENV_PYTHON", str(venv_python))
os.environ["PATH"] = f"{repo / 'tests/bin'}:{os.environ['PATH']}"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gsk, Gtk  # noqa: E402

from ipod_gui import app as app_module  # noqa: E402


def pump(rounds=1):
    context = app_module.GLib.MainContext.default()
    for _ in range(rounds):
        while context.pending():
            context.iteration(False)


application = app_module.IpodApp()
if not application.register(None):
    sys.exit("could not register the application")
application.activate()
pump(20)
window = application.props.active_window
window.set_default_size(args.width, 760)
window.allocate(args.width, 760, -1, None)
application.activate_action("navigate", app_module.GLib.Variant("s", args.page))
for _ in range(600):
    pump()
    time.sleep(0.01)
    if not window._library_scan_running and window._device_snapshot_ready:
        break
else:
    sys.exit("the demo library and device did not settle")
window.allocate(args.width, 760, -1, None)
pump(30)
widget = window.toasts
widget.allocate(args.width, 760, -1, None)
snapshot = Gtk.Snapshot.new()
widget.do_snapshot(widget, snapshot)
node = snapshot.to_node()
if node is None:
    sys.exit("the window rendered no content")
renderer = Gsk.CairoRenderer.new()
renderer.realize(None)
args.output.parent.mkdir(parents=True, exist_ok=True)
texture = renderer.render_texture(node, None)
if not texture.save_to_png(str(args.output)):
    sys.exit(f"could not write {args.output}")
renderer.unrealize()
window.close()
application.quit()
print(args.output)
