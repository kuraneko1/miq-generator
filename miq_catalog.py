"""Numbered contact sheets of every layout (the ``miq台帳`` reply).

from miq_catalog import pages
for title, png in pages():
    ...
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

import miq
from miq_fonts import FAMILIES, font_at
from miq_images import encode_png

ROOT = Path(__file__).resolve().parent
AVATAR = ROOT / "demo-avatar.png"
SAMPLE = "こいつマジで何言ってんの？笑"
DATE = datetime(2026, 4, 1, 9, 41)
PAPER, INK, SUB = (238, 236, 231), (32, 32, 36), (110, 108, 104)
# Thumbnail cell (image area) per orientation, and columns per row.
CELLS = {
    "landscape": ((320, 180), 5),
    "portrait": ((180, 320), 7),
    "square": ((240, 240), 7),
}
HELP = (
    "miq12 のように番号で構図を指定 ／ miq 縦・miq 正方形 で比率 ／ "
    "miq 切り抜き はキャラを前に出す"
)


def _font(px, bold=False):
    item = next((face for face in FAMILIES.values() if face.installed()), None)
    if item is None:
        raise ValueError(
            "標準フォントがありません。python scripts/setup_fonts.py を実行してください。"
        )
    return font_at(item.path, px, item.name if bold else item.handle)


def thumbnails(orientation):
    """``(number, label, image)`` for every layout of one orientation."""

    specs = sorted(
        (s for s in miq.REGISTRY.values() if orientation in s.orientations),
        key=lambda s: s.number,
    )
    tiles = []
    for spec in specs:
        canvas, _, _ = miq.compose(
            AVATAR,
            SAMPLE,
            "高橋 直人",
            "naoto_notes",
            layout=spec.name,
            orientation=orientation,
            date=DATE,
            seed=1,
        )
        label = spec.label + ("（miq 切り抜き）" if spec.hidden else "")
        tiles.append((f"#{spec.number}", label, canvas))
    return tiles


def sheet(sections, title):
    """Lay out ``[(heading, orientation, tiles)]`` as one page."""

    pad, label, heading = 16, 30, 44
    widths = []
    for _, orientation, tiles in sections:
        (cw, _), columns = CELLS[orientation]
        widths.append(min(columns, len(tiles)) * (cw + pad) + pad)
    width = max(max(widths, default=0), 900)
    height = 92
    for _, orientation, tiles in sections:
        (_, ch), columns = CELLS[orientation]
        rows = -(-len(tiles) // columns)
        height += heading + rows * (ch + label + pad)
    page = Image.new("RGB", (width, height + pad), PAPER)
    draw = ImageDraw.Draw(page)
    draw.text((pad, 14), title, font=_font(32, True), fill=INK)
    draw.text((pad, 58), HELP, font=_font(18), fill=SUB)
    y = 92
    for name, orientation, tiles in sections:
        (cw, ch), columns = CELLS[orientation]
        draw.text((pad, y + 8), name, font=_font(24, True), fill=INK)
        y += heading
        for i, (number, text, image) in enumerate(tiles):
            x = pad + (i % columns) * (cw + pad)
            top = y + (i // columns) * (ch + label + pad)
            thumb = ImageOps.contain(
                image.convert("RGB"), (cw, ch), Image.Resampling.LANCZOS
            )
            page.paste(thumb, (x + (cw - thumb.width) // 2, top))
            draw.text((x, top + ch + 4), number, font=_font(20, True), fill=INK)
            offset = draw.textlength(number + " ", font=_font(20, True))
            draw.text((x + offset, top + ch + 6), text, font=_font(17), fill=SUB)
        y += -(-len(tiles) // columns) * (ch + label + pad)
    return page


def pages():
    """``[(filename, png_bytes)]``: landscape layouts, then portrait/square/dialogue."""

    result = [
        (
            "miq-layouts-1.png",
            sheet(
                [("横（16:9） miq / miq12", "landscape", thumbnails("landscape"))],
                "miq 構図台帳 1/2",
            ),
        )
    ]
    sections = [
        ("縦（9:16） miq 縦", "portrait", thumbnails("portrait")),
        ("正方形 miq 正方形", "square", thumbnails("square")),
    ]
    try:
        from miq_dialogue import thumbnails as dialogue_thumbnails

        sections.append(
            (
                "掛け合い miq vs / miq love / miq talk",
                "landscape",
                dialogue_thumbnails(),
            )
        )
    except ImportError:
        pass
    result.append(("miq-layouts-2.png", sheet(sections, "miq 構図台帳 2/2")))
    return [(name, encode_png(page, 6)) for name, page in result]


def main() -> int:
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    for name, png in pages():
        (out / name).write_bytes(png)
        print(out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
