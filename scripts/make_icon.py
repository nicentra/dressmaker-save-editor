"""Draw the app icon (a dress form on a rose tile) as PNG and Windows .ico.

uv run python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024
ROSE = (183, 92, 107, 255)
CREAM = (255, 246, 236, 255)
GOLD = (236, 190, 120, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((32, 32, SIZE - 32, SIZE - 32), radius=200, fill=ROSE)
    cx = SIZE // 2
    # hanger hook and bar
    d.arc((cx - 70, 110, cx + 70, 250), start=180, end=360, fill=GOLD, width=34)
    d.line((cx + 70, 180, cx + 70, 215), fill=GOLD, width=34)
    d.line((cx - 190, 300, cx, 215), fill=GOLD, width=34)
    d.line((cx + 190, 300, cx, 215), fill=GOLD, width=34)
    # bodice
    d.polygon(
        [(cx - 150, 290), (cx + 150, 290), (cx + 110, 520), (cx - 110, 520)],
        fill=CREAM,
    )
    # waist band
    d.rectangle((cx - 118, 500, cx + 118, 548), fill=GOLD)
    # flared skirt with a scalloped hem
    hem_y = 860
    d.polygon([(cx - 118, 540), (cx + 118, 540), (cx + 300, hem_y), (cx - 300, hem_y)], fill=CREAM)
    for i in range(6):
        x0 = cx - 300 + i * 100
        d.ellipse((x0, hem_y - 50, x0 + 100, hem_y + 50), fill=CREAM)
    return img


def main() -> None:
    img = draw()
    data = ROOT / "src" / "dressmaker_editor" / "data"
    img.resize((256, 256), Image.Resampling.LANCZOS).save(data / "icon.png")
    packaging = ROOT / "packaging"
    packaging.mkdir(exist_ok=True)
    img.save(
        packaging / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("wrote", data / "icon.png", "and", packaging / "icon.ico")


if __name__ == "__main__":
    main()
