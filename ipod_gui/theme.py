"""The design's token sheet, as the one stylesheet the window loads."""

import hashlib

from gi.repository import Gdk, Gtk


# Every value here is from the design's token sheet. GTK has no CSS custom
# properties, so the light theme is expressed as overrides under .light rather
# than as a second set of variables.
PALETTE = {
    "dark": {
        "window": "#101011",
        "sidebar": "#0a0a0b",
        "content": "#131314",
        "card": "#1b1b1d",
        "line": "#26262a",
        "text": "#f4f2f0",
        "dim": "#8b8884",
        "accent": "#ff6b3d",
        "danger": "#c0341f",
        "ok": "#3ddc84",
        "warn": "#e0a33c",
        "queued": "#5a4034",
        "meter": "#26262a",
    },
    "light": {
        "window": "#ffffff",
        "sidebar": "#f1efec",
        "content": "#ffffff",
        "card": "#f7f5f2",
        "line": "#e3dfd9",
        "text": "#1a1917",
        "dim": "#6f6c67",
        "accent": "#e0521f",
        "danger": "#b02d18",
        "ok": "#1f9d55",
        "warn": "#a8721a",
        "queued": "#e8c3ad",
        "meter": "#e3dfd9",
    },
}

# Covers with no embedded art get one of these, chosen by hashing the album so
# a given record always looks the same. The design draws them as diagonal
# stripes, which reads as deliberate rather than as a missing image.
COVER_TINTS = [
    ("#3a2c26", "#2a201c"),
    ("#2b3340", "#1f2630"),
    ("#38302b", "#282320"),
    ("#2f3a34", "#232b27"),
    ("#3b2b33", "#2b1f26"),
    ("#333037", "#25232a"),
    ("#2a3436", "#1e2628"),
    ("#3a332a", "#2a251f"),
]

STYLE_CSS = """
window.shuffle,
window.shuffle > * {
  font-family: Manrope, Cantarell, sans-serif;
}
window.shuffle { background: #101011; color: #f4f2f0; }
window.shuffle.light { background: #ffffff; color: #1a1917; }

.sf-sidebar { background: #0a0a0b; border-right: 1px solid #1f1f22; }
window.shuffle.light .sf-sidebar {
  background: #f1efec; border-right: 1px solid #e3dfd9;
}
.sf-content { background: #131314; }
window.shuffle.light .sf-content { background: #ffffff; }

.sf-card {
  background: #1b1b1d; border: 1px solid #26262a; border-radius: 11px;
}
window.shuffle.light .sf-card { background: #f7f5f2; border-color: #e3dfd9; }

.sf-danger-card { background: #1a1416; border: 1px solid #4a2a24; border-radius: 11px; }
window.shuffle.light .sf-danger-card { background: #fdf3f1; border-color: #f0cec6; }

.sf-warn-card { background: #1e1a13; border: 1px solid #4a3d22; border-radius: 9px; }
window.shuffle.light .sf-warn-card { background: #fdf7ea; border-color: #e8d7a8; }

.sf-brand {
  background: #ff6b3d; color: #1a0d07; border-radius: 7px;
  font-weight: 800; font-size: 12px; min-width: 24px; min-height: 24px;
}
window.shuffle.light .sf-brand { background: #e0521f; color: #ffffff; }

.sf-nav-row { border-radius: 8px; padding: 9px 11px; font-size: 14px; }
.sf-nav-row:hover { background: alpha(#ffffff, 0.05); }
window.shuffle.light .sf-nav-row:hover { background: alpha(#000000, 0.04); }
.sf-nav-row.selected { background: #1f1f22; font-weight: 600; }
window.shuffle.light .sf-nav-row.selected { background: #e5e1db; font-weight: 600; }

.sf-heading { font-size: 26px; font-weight: 800; letter-spacing: -0.02em; }
.sf-album-title { font-size: 38px; font-weight: 800; letter-spacing: -0.03em; }
.sf-section-heading { font-size: 16px; font-weight: 700; }
.sf-row-title { font-size: 13.5px; font-weight: 600; }
.sf-body { font-size: 12.5px; color: #8b8884; }
.sf-caption { font-size: 11.5px; font-weight: 500; color: #8b8884; }
window.shuffle.light .sf-body,
window.shuffle.light .sf-caption { color: #6f6c67; }
.sf-section-label {
  font-family: "IBM Plex Mono", monospace; font-size: 10.5px;
  letter-spacing: 0.12em; color: #5c5955;
}
.sf-mono { font-family: "IBM Plex Mono", monospace; font-size: 11.5px; }
.sf-dim { color: #8b8884; }
window.shuffle.light .sf-dim { color: #6f6c67; }
.sf-accent { color: #ff6b3d; }
window.shuffle.light .sf-accent { color: #e0521f; }
.sf-alert { color: #ff8f6b; }
window.shuffle.light .sf-alert { color: #b02d18; }

.sf-pill {
  border-radius: 99px; padding: 4px 11px; font-size: 12px; font-weight: 500;
  background: #1b1b1d; border: 1px solid #2a2a2d; color: #c9c6c2;
  min-height: 0; box-shadow: none;
}
window.shuffle.light .sf-pill {
  background: #ffffff; border-color: #e3dfd9; color: #46433f;
}
.sf-pill.selected {
  background: #ff6b3d; color: #1a0d07; border-color: #ff6b3d; font-weight: 600;
}
window.shuffle.light .sf-pill.selected {
  background: #e0521f; color: #ffffff; border-color: #e0521f;
}

.sf-button {
  border-radius: 7px; font-size: 12px; font-weight: 600;
  background: transparent; border: 1px solid #3a3a40; color: #f4f2f0;
  box-shadow: none;
}
window.shuffle.light .sf-button { border-color: #d5d0c8; color: #1a1917; }
.sf-button:hover { background: alpha(#ffffff, 0.06); }
window.shuffle.light .sf-button:hover { background: alpha(#000000, 0.04); }
.sf-button.accent {
  background: #ff6b3d; border-color: #ff6b3d; color: #1a0d07; font-weight: 700;
}
window.shuffle.light .sf-button.accent {
  background: #e0521f; border-color: #e0521f; color: #ffffff;
}
.sf-button.accent:hover { background: #ff8355; }
.sf-button.danger { background: #c0341f; border-color: #c0341f; color: #ffffff; }
.sf-button.danger:hover { background: #d4402a; }

.sf-cover { border-radius: 9px; background: #26262a; }
.sf-cover.small { border-radius: 6px; }
.sf-cover.tiny { border-radius: 5px; }
.sf-cover-0 { background: repeating-linear-gradient(135deg,#3a2c26 0px,#3a2c26 10px,#2a201c 10px,#2a201c 20px); }
.sf-cover-1 { background: repeating-linear-gradient(135deg,#2b3340 0px,#2b3340 10px,#1f2630 10px,#1f2630 20px); }
.sf-cover-2 { background: repeating-linear-gradient(135deg,#38302b 0px,#38302b 10px,#282320 10px,#282320 20px); }
.sf-cover-3 { background: repeating-linear-gradient(135deg,#2f3a34 0px,#2f3a34 10px,#232b27 10px,#232b27 20px); }
.sf-cover-4 { background: repeating-linear-gradient(135deg,#3b2b33 0px,#3b2b33 10px,#2b1f26 10px,#2b1f26 20px); }
.sf-cover-5 { background: repeating-linear-gradient(135deg,#333037 0px,#333037 10px,#25232a 10px,#25232a 20px); }
.sf-cover-6 { background: repeating-linear-gradient(135deg,#2a3436 0px,#2a3436 10px,#1e2628 10px,#1e2628 20px); }
.sf-cover-7 { background: repeating-linear-gradient(135deg,#3a332a 0px,#3a332a 10px,#2a251f 10px,#2a251f 20px); }
window.shuffle.light .sf-cover-0 { background: repeating-linear-gradient(135deg,#e0cec4 0px,#e0cec4 10px,#d3bdb1 10px,#d3bdb1 20px); }
window.shuffle.light .sf-cover-1 { background: repeating-linear-gradient(135deg,#ccd6e2 0px,#ccd6e2 10px,#b9c7d8 10px,#b9c7d8 20px); }
window.shuffle.light .sf-cover-2 { background: repeating-linear-gradient(135deg,#ded4cb 0px,#ded4cb 10px,#d0c3b8 10px,#d0c3b8 20px); }
window.shuffle.light .sf-cover-3 { background: repeating-linear-gradient(135deg,#cddcd3 0px,#cddcd3 10px,#bacfc3 10px,#bacfc3 20px); }
window.shuffle.light .sf-cover-4 { background: repeating-linear-gradient(135deg,#e2ced6 0px,#e2ced6 10px,#d4bcc7 10px,#d4bcc7 20px); }
window.shuffle.light .sf-cover-5 { background: repeating-linear-gradient(135deg,#d7d3dc 0px,#d7d3dc 10px,#c7c2d1 10px,#c7c2d1 20px); }
window.shuffle.light .sf-cover-6 { background: repeating-linear-gradient(135deg,#ccd8d9 0px,#ccd8d9 10px,#b9cacc 10px,#b9cacc 20px); }
window.shuffle.light .sf-cover-7 { background: repeating-linear-gradient(135deg,#ded8cb 0px,#ded8cb 10px,#d0c8b7 10px,#d0c8b7 20px); }

.sf-dot { min-width: 8px; min-height: 8px; border-radius: 99px; }
.sf-dot.ipod { background: #ff6b3d; border: 1.5px solid #ff6b3d; }
window.shuffle.light .sf-dot.ipod { background: #e0521f; border-color: #e0521f; }
.sf-dot.library { background: transparent; border: 1.5px solid #8b8884; }
.sf-dot.preview { background: transparent; border: 1.5px dashed #a8a5a1; }

.sf-badge {
  background: alpha(#0a0a0b, 0.78); border-radius: 99px;
  padding: 3px 8px; font-size: 10.5px; font-weight: 600;
}
window.shuffle.light .sf-badge { background: alpha(#ffffff, 0.86); color: #1a1917; }

.sf-bottom-bar { background: #0a0a0b; border-top: 1px solid #1f1f22; }
window.shuffle.light .sf-bottom-bar {
  background: #f1efec; border-top: 1px solid #e3dfd9;
}
.sf-sync-bar { background: #151517; border-top: 1px solid #1f1f22; }
window.shuffle.light .sf-sync-bar {
  background: #f7f5f2; border-top: 1px solid #e3dfd9;
}

.sf-idle-art {
  border: 1px dashed #2f2f33; border-radius: 8px; color: #3a3a40;
}
window.shuffle.light .sf-idle-art { border-color: #d5d0c8; color: #b8b2aa; }

.sf-log {
  font-family: "IBM Plex Mono", monospace; font-size: 11px;
  background: transparent; color: #8b8884;
}
window.shuffle.light .sf-log { color: #6f6c67; }

/* The play affordance on a row's artwork. Transparent until the row is under
   the pointer, so a list at rest reads as covers rather than as a column of
   identical glyphs. Focus reveals it too, because a control that only exists
   while a mouse hovers it cannot be reached from the keyboard at all. */
.sf-cover-play {
  opacity: 0; transition: opacity 90ms ease-out;
  padding: 0; min-width: 0; min-height: 0;
  border: none; box-shadow: none; border-radius: 6px;
  background: alpha(#000000, 0.55); color: #ffffff;
}
.sf-tracks row:hover .sf-cover-play,
.sf-track-row:hover .sf-cover-play,
.sf-cover-play:hover,
.sf-cover-play:focus { opacity: 1; }
.sf-cover-play:disabled { color: alpha(#ffffff, 0.45); }

.sf-track-row { border-radius: 9px; padding: 6px 8px; }
.sf-track-row:hover { background: alpha(#ffffff, 0.04); }
window.shuffle.light .sf-track-row:hover { background: alpha(#000000, 0.03); }
.sf-track-row.previewed { opacity: 0.72; }
/* A YouTube result sits directly under a GtkColumnView of local results, and
   that view insets its last column by 4px less than a plain row's padding
   does. Matched here so the two lists' Add buttons share one right edge
   rather than nearly sharing one. */
.sf-track-row.sf-result-row { padding-right: 4px; }

/* Placeholder blocks holding the shape of a YouTube result while it loads.
   Flat rather than animated: a shimmer would be the only moving thing in a
   window that is otherwise still, and it would read as progress that the
   search has no way to actually report. */
.sf-skeleton { background: alpha(#ffffff, 0.06); border-radius: 5px; }
window.shuffle.light .sf-skeleton { background: alpha(#000000, 0.05); }

.sf-search { border-radius: 8px; font-size: 12.5px; min-height: 30px; }

.sf-new-tile {
  border: 1px dashed #33333a; border-radius: 10px;
  background: transparent; color: #8b8884; box-shadow: none;
}
window.shuffle.light .sf-new-tile { border-color: #d5d0c8; color: #6f6c67; }

.sf-tracks { background: transparent; }
.sf-tracks > listview > row,
.sf-tracks row { background: transparent; padding: 4px 2px; }
.sf-tracks row:hover { background: alpha(#ffffff, 0.04); border-radius: 9px; }
window.shuffle.light .sf-tracks row:hover { background: alpha(#000000, 0.03); }
.sf-tracks > header > button {
  background: transparent; border: none; box-shadow: none;
  font-size: 11.5px; font-weight: 600; color: #8b8884;
}
window.shuffle.light .sf-tracks > header > button { color: #6f6c67; }
.sf-tracks > header > button:hover { color: #f4f2f0; }
window.shuffle.light .sf-tracks > header > button:hover { color: #1a1917; }

.sf-meter { border-radius: 99px; background-color: #26262a; }
window.shuffle.light .sf-meter { background-color: #e3dfd9; }

.sf-flow { background: transparent; }
.sf-flow > flowboxchild { padding: 0; border-radius: 10px; }
.sf-flow > flowboxchild:selected { background: alpha(#ff6b3d, 0.14); }
"""


def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(STYLE_CSS, -1)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    return provider


def cover_class(seed):
    digest = hashlib.sha1((seed or "").encode("utf-8", "replace")).digest()
    return f"sf-cover-{digest[0] % len(COVER_TINTS)}"
