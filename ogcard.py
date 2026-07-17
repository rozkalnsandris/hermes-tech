#!/usr/bin/env python3
"""Generate a branded 1200x630 Hermes Tech social card.

CLI contract intentionally stays compatible with publish.sh:
    ogcard.py <slug> <title>
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = Path(__file__).resolve().parent
STATIC = BASE / "site" / "static"
WORDMARK = STATIC / "brand" / "hermes-tech-wordmark-v15.png"
MARK = STATIC / "brand" / "hermes-tech-mark-v15.png"
OUT_DIR = STATIC / "og"

WIDTH, HEIGHT = 1200, 630
NAVY_TOP = (3, 16, 31)
NAVY_BOTTOM = (1, 8, 18)
TEXT = (244, 247, 251)
MUTED = (151, 176, 199)
ACCENTS = {
    "devops": (34, 199, 255),
    "ai": (145, 167, 255),
    "agents": (255, 180, 84),
}
LABELS = {
    "devops": "DEVOPS",
    "ai": "AI",
    "agents": "AI AGENTS",
}


def font_path(bold: bool) -> str:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("KĻŪDA: nav atrasts DejaVu/Liberation Sans fonts")


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(bold), size=size)


def category_from_slug(slug: str) -> str:
    for category in ("agents", "devops", "ai"):
        if slug.endswith("-" + category):
            return category
    return "devops"


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: int, max_lines: int = 4) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            break
    remaining_index = sum(len(line.split()) for line in lines)
    remaining = words[remaining_index:]
    if len(lines) == max_lines - 1 and remaining:
        final = " ".join(remaining)
        while final and draw.textlength(final + "…", font=font) > max_width:
            final = " ".join(final.split()[:-1])
        current = (final + "…") if final else "…"
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


def make_gradient() -> Image.Image:
    strip = Image.new("RGB", (1, HEIGHT), NAVY_TOP)
    pixels = strip.load()
    for y in range(HEIGHT):
        t = y / max(1, HEIGHT - 1)
        pixels[0, y] = tuple(
            round(NAVY_TOP[i] * (1 - t) + NAVY_BOTTOM[i] * t)
            for i in range(3)
        )
    return strip.resize((WIDTH, HEIGHT))


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("Lietošana: ogcard.py <slug> <title>")

    slug, title = sys.argv[1], sys.argv[2].strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        raise SystemExit("KĻŪDA: nederīgs OG slug")
    if not title:
        raise SystemExit("KĻŪDA: tukšs OG virsraksts")
    if not WORDMARK.is_file() or not MARK.is_file():
        raise SystemExit("KĻŪDA: trūkst Hermes Tech v15 zīmola failu")

    category = category_from_slug(slug)
    accent = ACCENTS[category]

    image = make_gradient()

    # Subtle cyan atmosphere, kept outside the text's contrast area.
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((740, -210, 1360, 410), fill=(*accent, 52))
    glow = glow.filter(ImageFilter.GaussianBlur(85))
    image = Image.alpha_composite(image.convert("RGBA"), glow)

    resampling = getattr(Image, "Resampling", Image)

    mark = Image.open(MARK).convert("RGBA")
    mark.thumbnail((520, 390), resampling.LANCZOS)
    alpha = mark.getchannel("A").point(lambda value: int(value * 0.13))
    mark.putalpha(alpha)
    image.alpha_composite(mark, (WIDTH - mark.width + 40, 92))

    wordmark = Image.open(WORDMARK).convert("RGBA")
    wordmark.thumbnail((430, 76), resampling.LANCZOS)
    image.alpha_composite(wordmark, (72, 58))

    draw = ImageDraw.Draw(image)
    chip_font = load_font(22, bold=True)
    title_font = load_font(55, bold=True)
    footer_font = load_font(22)
    small_font = load_font(19)

    chip = LABELS[category]
    chip_w = int(draw.textlength(chip, font=chip_font)) + 34
    draw.rounded_rectangle((72, 162, 72 + chip_w, 204), radius=12,
                           fill=(7, 42, 64, 255), outline=accent, width=2)
    draw.text((89, 169), chip, font=chip_font, fill=(228, 248, 255, 255))

    lines = wrap_text(draw, title, title_font, max_width=790, max_lines=4)
    y = 235
    for line in lines:
        draw.text((72, y), line, font=title_font, fill=TEXT, stroke_width=0)
        y += 68

    draw.line((72, 535, 1128, 535), fill=(37, 92, 124, 255), width=1)
    draw.text((72, 558), "AI-generated · hype-filtered · human-supervised",
              font=small_font, fill=MUTED)
    domain = "tech.rozkalns.net"
    domain_w = draw.textlength(domain, font=footer_font)
    draw.text((1128 - domain_w, 554), domain, font=footer_font, fill=accent)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"{slug}.png"
    image.convert("RGB").save(output, format="PNG", optimize=True)
    output.chmod(0o644)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
