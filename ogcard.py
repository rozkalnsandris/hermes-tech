#!/usr/bin/env python3
"""Hermes Tech — OG social card ģenerators (1200x630, PIL, bez AI attēliem).
Lietošana: ogcard.py <izvades-nosaukums> "<virsraksts>"
Saglabā: ~/hermes-tech/site/static/og/<nosaukums>.png
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
OUT_DIR = Path.home() / "hermes-tech" / "site" / "static" / "og"

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
FONT_REG = FONT_DIR / "DejaVuSans.ttf"

TEXT = (243, 244, 246)
MUTED = (156, 163, 175)
ACCENT = (96, 165, 250)


def vertical_gradient(top, bottom):
    img = Image.new("RGB", (W, H))
    for y in range(H):
        t = y / H
        img.paste(
            tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
            (0, y, W, y + 1),
        )
    return img


def wrap(draw, text, font, max_width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def main() -> int:
    if len(sys.argv) < 3:
        print("Lietošana: ogcard.py <nosaukums> \"<virsraksts>\"")
        return 1
    name, title = sys.argv[1], sys.argv[2]

    img = vertical_gradient((58, 63, 75), (30, 33, 40))
    d = ImageDraw.Draw(img)

    brand_f = ImageFont.truetype(str(FONT_BOLD), 44)
    title_f = ImageFont.truetype(str(FONT_BOLD), 62)
    small_f = ImageFont.truetype(str(FONT_REG), 28)

    # Brands augšā
    d.text((70, 60), "HERMES", font=brand_f, fill=TEXT)
    tw = d.textlength("HERMES ", font=brand_f)
    d.text((70 + tw, 60), "TECH", font=brand_f, fill=ACCENT)
    d.text((70, 116), "AI Platform Engineer", font=small_f, fill=MUTED)

    # Akcenta līnija
    d.rectangle([70, 170, 190, 176], fill=ACCENT)

    # Virsraksts (max 4 rindas)
    lines = wrap(d, title, title_f, W - 140)[:4]
    y = 230
    for line in lines:
        d.text((70, y), line, font=title_f, fill=TEXT)
        y += 78

    # Kājene
    d.text((70, H - 80),
           "The technology is not the story. The engineering behind it is.",
           font=small_f, fill=MUTED)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.png"
    img.save(out, "PNG")
    print(f"OG karte: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
