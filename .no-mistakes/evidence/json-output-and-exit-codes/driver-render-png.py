#!/usr/bin/env python3
"""Renders an ANSI terminal transcript to a PNG, so the CLI surface this
change adds can be seen rather than described."""

import re
import sys

from PIL import Image, ImageDraw, ImageFont

SGR = re.compile(r"\x1b\[([0-9;]*)m")
COLORS = {
    0: "#d4d4d4",
    30: "#4e4e4e", 31: "#e06c6c", 32: "#79c07a", 33: "#d7bb63",
    34: "#6a9fd8", 35: "#c07ac0", 36: "#4ec9c9", 37: "#d4d4d4",
    90: "#8a8a8a", 91: "#e06c6c", 92: "#79c07a", 93: "#d7bb63",
    94: "#6a9fd8", 95: "#c07ac0", 96: "#4ec9c9", 97: "#ffffff",
}
BACKGROUND = "#161616"
PROMPT = "#8fd4ff"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SIZE = 15
PAD = 18


def spans(line):
    """The line as (text, colour, bold) runs."""
    out = []
    colour, bold, index = COLORS[0], False, 0
    for match in SGR.finditer(line):
        if match.start() > index:
            out.append((line[index:match.start()], colour, bold))
        for code in (match.group(1) or "0").split(";"):
            code = int(code or 0)
            if code == 0:
                colour, bold = COLORS[0], False
            elif code == 1:
                bold = True
            elif code in COLORS:
                colour = COLORS[code]
        index = match.end()
    if index < len(line):
        out.append((line[index:], colour, bold))
    return out


def render(lines, path, title):
    font = ImageFont.truetype(FONT_PATH, SIZE)
    bold_font = ImageFont.truetype(FONT_BOLD, SIZE)
    advance = font.getlength("M")
    height = SIZE + 6
    plain = [SGR.sub("", line) for line in lines]
    width = int(max(len(line) for line in plain + [title]) * advance) + PAD * 2
    image = Image.new(
        "RGB", (width, height * (len(lines) + 3) + PAD * 2), BACKGROUND
    )
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, height + PAD // 2], fill="#242424")
    draw.text((PAD, PAD // 2), title, font=bold_font, fill="#c8c8c8")
    for row, line in enumerate(lines):
        y = PAD + height * (row + 2)
        x = PAD
        for text, colour, bold in spans(line):
            if text.startswith("$ ") and x == PAD:
                draw.text((x, y), "$", font=bold_font, fill=PROMPT)
                x += advance * 2
                text = text[2:]
            draw.text((x, y), text, font=bold_font if bold else font, fill=colour)
            x += advance * len(text)
    image.save(path)
    print(f"wrote {path} ({image.width}x{image.height})")


def main():
    transcript = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    lines = transcript.splitlines()
    start, end, out, title = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    first = next(i for i, line in enumerate(lines) if start in SGR.sub("", line))
    last = next(
        i for i, line in enumerate(lines) if i > first and end in SGR.sub("", line)
    )
    chunk = [line.rstrip() for line in lines[first:last]]
    while chunk and not chunk[-1]:
        chunk.pop()
    render(chunk, out, title)


if __name__ == "__main__":
    main()
